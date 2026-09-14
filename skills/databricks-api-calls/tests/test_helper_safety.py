"""Synthetic contract tests for generated Databricks API helper safety."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_databricks_context as context_helper  # noqa: E402
import render_api_script as renderer  # noqa: E402
import safe_databricks_diagnostics as diagnostics  # noqa: E402


SQL_LITERAL = "SELECT 'sql-literal-should-never-leak'"
MESSAGE_CONTENT = "message-content-should-never-leak"
SECRET_VALUE = "Bearer secret-token-should-never-leak"
EXTERNAL_LINK = "https://example.invalid/results?signature=should-never-leak"


def load_generated(source: str) -> types.ModuleType:
    """Compile a rendered helper in an isolated module namespace."""
    module = types.ModuleType("generated_helper")
    exec(compile(source, "generated_helper.py", "exec"), module.__dict__)
    return module


def successful_process(payload: dict[str, object]) -> dict[str, object]:
    """Return one bounded, successful synthetic CLI result."""
    return {
        "returncode": 0,
        "stdout": json.dumps(payload).encode("utf-8"),
        "stderr": b"",
        "timed_out": False,
        "output_limited": False,
    }


class HelperSafetyTests(unittest.TestCase):
    """Exercise helpers only through synthetic files, processes, and API responses."""

    def test_text_runtime_inputs_never_enter_source_stdout_or_captured_argv(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            body_path = temp_dir / "request.json"
            statement_path = temp_dir / "query.sql"
            body_path.write_text(
                json.dumps(
                    {
                        "client_secret": "client-secret-value",
                        "messages": [{"content": MESSAGE_CONTENT}],
                    }
                ),
                encoding="utf-8",
            )
            statement_path.write_text(SQL_LITERAL, encoding="utf-8")
            post_source = renderer.render_post_script(
                "EXPLICIT_PROFILE",
                "/api/2.0/mlflow/runs/search",
                body_file=str(body_path),
            )
            sql_source = renderer.render_sql_script(
                "EXPLICIT_PROFILE", "warehouse-1", str(statement_path), "0s", 5.0
            )
            for forbidden in (
                SQL_LITERAL,
                MESSAGE_CONTENT,
                SECRET_VALUE,
                EXTERNAL_LINK,
                "client-secret-value",
            ):
                self.assertNotIn(forbidden, post_source)
                self.assertNotIn(forbidden, sql_source)

            post = load_generated(post_source)
            sql = load_generated(sql_source)
            post_commands: list[list[str]] = []
            sql_commands: list[list[str]] = []

            def post_process(command: list[str], timeout: float) -> dict[str, object]:
                post_commands.append(command)
                return successful_process(
                    {"message": MESSAGE_CONTENT, "external_link": EXTERNAL_LINK}
                )

            def sql_process(command: list[str], timeout: float) -> dict[str, object]:
                sql_commands.append(command)
                return successful_process(
                    {
                        "statement_id": "stmt-1",
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"truncated": False},
                        "result": {},
                    }
                )

            post.run_bounded_command = post_process
            sql.run_bounded_command = sql_process
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(post.main(), 0)
                self.assertEqual(sql.run_sql(5.0), 0)
            for command in (*post_commands, *sql_commands):
                joined = " ".join(command)
                self.assertIn("--json", command)
                self.assertTrue(command[-1].startswith("@"))
                for forbidden in (
                    SQL_LITERAL,
                    MESSAGE_CONTENT,
                    "client-secret-value",
                    SECRET_VALUE,
                ):
                    self.assertNotIn(forbidden, joined)
            for forbidden in (
                SQL_LITERAL,
                MESSAGE_CONTENT,
                SECRET_VALUE,
                EXTERNAL_LINK,
            ):
                self.assertNotIn(forbidden, stdout.getvalue())
            self.assertFalse(Path(post_commands[0][-1][1:]).exists())
            self.assertFalse(Path(sql_commands[0][-1][1:]).exists())

    def test_inline_text_and_known_secret_bodies_are_refused(self) -> None:
        for body in (
            {"client_secret": "client-secret-value"},
            {"messages": [{"content": MESSAGE_CONTENT}]},
            {"filter": "FINISHED"},
        ):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(argparse.ArgumentTypeError, "use --body-file"),
            ):
                renderer.parse_json_body(json.dumps(body))
        body = renderer.parse_json_body('{"max_results": 5, "include_deleted": false}')
        self.assertIn(
            "max_results",
            renderer.render_post_script(
                "EXPLICIT_PROFILE", "/api/2.0/mlflow/runs/search", body=body
            ),
        )

    def test_generated_cli_failure_and_invalid_json_never_emit_raw_process_output(
        self,
    ) -> None:
        module = load_generated(
            renderer.render_get_script("EXPLICIT_PROFILE", "/api/2.0/clusters/list")
        )
        module.run_bounded_command = lambda *args: {
            "returncode": 7,
            "stdout": f"rows={MESSAGE_CONTENT}".encode(),
            "stderr": f"{SECRET_VALUE}; {EXTERNAL_LINK}".encode(),
            "timed_out": False,
            "output_limited": False,
        }
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as failure,
        ):
            module.cli_json("api", "get", "/api/2.0/clusters/list")
        self.assertEqual(failure.exception.code, 7)
        output = stderr.getvalue()
        self.assertIn("databricks_cli_failed", output)
        for forbidden in (MESSAGE_CONTENT, SECRET_VALUE, EXTERNAL_LINK):
            self.assertNotIn(forbidden, output)

        module.run_bounded_command = lambda *args: {
            "returncode": 0,
            "stdout": SECRET_VALUE.encode(),
            "stderr": b"",
            "timed_out": False,
            "output_limited": False,
        }
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            module.cli_json("api", "get", "/api/2.0/clusters/list")
        self.assertIn("databricks_cli_invalid_json", stderr.getvalue())
        self.assertNotIn(SECRET_VALUE, stderr.getvalue())

    def test_sql_succeeded_requires_explicit_complete_inline_result(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            statement_path = Path(temp_dir_name) / "query.sql"
            statement_path.write_text(SQL_LITERAL, encoding="utf-8")
            incomplete = (
                {},
                {"manifest": "malformed", "result": {}},
                {"manifest": {"truncated": True}, "result": {}},
                {"manifest": {"truncated": False}},
                {
                    "manifest": {"truncated": False},
                    "result": {"next_chunk_index": None, "data_array": [["row"]]},
                },
                {
                    "manifest": {"truncated": False},
                    "result": {"continuation_token": ""},
                },
                {"manifest": {"truncated": False}, "result": {"external_links": []}},
                {"manifest": {"truncated": False}, "result": {}, "external_link": None},
            )
            for response_tail in incomplete:
                with self.subTest(response_tail=response_tail):
                    module = load_generated(
                        renderer.render_sql_script(
                            "EXPLICIT_PROFILE",
                            "warehouse-1",
                            str(statement_path),
                            "0s",
                            5.0,
                        )
                    )
                    module.run_bounded_command = (
                        lambda *args, tail=response_tail: successful_process(
                            {
                                "statement_id": "stmt-2",
                                "status": {"state": "SUCCEEDED"},
                                **tail,
                            }
                        )
                    )
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(module.run_sql(5.0), 1)
                    self.assertIn("sql_result_incomplete", stderr.getvalue())
                    self.assertNotIn("row", stderr.getvalue())

    def test_sql_deadline_is_monotonic_bounded_and_never_cancels(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            statement_path = Path(temp_dir_name) / "query.sql"
            statement_path.write_text(SQL_LITERAL, encoding="utf-8")
            source = renderer.render_sql_script(
                "EXPLICIT_PROFILE", "warehouse-1", str(statement_path), "0s", 1.0
            )
            self.assertNotIn("/cancel", source)
            module = load_generated(source)
            clock = types.SimpleNamespace(now=0.0)
            clock.monotonic = lambda: clock.now
            clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
            module.time = clock
            calls: list[list[str]] = []

            def pending(command: list[str], timeout: float) -> dict[str, object]:
                calls.append(command)
                return successful_process(
                    {"statement_id": "stmt-3", "status": {"state": "PENDING"}}
                )

            module.run_bounded_command = pending
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(module.run_sql(1.0), 1)
            self.assertEqual(len(calls), 1)
            self.assertIn("sql_poll_deadline_exceeded", stderr.getvalue())
            self.assertIn("stmt-3", stderr.getvalue())
            self.assertNotIn(SQL_LITERAL, stderr.getvalue())

    def test_renderer_and_generated_deadlines_reject_bool_nan_and_subsecond_values(
        self,
    ) -> None:
        for value in (False, 0, 0.5, "NaN", "inf", 3600.1):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                renderer.poll_deadline_arg(value)
        self.assertEqual(renderer.poll_deadline_arg("1"), 1.0)
        self.assertEqual(renderer.poll_deadline_arg("1.5"), 1.5)
        self.assertEqual(renderer.poll_deadline_arg("3600"), 3600.0)
        module = load_generated(
            renderer.render_sql_script(
                "EXPLICIT_PROFILE", "warehouse-1", "C:/private/query.sql", "0s", 1.0
            )
        )
        for value in (False, 0, 0.5, float("nan"), 3600.1):
            with self.subTest(generated=value):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.run_sql(value), 2)

    def test_private_file_boundary_checks_before_open_and_bounds_reads(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            path = Path(temp_dir_name) / "large.json"
            path.write_bytes(b"x" * 17)
            with (
                mock.patch.object(diagnostics, "MAX_RUNTIME_FILE_BYTES", 16),
                mock.patch.object(
                    diagnostics.os, "open", wraps=diagnostics.os.open
                ) as open_file,
            ):
                with self.assertRaises(diagnostics.RuntimeBoundaryError):
                    diagnostics.read_private_runtime_file(path, "request_body")
            self.assertNotIn(
                str(path),
                [str(call.args[0]) for call in open_file.call_args_list],
            )

    def test_bounded_process_and_depth_limited_redaction_do_not_expand_untrusted_data(
        self,
    ) -> None:
        result = diagnostics.run_bounded_command(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"],
            timeout_seconds=5,
            stdout_limit=1024,
            stderr_limit=1024,
        )
        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout), 1024)
        nested: object = "leaf"
        for _ in range(100):
            nested = [nested]
        redacted = diagnostics.redact_value(nested)
        self.assertIn("depth_budget", json.dumps(redacted))
        many_nodes = diagnostics.redact_value(list(range(10000)))
        self.assertLessEqual(len(many_nodes), diagnostics.MAX_REDACTION_NODES + 1)
        self.assertIn("node_budget", json.dumps(many_nodes))

    def test_context_helper_never_returns_malformed_failed_or_excessive_raw_output(
        self,
    ) -> None:
        self.assertEqual(
            context_helper.parse_current_user(f"{SECRET_VALUE} {MESSAGE_CONTENT}"),
            {"valid": False},
        )
        fake = diagnostics.BoundedProcessResult(
            returncode=9,
            stdout=MESSAGE_CONTENT.encode(),
            stderr=f"{SECRET_VALUE} {EXTERNAL_LINK}".encode(),
            timed_out=False,
            output_limited=False,
        )
        with mock.patch.object(
            context_helper, "run_bounded_command", return_value=fake
        ):
            report = context_helper.run_command(["databricks", "current-user"])
        serialized = json.dumps(report)
        for forbidden in (MESSAGE_CONTENT, SECRET_VALUE, EXTERNAL_LINK):
            self.assertNotIn(forbidden, serialized)
        oversized = diagnostics.BoundedProcessResult(0, b"x", b"", False, True)
        with mock.patch.object(
            context_helper, "run_bounded_command", return_value=oversized
        ):
            self.assertFalse(context_helper.run_command(["databricks"])["ok"])

    def test_effective_context_receipt_uses_exact_profile_and_effective_host(
        self,
    ) -> None:
        profile_name = "TAP"
        auth_describe = diagnostics.BoundedProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "username": "user@example.invalid",
                    "details": {
                        "auth_type": "pat",
                        "host": "https://effective-workspace.invalid/path",
                        "configuration": {"profile": {"value": profile_name}},
                    },
                }
            ).encode("utf-8"),
            stderr=b"",
            timed_out=False,
            output_limited=False,
        )
        current_user = diagnostics.BoundedProcessResult(
            returncode=0,
            stdout=b'{"userName":"user@example.invalid"}',
            stderr=b"",
            timed_out=False,
            output_limited=False,
        )
        with mock.patch.object(
            context_helper,
            "run_bounded_command",
            side_effect=(auth_describe, current_user),
        ) as command:
            report = context_helper.collect_profile_context(
                profile_name,
                {"host": "https://configured-workspace.invalid"},
                databricks_found=True,
            )
        self.assertEqual(
            report["effective_context"],
            {
                "version": 1,
                "ok": True,
                "profile": profile_name,
                "host": "https://effective-workspace.invalid",
            },
        )
        self.assertEqual(report["host"], "https://configured-workspace.invalid")
        self.assertEqual(
            command.call_args_list[0].args[0],
            [
                "databricks",
                "auth",
                "describe",
                "--profile",
                profile_name,
                "-o",
                "json",
            ],
        )
        self.assertNotIn(SECRET_VALUE, json.dumps(report))

    def test_effective_context_receipt_rejects_invalid_or_unverified_auth_describe(
        self,
    ) -> None:
        profile_name = "TAP"
        invalid_payloads = (
            b"not json",
            b"[]",
            (
                b'{"status":"success","details":{"host":"https://workspace.invalid",'
                b'"configuration":{"profile":{"value":"TAP","value":"TAP"}}}}'
            ),
            json.dumps(
                {
                    "status": "success",
                    "details": {
                        "host": "https://workspace.invalid",
                        "configuration": {"profile": {"value": profile_name}},
                    },
                    "padding": "x" * context_helper.MAX_AUTH_DESCRIBE_JSON_BYTES,
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "status": "success",
                    "details": {
                        "host": "https://workspace.invalid",
                        "configuration": {"profile": {"value": "OTHER"}},
                    },
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "status": "success",
                    "details": {"configuration": {"profile": {"value": profile_name}}},
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "status": "error",
                    "details": {
                        "host": "https://workspace.invalid",
                        "configuration": {"profile": {"value": profile_name}},
                    },
                }
            ).encode("utf-8"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload_bytes=len(payload)):
                result = diagnostics.BoundedProcessResult(
                    0, payload, f"{SECRET_VALUE} {EXTERNAL_LINK}".encode(), False, False
                )
                with mock.patch.object(
                    context_helper, "run_bounded_command", return_value=result
                ):
                    receipt = context_helper.effective_context_receipt(profile_name)
                self.assertEqual(
                    receipt,
                    {
                        "version": 1,
                        "ok": False,
                        "profile": profile_name,
                        "error": "unverified effective context",
                    },
                )
                serialized = json.dumps(receipt)
                self.assertNotIn(SECRET_VALUE, serialized)
                self.assertNotIn(EXTERNAL_LINK, serialized)
        failed = diagnostics.BoundedProcessResult(
            7, MESSAGE_CONTENT.encode(), SECRET_VALUE.encode(), False, False
        )
        with mock.patch.object(
            context_helper, "run_bounded_command", return_value=failed
        ):
            receipt = context_helper.effective_context_receipt(profile_name)
        self.assertFalse(receipt["ok"])
        self.assertNotIn(MESSAGE_CONTENT, json.dumps(receipt))
        self.assertNotIn(SECRET_VALUE, json.dumps(receipt))

    def test_effective_context_receipt_accepts_80_char_profile_and_rejects_81(
        self,
    ) -> None:
        accepted_profile = "P" * 80
        describe = diagnostics.BoundedProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "details": {
                        "host": "https://workspace.invalid",
                        "configuration": {"profile": {"value": accepted_profile}},
                    },
                }
            ).encode("utf-8"),
            stderr=b"",
            timed_out=False,
            output_limited=False,
        )
        current_user = diagnostics.BoundedProcessResult(
            returncode=0,
            stdout=b'{"userName":"user@example.invalid"}',
            stderr=b"",
            timed_out=False,
            output_limited=False,
        )
        with mock.patch.object(
            context_helper,
            "run_bounded_command",
            side_effect=(describe, current_user),
        ) as command:
            report = context_helper.collect_profile_context(
                accepted_profile, {}, databricks_found=True
            )
            self.assertEqual(
                report["effective_context"],
                {
                    "version": 1,
                    "ok": True,
                    "profile": accepted_profile,
                    "host": "https://workspace.invalid",
                },
            )
        self.assertEqual(command.call_args.args[0][4], accepted_profile)
        self.assertEqual(command.call_count, 2)
        self.assertEqual(
            command.call_args_list[1].args[0],
            [
                "databricks",
                "current-user",
                "me",
                "--profile",
                accepted_profile,
                "-o",
                "json",
            ],
        )
        rejected_profile = "P" * 81
        with (
            mock.patch.object(
                context_helper,
                "effective_context_receipt",
                side_effect=AssertionError("auth describe must not run"),
            ) as describe_command,
            mock.patch.object(
                context_helper,
                "run_command",
                side_effect=AssertionError("current-user must not run"),
            ) as current_user_command,
        ):
            self.assertEqual(
                context_helper.collect_profile_context(
                    rejected_profile, {}, databricks_found=True
                ),
                {
                    "profile": "P" * 80 + "<truncated>",
                    "configured": False,
                    "host": None,
                    "credential_source": "unknown",
                    "config_has_token": False,
                    "config_has_client_id": False,
                    "config_has_client_secret": False,
                    "config_has_auth_type": False,
                    "effective_context": {
                        "version": 1,
                        "ok": False,
                        "profile": "P" * 80 + "<truncated>",
                        "error": "unsupported profile name",
                    },
                    "current_user": {
                        "ok": False,
                        "error": "unsupported profile name",
                    },
                },
            )
        describe_command.assert_not_called()
        current_user_command.assert_not_called()

    def test_context_helper_never_selects_default_profile_implicitly(self) -> None:
        with (
            mock.patch.object(
                context_helper.shutil, "which", return_value="databricks"
            ),
            mock.patch.object(
                context_helper,
                "read_profile_config",
                return_value=({"DEFAULT": {"host": "https://default.invalid"}}, True),
            ),
            mock.patch.object(context_helper, "effective_context_receipt") as receipt,
        ):
            report = context_helper.build_report([])
        self.assertEqual(report["profiles"], [])
        receipt.assert_not_called()

    def test_git_private_boundary_refuses_tracked_or_unignored_paths_and_ignores_hostile_env(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir_name:
            repo = Path(temp_dir_name) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "-C", str(repo), "init"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tracked = repo / "tracked-secret.json"
            tracked.write_text("{}", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", tracked.name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self.assertRaises(diagnostics.RuntimeBoundaryError):
                diagnostics.assert_private_git_target(tracked)
            unignored = repo / "unignored-secret.json"
            with self.assertRaises(diagnostics.RuntimeBoundaryError):
                diagnostics.assert_private_git_target(unignored)
            config_path = repo / "tracked-config"
            config_path.write_text(
                "[SAFE]\nhost = https://workspace.invalid\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", config_path.name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(
                context_helper.read_profile_config(config_path), ({}, False)
            )
            (repo / ".gitignore").write_text("private/\n", encoding="utf-8")
            private = repo / "private" / "safe.py"
            private.parent.mkdir()
            with mock.patch.dict(
                os.environ,
                {"GIT_DIR": str(repo / "missing"), "GIT_WORK_TREE": str(repo)},
                clear=False,
            ):
                self.assertEqual(
                    diagnostics.assert_private_git_target(private), private.absolute()
                )
            renderer.write_script(private, "print('safe')\n")
            self.assertTrue(private.exists())
            with self.assertRaises(diagnostics.RuntimeBoundaryError):
                renderer.write_script(private, "print('replacement')\n")

    def test_parent_symlink_or_reparse_is_rejected_before_git_classification(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            repo = temp_dir / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "-C", str(repo), "init"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tracked = repo / "tracked-secret.json"
            tracked.write_text("{}", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", tracked.name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            alias = temp_dir / "ignored-looking-alias"
            try:
                os.symlink(repo, alias, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable on this Windows host")
            with mock.patch.object(diagnostics, "_run_git") as run_git:
                with self.assertRaises(diagnostics.RuntimeBoundaryError):
                    diagnostics.assert_private_git_target(alias / tracked.name)
            run_git.assert_not_called()

    def test_reparse_rejection_stops_git_and_open_probes_in_central_and_generated_support(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir_name:
            runtime_file = Path(temp_dir_name) / "private.json"
            runtime_file.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    diagnostics,
                    "assert_no_reparse_components",
                    side_effect=diagnostics.RuntimeBoundaryError("blocked"),
                ),
                mock.patch.object(diagnostics, "_run_git") as run_git,
                mock.patch.object(
                    diagnostics.os, "open", wraps=diagnostics.os.open
                ) as open_file,
            ):
                with self.assertRaises(diagnostics.RuntimeBoundaryError):
                    diagnostics.read_private_runtime_file(runtime_file, "request_body")
            run_git.assert_not_called()
            self.assertNotIn(
                str(runtime_file),
                [str(call.args[0]) for call in open_file.call_args_list],
            )

            generated = load_generated(
                renderer.render_get_script("EXPLICIT_PROFILE", "/api/2.0/clusters/list")
            )
            with (
                mock.patch.object(
                    generated,
                    "assert_no_reparse_components",
                    side_effect=generated.RuntimeBoundaryError("blocked"),
                ),
                mock.patch.object(generated, "_run_git") as run_git,
            ):
                with self.assertRaises(generated.RuntimeBoundaryError):
                    generated.assert_private_git_target(runtime_file)
            run_git.assert_not_called()
        synthetic_reparse = types.SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=diagnostics.FILE_ATTRIBUTE_REPARSE_POINT,
        )
        self.assertTrue(diagnostics._is_symlink_or_reparse(synthetic_reparse))

    def test_rendering_preserves_quoted_arguments_as_data_without_shell_execution(
        self,
    ) -> None:
        source = renderer.render_get_script(
            "profile-with-'quote-and-;characters",
            "/api/2.0/clusters/list?name=one;two",
        )
        compile(source, "quoted_generated.py", "exec")
        self.assertIn("--profile", source)
        self.assertNotIn("shell" + "=True", source)


if __name__ == "__main__":
    unittest.main()
