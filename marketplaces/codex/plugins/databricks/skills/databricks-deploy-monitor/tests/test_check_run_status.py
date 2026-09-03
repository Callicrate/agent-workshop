"""Offline fake-CLI coverage for the public Databricks run-status helper."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pip._vendor.distlib.scripts import ScriptMaker

SKILL_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = SKILL_ROOT / "scripts" / "check_run_status.py"
SPEC = importlib.util.spec_from_file_location("check_run_status", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPER
SPEC.loader.exec_module(HELPER)

FAKE_CLI = r"""
import json
import os
import subprocess
import sys
import time

def main():
    scenario = json.loads(open(os.environ["FAKE_SCENARIO"], encoding="utf-8").read())
    arguments = sys.argv[1:]
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(arguments) + "\n")

    if scenario.get("descendant_marker"):
        ready_marker = scenario["descendant_ready_marker"]
        child_code = (
            "import pathlib, sys, time; "
            "pathlib.Path(sys.argv[1]).write_text('ready', encoding='utf-8'); "
            "time.sleep(float(sys.argv[3])); "
            "pathlib.Path(sys.argv[2]).write_text('survived', encoding='utf-8')"
        )
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_code,
                ready_marker,
                scenario["descendant_marker"],
                str(scenario.get("descendant_delay", 1.5)),
            ]
        )
        ready_deadline = time.monotonic() + 1
        while not os.path.exists(ready_marker):
            if time.monotonic() >= ready_deadline:
                raise RuntimeError("descendant did not start")
            time.sleep(0.01)

    if scenario.get("sleep_seconds"):
        time.sleep(scenario["sleep_seconds"])

    token = ""
    if "--page-token" in arguments:
        token = arguments[arguments.index("--page-token") + 1]
    failure = scenario.get("failures", {}).get(token)
    if failure:
        if failure.get("stderr"):
            sys.stderr.write(failure["stderr"])
        if failure.get("stdout"):
            sys.stdout.write(failure["stdout"])
        raise SystemExit(failure.get("exit", 1))
    if scenario.get("stdout_bytes"):
        sys.stdout.buffer.write(b"x" * scenario["stdout_bytes"])
        raise SystemExit(0)
    if scenario.get("stderr_bytes"):
        sys.stderr.buffer.write(b"x" * scenario["stderr_bytes"])
        raise SystemExit(1)
    page = scenario["pages"][token]
    if isinstance(page, str):
        sys.stdout.write(page)
    else:
        sys.stdout.write(json.dumps(page))
"""


def current_page(
    *,
    state: str = "TERMINATED",
    code: str | None = "SUCCESS",
    run_id: int = 123,
    **extra: object,
) -> dict[str, object]:
    status: dict[str, object] = {"state": state}
    if code is not None:
        status["termination_details"] = {"code": code}
    return {"run_id": run_id, "job_id": 9, "status": status, **extra}


class CheckRunStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fake_script = self.root / "fake_cli.py"
        self.fake_script.write_text(FAKE_CLI, encoding="utf-8")
        self.fake_command = self.root / "databricks.exe"
        maker = ScriptMaker(str(self.root), str(self.root))
        maker.executable = sys.executable
        maker.variants = {""}
        maker.clobber = True
        created = {Path(path) for path in maker.make("databricks = fake_cli:main")}
        self.assertIn(self.fake_command, created)
        self.scenario_path = self.root / "scenario.json"
        self.log_path = self.root / "argv.jsonl"
        self.wrapper_marker = self.root / "wrapper-invoked.txt"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _environment(
        self, scenario: dict[str, object], tool_input: str | None = None
    ) -> dict[str, str]:
        self.scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        environment = os.environ.copy()
        environment["PATH"] = str(self.root) + os.pathsep + environment.get("PATH", "")
        environment["PYTHONPATH"] = (
            str(self.root) + os.pathsep + environment.get("PYTHONPATH", "")
        )
        environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
        environment["FAKE_PYTHON"] = sys.executable
        environment["FAKE_SCRIPT"] = str(self.fake_script)
        environment["FAKE_SCENARIO"] = str(self.scenario_path)
        environment["FAKE_LOG"] = str(self.log_path)
        environment["FAKE_WRAPPER_MARKER"] = str(self.wrapper_marker)
        if tool_input is None:
            environment.pop("TOOL_INPUT", None)
        else:
            environment["TOOL_INPUT"] = tool_input
        return environment

    def _invoke(
        self,
        scenario: dict[str, object],
        arguments: list[str] | None = None,
        tool_input: str | None = None,
    ) -> tuple[int, dict[str, object], str]:
        completed = __import__("subprocess").run(
            [
                sys.executable,
                str(HELPER_PATH),
                *(arguments if arguments is not None else ["123", "explicit-profile"]),
            ],
            cwd=SKILL_ROOT,
            env=self._environment(scenario, tool_input),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout), completed.stderr

    def _in_process(
        self, scenario: dict[str, object], arguments: list[str], **overrides: object
    ) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        original_cwd = Path.cwd()
        try:
            os.chdir(SKILL_ROOT)
            with (
                patch.dict(os.environ, self._environment(scenario), clear=True),
                contextlib.redirect_stdout(output),
            ):
                with contextlib.ExitStack() as stack:
                    for name, value in overrides.items():
                        stack.enter_context(patch.object(HELPER, name, value))
                    exit_code = HELPER.main(arguments)
        finally:
            os.chdir(original_cwd)
        return exit_code, json.loads(output.getvalue())

    def _logged_argv(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def test_status_first_success_and_argv_allowlist(self) -> None:
        page = current_page(
            run_duration=1_501,
            start_time=1,
            end_time=9_999,
            run_page_url="https://example/run",
        )
        exit_code, result, stderr = self._invoke({"pages": {"": page}})
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(result["source"], "status")
        self.assertEqual(result["status"], "TERMINATED")
        self.assertEqual(result["termination"], "SUCCESS")
        self.assertTrue(result["is_success"])
        self.assertTrue(result["outcome_complete"])
        self.assertEqual(result["duration_seconds"], 2)
        self.assertEqual(
            self._logged_argv(),
            [
                [
                    "jobs",
                    "get-run",
                    "123",
                    "--profile",
                    "explicit-profile",
                    "--include-history",
                    "-o",
                    "json",
                ]
            ],
        )

    def test_known_active_status_is_a_valid_nonterminal_snapshot(self) -> None:
        for state in (
            "BLOCKED",
            "PENDING",
            "QUEUED",
            "RUNNING",
            "TERMINATING",
            "WAITING",
        ):
            with self.subTest(state=state):
                exit_code, result, _ = self._invoke(
                    {"pages": {"": current_page(state=state, code=None)}}
                )
                self.assertEqual(exit_code, 0)
                self.assertEqual(result["status"], state)
                self.assertFalse(result["is_terminal"])
                self.assertFalse(result["outcome_complete"])
                self.assertFalse(result["is_success"])

    def test_termination_failure_is_complete_but_not_a_process_error(self) -> None:
        exit_code, result, _ = self._invoke(
            {"pages": {"": current_page(code="DRIVER_ERROR")}}
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["outcome_complete"])
        self.assertFalse(result["is_success"])
        self.assertEqual(result["termination"], "DRIVER_ERROR")

    def test_legacy_fallback_and_mixed_conflict(self) -> None:
        legacy = {
            "run_id": 123,
            "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
        }
        exit_code, result, _ = self._invoke({"pages": {"": legacy}})
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["source"], "legacy_state")
        self.assertTrue(result["is_success"])

        mixed = current_page(
            code="SUCCESS", state="TERMINATED", state_message="ignored"
        )
        mixed["state"] = {"life_cycle_state": "RUNNING", "result_state": "FAILED"}
        exit_code, result, _ = self._invoke({"pages": {"": mixed}})
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["source"], "status")
        self.assertTrue(result["mixed_state_conflict"])
        self.assertTrue(result["is_success"])

    def test_unknown_root_state_or_terminal_code_fails_closed(self) -> None:
        for page in (
            current_page(state="FUTURE", code=None),
            current_page(code="FUTURE_CODE"),
            {
                "run_id": 123,
                "status": None,
                "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
            },
        ):
            with self.subTest(page=page):
                exit_code, result, _ = self._invoke({"pages": {"": page}})
                self.assertEqual(exit_code, 1)
                self.assertFalse(result["tasks_complete"])
                self.assertFalse(result["outcome_complete"])
                self.assertFalse(result["is_success"])

    def test_explicit_profile_modes_and_pre_cli_input_errors(self) -> None:
        scenario = {"pages": {"": current_page()}}
        exit_code, result, _ = self._invoke(
            scenario, arguments=[], tool_input='{"run_id": "123", "profile": "DEFAULT"}'
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["run_id"], 123)
        self.assertIn("DEFAULT", self._logged_argv()[0])

        for arguments, tool_input in (
            (["123"], None),
            (["123", ""], None),
            (["123", "x", "extra"], None),
            ([], '{"run_id":"123"}'),
        ):
            with self.subTest(arguments=arguments, tool_input=tool_input):
                self.log_path.unlink(missing_ok=True)
                exit_code, result, _ = self._invoke(
                    scenario, arguments=arguments, tool_input=tool_input
                )
                self.assertEqual(exit_code, 2)
                self.assertIn("error", result)
                self.assertEqual(self._logged_argv(), [])

    def test_run_id_is_limited_to_positive_decimal_int64_before_cli(self) -> None:
        maximum = "9223372036854775807"
        exit_code, result, _ = self._invoke(
            {"pages": {"": current_page(run_id=int(maximum))}},
            arguments=[maximum, "explicit-profile"],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["run_id"], int(maximum))

        scenario = {"pages": {"": current_page()}}
        for invalid_run_id in (
            "0",
            "-1",
            "9223372036854775808",
            "9999999999999999999",
        ):
            with self.subTest(invalid_run_id=invalid_run_id):
                self.log_path.unlink(missing_ok=True)
                exit_code, result, _ = self._invoke(
                    scenario, arguments=[invalid_run_id, "explicit-profile"]
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(result["error"]["code"], "invalid_run_id")
                self.assertEqual(self._logged_argv(), [])

    def test_windows_wrapper_and_unsafe_page_token_never_execute_second_effects(
        self,
    ) -> None:
        wrapper = self.root / "databricks.cmd"
        wrapper.write_text(
            '@echo off\r\necho wrapper-invoked > "%FAKE_WRAPPER_MARKER%"\r\n',
            encoding="utf-8",
        )
        page = current_page(has_more=True, next_page_token="opaque&ver")
        exit_code, result, _ = self._invoke({"pages": {"": page}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "invalid_pagination")
        self.assertEqual(len(self._logged_argv()), 1)
        self.assertFalse(self.wrapper_marker.exists())

        self.fake_command.unlink()
        self.log_path.unlink(missing_ok=True)
        with patch.object(
            HELPER.shutil,
            "which",
            side_effect=lambda name: str(wrapper) if name == "databricks" else None,
        ):
            exit_code, result = self._in_process(
                {"pages": {"": current_page()}}, ["123", "profile"]
            )
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "unsafe_cli_wrapper")
        self.assertEqual(self._logged_argv(), [])
        self.assertFalse(self.wrapper_marker.exists())

    def test_cluster_terminated_by_user_is_complete_and_actionable(self) -> None:
        page = current_page(
            code="CLUSTER_TERMINATED_BY_USER",
            tasks=[
                {
                    "task_key": "affected",
                    "run_id": 10,
                    "status": {
                        "state": "TERMINATED",
                        "termination_details": {"code": "CLUSTER_TERMINATED_BY_USER"},
                    },
                }
            ],
        )
        exit_code, result, _ = self._invoke({"pages": {"": page}})
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["outcome_complete"])
        self.assertFalse(result["is_success"])
        self.assertEqual(result["failed_task_runs"][0]["run_id"], 10)
        self.assertEqual(
            result["failed_task_runs"][0]["termination"],
            "CLUSTER_TERMINATED_BY_USER",
        )

    def test_timeout_kills_descendant_process_tree(self) -> None:
        descendant_marker = self.root / "descendant-survived.txt"
        ready_marker = self.root / "descendant-ready.txt"
        scenario = {
            "pages": {"": current_page()},
            "descendant_marker": str(descendant_marker),
            "descendant_ready_marker": str(ready_marker),
            "descendant_delay": 1.5,
            "sleep_seconds": 5,
        }
        exit_code, result = self._in_process(
            scenario,
            ["123", "profile"],
            CALL_TIMEOUT_SECONDS=0.8,
            OVERALL_TIMEOUT_SECONDS=2.0,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "cli_timeout")
        self.assertTrue(ready_marker.exists(), "test descendant never started")
        time.sleep(1.7)
        self.assertFalse(
            descendant_marker.exists(), "timed-out CLI child survived process cleanup"
        )

    def test_pagination_aggregates_empty_pages_and_keeps_page_tokens_private(
        self,
    ) -> None:
        first = current_page(
            tasks=[{"task_key": "a", "run_id": 10, "attempt_number": 0}],
            has_more=True,
            next_page_token="first-token",
        )
        empty = current_page(
            tasks=[], iterations=[], has_more=True, next_page_token="second-token"
        )
        last = current_page(
            tasks=[{"task_key": "b", "run_id": 11, "attempt_number": 0}],
            iterations=[
                {
                    "task_key": "fanout",
                    "run_id": 12,
                    "status": {
                        "state": "TERMINATED",
                        "termination_details": {"code": "DRIVER_ERROR"},
                    },
                }
            ],
            has_more=False,
        )
        exit_code, result, _ = self._invoke(
            {"pages": {"": first, "first-token": empty, "second-token": last}}
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["pages"], 3)
        self.assertTrue(result["tasks_complete"])
        self.assertEqual(result["task_run_ids"], {"a": 10, "b": 11})
        self.assertEqual(result["failed_iteration_runs"][0]["run_id"], 12)
        self.assertEqual(self._logged_argv()[-1][-2:], ["--page-token", "second-token"])

    def test_pagination_rejects_cycles_and_incoherent_tokens(self) -> None:
        cycle = current_page(has_more=True, next_page_token="loop")
        exit_code, result, _ = self._invoke({"pages": {"": cycle, "loop": cycle}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "pagination_cycle")

        no_token = current_page(has_more=True)
        exit_code, result, _ = self._invoke({"pages": {"": no_token}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "invalid_pagination")

        inconsistent = current_page(has_more=False, next_page_token="not-used")
        exit_code, result, _ = self._invoke({"pages": {"": inconsistent}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "invalid_pagination")

    def test_one_hundred_pages_without_a_token_succeeds_and_more_fails(self) -> None:
        pages = {}
        for index in range(100):
            token = "" if index == 0 else f"token-{index}"
            page = current_page(tasks=[])
            if index < 99:
                page.update({"has_more": True, "next_page_token": f"token-{index + 1}"})
            pages[token] = page
        request = HELPER.RunRequest(run_id="123", profile="explicit-profile")
        with patch.object(
            HELPER,
            "run_cli",
            side_effect=lambda _request, token, _deadline: pages[token or ""],
        ):
            result = HELPER.collect_run(request)
        self.assertEqual(result["pages"], 100)

        pages["token-100"] = current_page()
        pages["token-99"]["next_page_token"] = "token-100"
        with (
            patch.object(
                HELPER,
                "run_cli",
                side_effect=lambda _request, token, _deadline: pages[token or ""],
            ),
            self.assertRaises(HELPER.MonitorError) as error,
        ):
            HELPER.collect_run(request)
        self.assertEqual(error.exception.code, "pagination_limit_exceeded")

    def test_later_page_failure_is_incomplete(self) -> None:
        first = current_page(has_more=True, next_page_token="later")
        exit_code, result, _ = self._invoke(
            {
                "pages": {"": first},
                "failures": {"later": {"exit": 1, "stderr": "later failure"}},
            }
        )
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["tasks_complete"])
        self.assertEqual(result["error"]["code"], "cli_failed")

    def test_repair_attempts_select_current_root_task_and_ignore_config(self) -> None:
        page = current_page(
            job_clusters="not-an-array-and-ignored",
            settings={"tasks": "ignored-config"},
            repair_history=[{"task_run_ids": [10]}, {"task_run_ids": [11]}],
            tasks=[
                {
                    "task_key": "root",
                    "run_id": 10,
                    "attempt_number": 0,
                    "state": {
                        "life_cycle_state": "TERMINATED",
                        "result_state": "FAILED",
                    },
                },
                {
                    "task_key": "root",
                    "run_id": 11,
                    "attempt_number": 1,
                    "state": {
                        "life_cycle_state": "TERMINATED",
                        "result_state": "SUCCESS",
                    },
                },
            ],
        )
        exit_code, result, _ = self._invoke({"pages": {"": page}})
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["task_run_ids"], {"root": 11})
        self.assertEqual(result["failed_task_runs"], [])

    def test_malformed_payloads_and_wrong_identity_fail(self) -> None:
        malformed = current_page(tasks={"not": "a list"})
        exit_code, result, _ = self._invoke({"pages": {"": malformed}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "invalid_cli_schema")

        exit_code, result, _ = self._invoke({"pages": {"": current_page(run_id=456)}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "run_identity_mismatch")

        exit_code, result, _ = self._invoke({"pages": {"": "not json"}})
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "invalid_cli_json")

    def test_bounded_output_timeout_and_redaction(self) -> None:
        scenario = {"pages": {"": current_page()}, "stdout_bytes": 4_096}
        exit_code, result = self._in_process(
            scenario, ["123", "profile"], MAX_STDOUT_BYTES=128
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "stdout_limit_exceeded")

        scenario = {"pages": {"": current_page()}, "stderr_bytes": 4_096}
        exit_code, result = self._in_process(
            scenario, ["123", "profile"], MAX_STDERR_BYTES=128
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "stderr_limit_exceeded")

        scenario = {"pages": {"": current_page()}, "sleep_seconds": 0.2}
        exit_code, result = self._in_process(
            scenario,
            ["123", "profile"],
            CALL_TIMEOUT_SECONDS=0.01,
            OVERALL_TIMEOUT_SECONDS=1.0,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "cli_timeout")

        secret = "dapi" + "a" * 32
        scenario = {
            "pages": {"": current_page()},
            "failures": {
                "": {
                    "exit": 1,
                    "stderr": f"Authorization: Bearer {secret} token={secret}",
                }
            },
        }
        exit_code, result, _ = self._invoke(scenario)
        self.assertEqual(exit_code, 1)
        serialized = json.dumps(result)
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)

        boundary_secret = "top-secret-value"
        self.assertNotIn(
            boundary_secret, HELPER.redact("x" * 2_046 + "Bearer " + boundary_secret)
        )


if __name__ == "__main__":
    unittest.main()
