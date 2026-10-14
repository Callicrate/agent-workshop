"""Regression coverage for privacy-safe Python diagnostic helpers."""

from __future__ import annotations

import ast
import json
import linecache
import subprocess
import sys
import tempfile
import traceback as traceback_module
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_traceback  # noqa: E402
import check_notebook_runtime_order as notebook_order  # noqa: E402
from diagnostic_safety import error_envelope, sanitize_text  # noqa: E402


class DiagnosticSafetyTests(unittest.TestCase):
    @staticmethod
    def uniformly_wrap(text: str, prefix: str) -> str:
        return "\n".join(f"{prefix}{line}" for line in text.splitlines())

    def native_traceback(
        self,
        source: str,
        filename: str,
        expected: type[BaseException],
        *,
        source_present: bool,
    ) -> str:
        absent = object()
        previous = linecache.cache.get(filename, absent)
        if source_present:
            linecache.cache[filename] = (
                len(source),
                None,
                source.splitlines(keepends=True),
                filename,
            )
        else:
            linecache.cache.pop(filename, None)
        try:
            try:
                exec(compile(source, filename, "exec"), {})
            except expected as exc:
                return "".join(traceback_module.format_exception(exc))
        finally:
            if previous is absent:
                linecache.cache.pop(filename, None)
            else:
                linecache.cache[filename] = previous  # type: ignore[assignment]
        self.fail(f"synthetic source did not raise {expected.__name__}")

    def test_sanitizer_redacts_controls_paths_secrets_and_long_values(self) -> None:
        github = "gh" + "p_" + "A" * 24
        token = "Bearer " + "B" * 24
        databricks = "dapi" + "C" * 24
        jwt = "ey" + "J" + "header.payload.signature"
        openai = "s" + "k-" + "D" * 24
        message = f"\x1b[31mAuthorization: {token}\r\nurl=https://user:pass@word@example.test/x {github} {databricks} {jwt} {openai} password=hidden"
        sanitized, metadata = sanitize_text(message, limit=48)
        self.assertNotIn("user:pass", sanitized)
        self.assertNotIn("word", sanitized)
        self.assertNotIn(github, sanitized)
        self.assertNotIn(databricks, sanitized)
        self.assertNotIn(jwt, sanitized)
        self.assertNotIn(openai, sanitized)
        self.assertNotIn("hidden", sanitized)
        self.assertNotIn("\n", sanitized)
        self.assertNotIn("\x1b", sanitized)
        self.assertGreaterEqual(metadata.redaction_count, 7)
        self.assertGreater(metadata.controls_removed, 0)
        self.assertTrue(metadata.truncated)

    def test_sanitizer_redacts_cookie_and_session_values_but_keeps_names(self) -> None:
        sentinel = "Z" * 24
        message = f'Cookie: session={sentinel}; theme=dark; Set-Cookie: prefs="{sentinel}"; Path=/; "Cookie": "sid={sentinel}; color=blue"; session_token: "{sentinel}"'
        sanitized, metadata = sanitize_text(message)
        self.assertNotIn(sentinel, sanitized)
        self.assertIn("Cookie:", sanitized)
        self.assertIn("Set-Cookie:", sanitized)
        for name in (
            "session=",
            "theme=",
            "prefs=",
            "Path=",
            "sid=",
            "color=",
            "session_token",
        ):
            self.assertIn(name, sanitized)
        self.assertGreaterEqual(metadata.redaction_count, 7)
        traceback = analyze_traceback.analyze(f"ValueError: {message}")
        self.assertNotIn(sentinel, analyze_traceback.format_analysis(traceback))
        self.assertNotIn(
            sentinel, json.dumps(analyze_traceback.success_envelope(traceback))
        )
        self.assertNotIn(sentinel, json.dumps(error_envelope("input_error", message)))

    def test_traceback_removes_source_context_and_uses_safe_path(self) -> None:
        github = "gh" + "p_" + "C" * 24
        traceback = (
            "Traceback (most recent call last):\n"
            '  File "C:/private/project/secrets.py", line 7, in run\n'
            f'    token = "{github}"\n'
            "AttributeError: 'NoneType' object has no attribute 'id'\n"
        )
        analysis = analyze_traceback.analyze(traceback)
        rendered = analyze_traceback.format_analysis(analysis)
        self.assertEqual(analysis.file_path, "secrets.py")
        self.assertIsNone(analysis.code_context)
        self.assertNotIn(
            github, json.dumps(analyze_traceback.success_envelope(analysis))
        )
        self.assertNotIn("token =", rendered)
        self.assertIn("AttributeError_NoneType", analysis.related_patterns)

    def test_traceback_uses_repo_relative_path_only_when_explicit(self) -> None:
        root = Path(tempfile.mkdtemp())
        source = root / "src" / "worker.py"
        analysis = analyze_traceback.analyze(
            f"File \"{source}\", line 3, in run\nNameError: name 'x' is not defined",
            repo_root=root,
        )
        self.assertEqual(analysis.file_path, "src/worker.py")

    def test_traceback_prefers_last_exception_over_warning_and_keeps_notes(
        self,
    ) -> None:
        text = (
            "RuntimeWarning: preliminary warning\n"
            "ValueError: first failure\n"
            "  additional bounded detail\n"
            "plain PEP 678 note\n"
            "note: investigate source boundary\n"
            "  owned by ingestion\n"
            "During handling of the above exception, another exception occurred:\n"
            "KeyError: final failure\n"
            "=========================== short test summary info ===========================\n"
            "FAILED test_example.py::test_case\n"
        )
        analysis = analyze_traceback.analyze(text)
        self.assertEqual(analysis.error_type, "KeyError")
        self.assertEqual(analysis.error_message, "final failure")
        self.assertEqual(
            analysis.notes,
            ["plain PEP 678 note", "investigate source boundary", "owned by ingestion"],
        )
        self.assertEqual(
            [item["error_type"] for item in analysis.exception_chain],
            ["RuntimeWarning", "ValueError", "KeyError"],
        )

    def test_traceback_handles_exception_group_and_warning_location(self) -> None:
        group = "  | ExceptionGroup: grouped failures (2 sub-exceptions)\n  +-+---------------- 1 ----------------\n  | ValueError: inner\n"
        parsed = analyze_traceback.parse_traceback(group)
        self.assertEqual(parsed["error_type"], "ValueError")
        warning = analyze_traceback.parse_traceback(
            "module.py:12: RuntimeWarning: check input"
        )
        self.assertEqual(warning["error_type"], "RuntimeWarning")

    def test_traceback_without_displayed_source_keeps_chain_note_and_location(self) -> None:
        source = (
            "def inner():\n"
            "    raise RuntimeError('inner-failure')\n"
            "def outer():\n"
            "    try:\n"
            "        inner()\n"
            "    except RuntimeError as error:\n"
            "        final = KeyError('synthetic-key')\n"
            "        final.add_note('synthetic-note')\n"
            "        raise final from error\n"
            "outer()\n"
        )
        rendered = self.native_traceback(
            source, "<synthetic-missing-source>", KeyError, source_present=False
        )
        parsed = analyze_traceback.parse_traceback(rendered)
        analysis = analyze_traceback.analyze(rendered)
        self.assertEqual(parsed["error_type"], "KeyError")
        self.assertEqual(
            [item["error_type"] for item in parsed["exception_chain"]],
            ["RuntimeError", "KeyError"],
        )
        self.assertEqual(parsed["notes"], ["synthetic-note"])
        self.assertEqual(parsed["file_path"], "<synthetic-missing-source>")
        self.assertIsInstance(parsed["line_number"], int)
        self.assertEqual(analysis.error_type, "KeyError")
        self.assertEqual(analysis.related_patterns, ["KeyError"])
        self.assertIn("Missing dictionary key", analysis.likely_causes)
        expected = (
            parsed["error_type"],
            parsed["error_message"],
            parsed["exception_chain"],
            parsed["notes"],
            parsed["file_path"],
            parsed["line_number"],
            analysis.related_patterns,
            analysis.likely_causes,
        )
        for name, prefix in (
            ("ascii", " "),
            ("nbsp", "\N{NO-BREAK SPACE}"),
            ("em-space", "\N{EM SPACE}"),
        ):
            with self.subTest(wrapper=name):
                wrapped = self.uniformly_wrap(rendered, prefix)
                wrapped_parsed = analyze_traceback.parse_traceback(wrapped)
                wrapped_analysis = analyze_traceback.analyze(wrapped)
                self.assertEqual(
                    (
                        wrapped_parsed["error_type"],
                        wrapped_parsed["error_message"],
                        wrapped_parsed["exception_chain"],
                        wrapped_parsed["notes"],
                        wrapped_parsed["file_path"],
                        wrapped_parsed["line_number"],
                        wrapped_analysis.related_patterns,
                        wrapped_analysis.likely_causes,
                    ),
                    expected,
                )

    def test_traceback_plain_note_and_continuation_layout_is_wrapper_relative(
        self,
    ) -> None:
        cases = {
            "warning-note": (
                "module.py:12: RuntimeWarning: warning-message\n"
                "warning-note\n"
            ),
            "detail-and-note": (
                "ValueError: first failure\n"
                "  additional bounded detail\n"
                "plain note\n"
            ),
            "same-level-convention": (
                "ValueError: first failure\n"
                "second physical line\n"
            ),
            "same-indent-hand-authored": (
                "  ValueError: first failure\n"
                "  same-level note\n"
            ),
            "deeper-continuation": (
                "  ValueError: first failure\n"
                "    deeper detail\n"
            ),
            "labeled-active-note": (
                "ValueError: first failure\n"
                "note: labeled note\n"
                "  continued note\n"
            ),
            "chain-and-frame": (
                "RuntimeError: earlier failure\n"
                ' File "synthetic_chain.py", line 8, in run\n'
                "   source_call()\n"
                "KeyError: final failure\n"
                "final note\n"
            ),
        }
        expected_details = analyze_traceback.parse_traceback(cases["detail-and-note"])
        self.assertEqual(
            expected_details["error_message"],
            "first failure additional bounded detail",
        )
        self.assertEqual(expected_details["notes"], ["plain note"])
        expected_same_level = analyze_traceback.parse_traceback(
            cases["same-level-convention"]
        )
        self.assertEqual(expected_same_level["error_message"], "first failure")
        self.assertEqual(expected_same_level["notes"], ["second physical line"])
        expected_deeper = analyze_traceback.parse_traceback(
            cases["deeper-continuation"]
        )
        self.assertEqual(expected_deeper["error_message"], "first failure deeper detail")
        self.assertEqual(expected_deeper["notes"], [])
        expected_chain = analyze_traceback.parse_traceback(cases["chain-and-frame"])
        self.assertEqual(
            [item["error_type"] for item in expected_chain["exception_chain"]],
            ["RuntimeError", "KeyError"],
        )
        self.assertEqual(expected_chain["notes"], ["final note"])
        replacement = analyze_traceback.parse_traceback(
            "RuntimeError: earlier failure\n"
            "  KeyError: final failure\n"
            "  final note\n"
        )
        self.assertEqual(
            [item["error_message"] for item in replacement["exception_chain"]],
            ["earlier failure", "final failure"],
        )
        self.assertEqual(replacement["notes"], ["final note"])
        frame_cleared = analyze_traceback.parse_traceback(
            self.uniformly_wrap(
                "RuntimeError: earlier failure\n"
                ' File "synthetic_transition.py", line 4, in run\n'
                "   source_call()\n"
                "between-frame detail\n"
                "KeyError: final failure\n"
                "final note\n",
                "\N{NO-BREAK SPACE}",
            )
        )
        self.assertEqual(
            [item["error_message"] for item in frame_cleared["exception_chain"]],
            ["earlier failure between-frame detail", "final failure"],
        )
        self.assertEqual(frame_cleared["notes"], ["final note"])
        for case_name, text in cases.items():
            baseline = analyze_traceback.parse_traceback(text)
            for wrapper_name, prefix in (
                ("ascii", " "),
                ("nbsp", "\N{NO-BREAK SPACE}"),
                ("em-space", "\N{EM SPACE}"),
            ):
                with self.subTest(case=case_name, wrapper=wrapper_name):
                    wrapped = analyze_traceback.parse_traceback(
                        self.uniformly_wrap(text, prefix)
                    )
                    self.assertEqual(wrapped, baseline)

    def test_traceback_pipe_candidate_note_behavior_is_unchanged(self) -> None:
        text = "  | ValueError: inner\n  | same-level content\n"
        baseline = analyze_traceback.parse_traceback(text)
        self.assertEqual(baseline["error_message"], "inner")
        self.assertEqual(baseline["notes"], [])
        for name, prefix in (
            ("ascii", " "),
            ("nbsp", "\N{NO-BREAK SPACE}"),
            ("em-space", "\N{EM SPACE}"),
        ):
            with self.subTest(wrapper=name):
                self.assertEqual(
                    analyze_traceback.parse_traceback(
                        self.uniformly_wrap(text, prefix)
                    ),
                    baseline,
                )

    def test_traceback_wrapped_native_note_stays_in_cli_note_outputs(self) -> None:
        source = (
            "error = KeyError('final')\n"
            "error.add_note('synthetic-note')\n"
            "raise error\n"
        )
        rendered = self.native_traceback(
            source, "<synthetic-cli-note>", KeyError, source_present=False
        )
        wrapped = self.uniformly_wrap(rendered, "\N{NO-BREAK SPACE}")
        script = SCRIPTS / "analyze_traceback.py"
        json_result = subprocess.run(
            [sys.executable, "-B", str(script), "-", "--json"],
            input=wrapped,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(json_result.stdout)
        self.assertEqual(json_result.returncode, 0)
        self.assertEqual(json_result.stderr, "")
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["analysis"]["error_message"], "'final'")
        self.assertEqual(payload["analysis"]["notes"], ["synthetic-note"])
        text_result = subprocess.run(
            [sys.executable, "-B", str(script), "-"],
            input=wrapped,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(text_result.returncode, 0)
        self.assertEqual(text_result.stderr, "")
        self.assertIn("Message: 'final'", text_result.stdout)
        self.assertIn("Notes:\n  - synthetic-note", text_result.stdout)

    def test_traceback_excludes_header_like_displayed_source_from_every_sink(self) -> None:
        marker = "SOURCE_ONLY_ANNOTATION"
        source = (
            "ValueError: str = (_ for _ in ()).throw(KeyError('synthetic-key'))"
            f"  # {marker}\n"
        )
        rendered = self.native_traceback(
            source, "synthetic_header_like_source.py", KeyError, source_present=True
        )
        parsed = analyze_traceback.parse_traceback(rendered)
        analysis = analyze_traceback.analyze(rendered)
        sinks = (
            json.dumps(parsed, sort_keys=True),
            json.dumps(analysis.exception_chain, sort_keys=True),
            json.dumps(analyze_traceback.success_envelope(analysis), sort_keys=True),
            analyze_traceback.format_analysis(analysis),
        )
        self.assertEqual(parsed["error_type"], "KeyError")
        self.assertEqual(
            [item["error_type"] for item in parsed["exception_chain"]], ["KeyError"]
        )
        for sink in sinks:
            self.assertNotIn(marker, sink)

    def test_traceback_excludes_native_multiline_source_shape(self) -> None:
        marker = "SOURCE_ONLY_LATER"
        cases = {
            "index": (
                "def boom():\n"
                "    values = {}\n"
                "    return values[\n"
                f"        'missing'  # {marker}\n"
                "    ]\n"
                "boom()\n"
            ),
            "call": (
                "def boom():\n"
                "    return int(\n"
                f"        'not-an-integer'  # {marker}\n"
                "    )\n"
                "boom()\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                rendered = self.native_traceback(
                    source,
                    f"synthetic_multiline_{name}.py",
                    Exception,
                    source_present=True,
                )
                analysis = analyze_traceback.analyze(rendered)
                self.assertNotIn(
                    marker,
                    json.dumps(
                        analyze_traceback.success_envelope(analysis), sort_keys=True
                    ),
                )

    def test_traceback_excludes_multiline_dictionary_header_from_chain(self) -> None:
        marker = "SOURCE_ONLY_LATER_HEADER"
        source = (
            "def boom():\n"
            "    return {\n"
            f"        ValueError: '{marker}',\n"
            "    }['missing']\n"
            "boom()\n"
        )
        rendered = self.native_traceback(
            source, "synthetic_dict_header.py", KeyError, source_present=True
        )
        parsed = analyze_traceback.parse_traceback(rendered)
        analysis = analyze_traceback.analyze(rendered)
        self.assertEqual(parsed["error_type"], "KeyError")
        self.assertNotIn("ValueError", [item["error_type"] for item in parsed["exception_chain"]])
        self.assertNotIn(
            marker,
            json.dumps(analyze_traceback.success_envelope(analysis), sort_keys=True),
        )

    def test_traceback_handles_native_group_source_presence_and_inner_location(self) -> None:
        marker = "SOURCE_ONLY_GROUP_HEADER"
        source = (
            "def boom():\n"
            "    return {\n"
            f"        ValueError: '{marker}',\n"
            "    }['missing']\n"
            "try:\n"
            "    boom()\n"
            "except KeyError as error:\n"
            "    raise ExceptionGroup('synthetic-group', [error])\n"
        )
        for source_present in (True, False):
            with self.subTest(source_present=source_present):
                filename = f"synthetic_group_{source_present}.py"
                rendered = self.native_traceback(
                    source,
                    filename,
                    ExceptionGroup,
                    source_present=source_present,
                )
                parsed = analyze_traceback.parse_traceback(rendered)
                analysis = analyze_traceback.analyze(rendered)
                self.assertEqual(parsed["error_type"], "KeyError")
                self.assertEqual(parsed["file_path"], filename)
                self.assertIsInstance(parsed["line_number"], int)
                self.assertNotIn(
                    marker,
                    json.dumps(
                        analyze_traceback.success_envelope(analysis), sort_keys=True
                    ),
                )

    def test_traceback_uniform_nbsp_wrappers_preserve_source_exclusion(self) -> None:
        marker = "SOURCE_ONLY_NBSP"
        cases = {
            "ordinary": (
                "ValueError: str = (_ for _ in ()).throw(KeyError('synthetic-key'))"
                f"  # {marker}\n",
                "synthetic_nbsp_ordinary.py",
                KeyError,
            ),
            "pipe": (
                "def boom():\n"
                "    return {\n"
                f"        ValueError: '{marker}',\n"
                "    }['missing']\n"
                "try:\n"
                "    boom()\n"
                "except KeyError as error:\n"
                "    raise ExceptionGroup('synthetic-group', [error])\n",
                "synthetic_nbsp_group.py",
                ExceptionGroup,
            ),
        }
        for name, (source, filename, expected) in cases.items():
            with self.subTest(name=name):
                rendered = self.native_traceback(
                    source, filename, expected, source_present=True
                )
                wrapped = "\n".join(
                    f"\N{NO-BREAK SPACE}{line}" for line in rendered.splitlines()
                )
                parsed = analyze_traceback.parse_traceback(wrapped)
                analysis = analyze_traceback.analyze(wrapped)
                chain_types = [
                    item["error_type"] for item in parsed["exception_chain"]
                ]
                sinks = (
                    json.dumps(parsed, sort_keys=True),
                    json.dumps(analysis.exception_chain, sort_keys=True),
                    json.dumps(
                        analyze_traceback.success_envelope(analysis), sort_keys=True
                    ),
                    analyze_traceback.format_analysis(analysis),
                )
                self.assertEqual(parsed["error_type"], "KeyError")
                self.assertEqual(chain_types[-1], "KeyError")
                self.assertNotIn("ValueError", chain_types)
                self.assertEqual(parsed["file_path"], filename)
                self.assertIsInstance(parsed["line_number"], int)
                for sink in sinks:
                    self.assertNotIn(marker, sink)

    def test_traceback_source_block_boundaries_are_reprocessed(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "real_outer.py", line 3, in outer\n'
            "    ValueError: SOURCE_ONLY_HEADER\n"
            "      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
            "    \n"
            "    ]\n"
            '  File "real_inner.py", line 8, in inner\n'
            "    File \"SOURCE_ONLY_MARKER.py\", line 999\n"
            "    ...\n"
            "  KeyError: equal-indent-boundary\n"
            ' | File "group_inner.py", line 11, in grouped\n'
            " |     RuntimeError: SOURCE_ONLY_GROUP\n"
            " | ValueError: context-boundary\n"
            "RuntimeError: final-failure\n"
        )
        parsed = analyze_traceback.parse_traceback(text)
        analysis = analyze_traceback.analyze(text)
        envelope = json.dumps(analyze_traceback.success_envelope(analysis), sort_keys=True)
        self.assertEqual(parsed["error_type"], "RuntimeError")
        self.assertEqual(parsed["error_message"], "final-failure")
        self.assertEqual(parsed["file_path"], "group_inner.py")
        self.assertEqual(parsed["line_number"], 11)
        self.assertEqual(
            [item["error_type"] for item in parsed["exception_chain"]],
            ["KeyError", "ValueError", "RuntimeError"],
        )
        for marker in (
            "SOURCE_ONLY_HEADER",
            "SOURCE_ONLY_MARKER",
            "SOURCE_ONLY_GROUP",
        ):
            self.assertNotIn(marker, envelope)

    def test_traceback_json_errors_are_stable_and_sanitized(self) -> None:
        script = SCRIPTS / "analyze_traceback.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "does-not-exist", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "input_not_found")
        self.assertNotIn("Traceback", result.stderr)


class NotebookOrderTests(unittest.TestCase):
    def write_notebook(self, payload: object, name: str = "sample.ipynb") -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def report_for(
        self, cells: list[dict[str, object]]
    ) -> notebook_order.NotebookReport:
        path = self.write_notebook({"cells": cells})
        return notebook_order.check_notebook(path)

    @staticmethod
    def code(source: object, **extra: object) -> dict[str, object]:
        return {"cell_type": "code", "source": source, **extra}

    def categories(self, report: notebook_order.NotebookReport) -> list[str]:
        return [finding.category for finding in report.findings]

    def test_function_async_parameters_and_nested_globals_do_not_define_module_state(
        self,
    ) -> None:
        report = self.report_for(
            [
                self.code(
                    "def local(value):\n    return value\nasync def also_local(item):\n    return item"
                ),
                self.code("def mutate():\n    global leaked\n    leaked = 1"),
                self.code("print(leaked)"),
            ]
        )
        unresolved = [
            finding.message
            for finding in report.findings
            if finding.category == "unresolved-symbol"
        ]
        self.assertTrue(any("leaked" in message for message in unresolved))
        self.assertFalse(
            any("value" in message or "item" in message for message in unresolved)
        )

    def test_same_cell_use_before_definition_and_import_are_detected(self) -> None:
        report = self.report_for(
            [self.code("run()\ndef run():\n    return 1\njson.dumps({})\nimport json")]
        )
        categories = self.categories(report)
        self.assertIn("definition-after-use", categories)
        self.assertIn("import-after-use", categories)

    def test_deleted_and_conditional_definitions_are_not_reliable_state(self) -> None:
        report = self.report_for(
            [
                self.code("value = 1\ndel value\nprint(value)"),
                self.code("if condition:\n    maybe = 1\nprint(maybe)"),
            ]
        )
        self.assertIn("deleted-symbol", self.categories(report))
        self.assertIn("conditional-definition", self.categories(report))

    def test_conditional_delete_warns_without_claiming_definite_deletion(self) -> None:
        maybe_deleted = self.report_for(
            [
                self.code("value = 1"),
                self.code("if branch:\n    del value"),
                self.code("print(value)"),
            ]
        )
        self.assertIn("conditional-state", self.categories(maybe_deleted))
        self.assertNotIn("deleted-symbol", self.categories(maybe_deleted))
        branch_variants = self.report_for(
            [
                self.code("value = 1"),
                self.code("if branch:\n    del value\nelse:\n    value = 2"),
                self.code("print(value)"),
            ]
        )
        self.assertIn("conditional-state", self.categories(branch_variants))
        restored = self.report_for(
            [
                self.code("value = 1"),
                self.code("if branch:\n    del value"),
                self.code("value = 2\nprint(value)"),
            ]
        )
        self.assertNotIn("conditional-state", self.categories(restored))

    def test_structural_restart_clears_state_but_strings_do_not(self) -> None:
        restarted = self.report_for(
            [
                self.code("value = 1"),
                self.code("dbutils.library.restartPython()"),
                self.code("print(value)"),
            ]
        )
        self.assertEqual(restarted.restart_cells, [2])
        self.assertGreaterEqual(len(restarted.segments), 2)
        self.assertTrue(
            any(
                "value" in finding.message
                for finding in restarted.findings
                if finding.category == "unresolved-symbol"
            )
        )
        warm = self.report_for(
            [
                self.code("message = 'restartPython'; value = 1"),
                self.code("print(value)"),
            ]
        )
        self.assertEqual(warm.restart_cells, [])
        self.assertFalse(
            any(
                "value" in finding.message
                for finding in warm.findings
                if finding.category == "unresolved-symbol"
            )
        )

    def test_restart_requires_a_direct_unrebound_statement_and_resets_after_prefix(
        self,
    ) -> None:
        lookalikes = {
            "function": "def restart():\n    dbutils.library.restartPython()",
            "lambda": "restart = lambda: dbutils.library.restartPython()",
            "conditional": "if False:\n    dbutils.library.restartPython()",
            "comprehension": "[dbutils.library.restartPython() for _ in []]",
            "text": "message = 'dbutils.library.restartPython()'",
        }
        for name, source in lookalikes.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [self.code("%pip install demo"), self.code(source)]
                )
                self.assertEqual(report.restart_cells, [])
                self.assertIn("install-without-restart", self.categories(report))

        restarted = self.report_for(
            [
                self.code("%pip install demo"),
                self.code(
                    "before()\nvalue = 1\ndbutils.library.restartPython()\ndef before():\n    return 1"
                ),
                self.code("print(value)"),
            ]
        )
        self.assertEqual(restarted.restart_cells, [2])
        self.assertIn("unresolved-symbol", self.categories(restarted))
        self.assertFalse(
            any(
                finding.category == "definition-after-use"
                and "before" in finding.message
                for finding in restarted.findings
            )
        )
        self.assertNotIn("before", restarted.definition_cells)
        self.assertTrue(
            any(
                finding.category == "unresolved-symbol" and "value" in finding.message
                for finding in restarted.findings
            )
        )

        shutdown = self.report_for(
            [
                self.code("%pip install demo"),
                self.code("get_ipython().kernel.do_shutdown(restart=True)"),
            ]
        )
        self.assertEqual(shutdown.restart_cells, [2])
        self.assertNotIn("install-without-restart", self.categories(shutdown))

        mutations = {
            "name-assignment": "dbutils = None\ndbutils.library.restartPython()",
            "name-delete": "del dbutils\ndbutils.library.restartPython()",
            "literal-attribute": "dbutils.library.restartPython = None\ndbutils.library.restartPython()",
            "literal-setattr": "setattr(dbutils.library, 'restartPython', None)\ndbutils.library.restartPython()",
            "literal-vars": "vars(dbutils)['library'] = None\ndbutils.library.restartPython()",
            "tuple-target": "(dbutils.library.restartPython, other) = (None, None)\ndbutils.library.restartPython()",
            "zero-iteration-for": "for dbutils.library.restartPython in []:\n    pass\ndbutils.library.restartPython()",
            "with-target": "with manager() as dbutils.library.restartPython:\n    pass\ndbutils.library.restartPython()",
        }
        for name, source in mutations.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [self.code("%pip install demo"), self.code(source)]
                )
                self.assertEqual(report.restart_cells, [])
                self.assertIn("install-without-restart", self.categories(report))

        class_local = self.report_for(
            [
                self.code("%pip install demo"),
                self.code(
                    "class Namespace:\n    dbutils = None\ndbutils.library.restartPython()"
                ),
            ]
        )
        self.assertEqual(class_local.restart_cells, [2])
        self.assertNotIn("install-without-restart", self.categories(class_local))

    def test_restart_is_terminal_for_its_cell_in_api_and_cli_reports(self) -> None:
        path = self.write_notebook(
            {
                "cells": [
                    self.code("%pip install demo"),
                    self.code("dbutils.library.restartPython(); after = 1"),
                    self.code("print(after)"),
                ]
            },
            "terminal-restart.ipynb",
        )
        report = notebook_order.check_notebook(path)
        self.assertEqual(report.restart_cells, [2])
        self.assertNotIn("after", report.definition_cells)
        self.assertEqual(report.first_use_cells["after"], 3)
        self.assertTrue(
            any(
                finding.category == "unresolved-symbol"
                and finding.cell == 3
                and "after" in finding.message
                for finding in report.findings
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(path),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        cli_report = payload["results"][0]["report"]
        self.assertEqual(cli_report["restart_cells"], [2])
        self.assertNotIn("after", cli_report["definition_cells"])
        self.assertEqual(cli_report["first_use_cells"]["after"], 3)

    def test_ipython_shutdown_integrity_is_independent_and_bounded(self) -> None:
        dbutils_rebound = self.report_for(
            [
                self.code("%pip install demo"),
                self.code(
                    "dbutils = None\nget_ipython().kernel.do_shutdown(restart=True)"
                ),
            ]
        )
        self.assertEqual(dbutils_rebound.restart_cells, [2])
        self.assertNotIn("install-without-restart", self.categories(dbutils_rebound))

        mutations = {
            "name-assignment": "get_ipython = None\nget_ipython().kernel.do_shutdown(restart=True)",
            "name-delete": "del get_ipython\nget_ipython().kernel.do_shutdown(restart=True)",
            "literal-attribute": "get_ipython().kernel.do_shutdown = None\nget_ipython().kernel.do_shutdown(restart=True)",
            "literal-setattr": "setattr(get_ipython(), 'kernel', None)\nget_ipython().kernel.do_shutdown(restart=True)",
            "literal-vars": "vars(get_ipython().kernel)['do_shutdown'] = None\nget_ipython().kernel.do_shutdown(restart=True)",
            "tuple-target": "(get_ipython().kernel.do_shutdown, other) = (None, None)\nget_ipython().kernel.do_shutdown(restart=True)",
            "zero-iteration-for": "for get_ipython().kernel.do_shutdown in []:\n    pass\nget_ipython().kernel.do_shutdown(restart=True)",
        }
        for name, source in mutations.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [self.code("%pip install demo"), self.code(source)]
                )
                self.assertEqual(report.restart_cells, [])
                self.assertIn("install-without-restart", self.categories(report))

        class_local = self.report_for(
            [
                self.code("%pip install demo"),
                self.code(
                    "class Namespace:\n    get_ipython = None\nget_ipython().kernel.do_shutdown(restart=True)"
                ),
            ]
        )
        self.assertEqual(class_local.restart_cells, [2])

        cli_path = self.write_notebook(
            {
                "cells": [
                    self.code("%pip install demo"),
                    self.code(
                        "get_ipython = None\nget_ipython().kernel.do_shutdown(restart=True)"
                    ),
                ]
            },
            "rebound-ipython.ipynb",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(cli_path),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        cli_report = json.loads(result.stdout)["results"][0]["report"]
        self.assertEqual(cli_report["restart_cells"], [])
        self.assertTrue(
            any(
                finding["category"] == "install-without-restart"
                for finding in cli_report["findings"]
            )
        )

    def test_restart_calls_require_the_supported_literal_signature(self) -> None:
        malformed = {
            "dbutils-positional": "dbutils.library.restartPython(False)",
            "dbutils-keyword": "dbutils.library.restartPython(restart=True)",
            "ipython-duplicate-restart": "get_ipython().kernel.do_shutdown(False, restart=True)",
            "ipython-extra-keyword": "get_ipython().kernel.do_shutdown(restart=True, force=True)",
            "ipython-expanded-keyword": "get_ipython().kernel.do_shutdown(**{'restart': True})",
            "ipython-false": "get_ipython().kernel.do_shutdown(restart=False)",
        }
        for name, source in malformed.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [self.code("%pip install demo"), self.code(source)]
                )
                self.assertEqual(report.restart_cells, [])
                self.assertIn("install-without-restart", self.categories(report))

        path = self.write_notebook(
            {
                "cells": [
                    self.code("%pip install demo"),
                    self.code("get_ipython().kernel.do_shutdown(False, restart=True)"),
                ]
            },
            "malformed-ipython-restart.ipynb",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(path),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        cli_report = json.loads(result.stdout)["results"][0]["report"]
        self.assertEqual(cli_report["restart_cells"], [])
        self.assertTrue(
            any(
                finding["category"] == "install-without-restart"
                for finding in cli_report["findings"]
            )
        )

    def test_immediate_definition_expressions_and_comprehension_scopes_are_ordered(
        self,
    ) -> None:
        late = self.report_for(
            [
                self.code(
                    "@decorator(decorator_argument)\n"
                    "def build(value: Later = default) -> Result:\n"
                    "    local = value\n"
                    "    return local\n"
                    "lambda_build = lambda value=lambda_default: value\n"
                    "class Child(base, metaclass=class_meta):\n"
                    "    captured = class_late\n"
                    "values = [item for item in iterable if item > lower]\n"
                    "pairs = [inner for outer in groups for inner in outer]\n"
                    "decorator = lambda value: lambda function: function\n"
                    "decorator_argument = 1\n"
                    "default = 1\n"
                    "lambda_default = 1\n"
                    "Later = int\n"
                    "Result = int\n"
                    "base = object\n"
                    "class_meta = type\n"
                    "class_late = 1\n"
                    "iterable = [1]\n"
                    "lower = 0\n"
                    "groups = [[1]]"
                )
            ]
        )
        for name in (
            "decorator",
            "decorator_argument",
            "default",
            "lambda_default",
            "base",
            "class_meta",
            "class_late",
            "iterable",
            "lower",
            "groups",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    any(
                        finding.category == "definition-after-use"
                        and f"{name!r}" in finding.message
                        for finding in late.findings
                    )
                )
        for name in ("Later", "Result"):
            with self.subTest(name=name):
                annotation_finding = any(
                    finding.category == "definition-after-use"
                    and f"{name!r}" in finding.message
                    for finding in late.findings
                )
                self.assertEqual(
                    annotation_finding,
                    not notebook_order.LAZY_ANNOTATIONS,
                )
        for local in ("value", "local", "item", "outer", "inner"):
            self.assertFalse(
                any(f"{local!r}" in finding.message for finding in late.findings)
            )

        ready = self.report_for(
            [
                self.code(
                    "decorator = lambda value: lambda function: function\n"
                    "decorator_argument = 1\n"
                    "default = 1\n"
                    "lambda_default = 1\n"
                    "Later = int\n"
                    "Result = int\n"
                    "base = object\n"
                    "class_meta = type\n"
                    "class_late = 1\n"
                    "iterable = [1]\n"
                    "lower = 0\n"
                    "groups = [[1]]\n"
                    "@decorator(decorator_argument)\n"
                    "def build(value: Later = default) -> Result:\n"
                    "    return value\n"
                    "lambda_build = lambda value=lambda_default: value\n"
                    "class Child(base, metaclass=class_meta):\n"
                    "    captured = class_late\n"
                    "values = [item for item in iterable if item > lower]\n"
                    "pairs = [inner for outer in groups for inner in outer]"
                )
            ]
        )
        self.assertFalse(any(finding.severity == "error" for finding in ready.findings))

    def test_generator_expression_defers_its_body_but_not_outer_iterator(self) -> None:
        deferred_body = self.report_for(
            [
                self.code(
                    "source = [1]\n"
                    "stream = (late * item for item in source)\n"
                    "late = 2\n"
                    "list(stream)"
                )
            ]
        )
        self.assertFalse(
            any("late" in finding.message for finding in deferred_body.findings)
        )
        self.assertFalse(
            any("item" in finding.message for finding in deferred_body.findings)
        )

        late_outer_iterator = self.report_for(
            [self.code("stream = (item for item in source)\nsource = []")]
        )
        self.assertTrue(
            any(
                finding.category == "definition-after-use"
                and "source" in finding.message
                for finding in late_outer_iterator.findings
            )
        )

    @unittest.skipUnless(
        hasattr(ast, "TypeAlias"), "requires Python 3.12+ type aliases"
    )
    def test_type_alias_binds_immediately_while_value_and_type_parameters_remain_lazy(
        self,
    ) -> None:
        path = self.write_notebook(
            {
                "cells": [
                    self.code(
                        "type Alias[T: Bound = Default] = Later\n"
                        "Later = int\n"
                        "Bound = object\n"
                        "Default = object\n"
                        "print(Alias)"
                    ),
                    self.code(
                        "class Holder:\n"
                        "    type Nested[U: NestedBound] = NestedLater\n"
                        "NestedBound = object\n"
                        "NestedLater = int\n"
                        "print(Holder.Nested)"
                    ),
                ]
            },
            "lazy-alias.ipynb",
        )
        report = notebook_order.check_notebook(path)
        self.assertEqual(report.definition_cells["Alias"], 1)
        self.assertEqual(report.definition_cells["Holder"], 2)
        for name in (
            "Later",
            "Bound",
            "Default",
            "NestedBound",
            "NestedLater",
        ):
            self.assertFalse(
                any(name in finding.message for finding in report.findings)
            )
        self.assertFalse(any("Alias" in finding.message for finding in report.findings))
        self.assertFalse(
            any("Nested" in finding.message for finding in report.findings)
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(path),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        cli_report = json.loads(result.stdout)["results"][0]["report"]
        self.assertEqual(cli_report["definition_cells"]["Alias"], 1)
        self.assertEqual(cli_report["definition_cells"]["Holder"], 2)
        self.assertFalse(
            any("Later" in finding["message"] for finding in cli_report["findings"])
        )

    @unittest.skipUnless(
        hasattr(ast, "TypeVar"), "requires Python 3.12+ type parameters"
    )
    def test_generic_class_and_function_type_parameters_are_private_and_ordered(
        self,
    ) -> None:
        path = self.write_notebook(
            {
                "cells": [
                    self.code("class Base[T]:\n    base_value = T"),
                    self.code("class Box[T](Base[T]):\n    value = T"),
                    self.code(
                        "class Outer[T]:\n"
                        "    outer_value = T\n"
                        "    class Nested[U](Base[U]):\n"
                        "        inner_value = U"
                    ),
                    self.code("print(Box)\nprint(Outer)\nprint(T)\nprint(U)"),
                ]
            },
            "generic-classes.ipynb",
        )
        report = notebook_order.check_notebook(path)
        self.assertEqual(report.definition_cells["Base"], 1)
        self.assertEqual(report.definition_cells["Box"], 2)
        self.assertEqual(report.definition_cells["Outer"], 3)
        self.assertNotIn("T", report.definition_cells)
        self.assertNotIn("U", report.definition_cells)
        for name in ("T", "U"):
            self.assertTrue(
                any(
                    finding.category == "unresolved-symbol"
                    and finding.cell == 4
                    and f"{name!r}" in finding.message
                    for finding in report.findings
                )
            )
            self.assertFalse(
                any(
                    finding.cell < 4 and f"{name!r}" in finding.message
                    for finding in report.findings
                )
            )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(path),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        cli_report = json.loads(result.stdout)["results"][0]["report"]
        self.assertEqual(cli_report["definition_cells"]["Box"], 2)
        self.assertFalse("T" in cli_report["definition_cells"])
        self.assertTrue(
            any(
                finding["category"] == "unresolved-symbol"
                and finding["cell"] == 4
                and "'T'" in finding["message"]
                for finding in cli_report["findings"]
            )
        )

        function_path = self.write_notebook(
            {
                "cells": [
                    self.code(
                        "def identity[T](value: T) -> T:\n"
                        "    return T\n"
                        "print(identity)\n"
                        "print(T)"
                    )
                ]
            },
            "generic-function.ipynb",
        )
        with mock.patch.object(notebook_order, "LAZY_ANNOTATIONS", False):
            eager_report = notebook_order.check_notebook(function_path)
        self.assertEqual(eager_report.definition_cells["identity"], 1)
        self.assertFalse(
            any(
                finding.category == "definition-after-use" and "'T'" in finding.message
                for finding in eager_report.findings
            )
        )
        self.assertTrue(
            any(
                finding.category == "unresolved-symbol"
                and finding.cell == 1
                and "'T'" in finding.message
                for finding in eager_report.findings
            )
        )

    def test_module_bindings_deletes_and_postponed_annotations_follow_static_boundary(
        self,
    ) -> None:
        report = self.report_for(
            [
                self.code(
                    "declared: int\n"
                    "print(declared)\n"
                    "alive = 1\n"
                    "del alive\n"
                    "print(alive)\n"
                    "result = (bound := factory())\n"
                    "print(bound)\n"
                    "for record in records:\n"
                    "    pass\n"
                    "print(record)\n"
                    "with manager() as handle:\n"
                    "    pass\n"
                    "print(handle)\n"
                    "factory = lambda: 1\n"
                    "records = []\n"
                    "manager = lambda: None"
                )
            ]
        )
        for name in ("factory", "records", "manager"):
            with self.subTest(name=name):
                self.assertTrue(
                    any(
                        finding.category == "definition-after-use"
                        and f"{name!r}" in finding.message
                        for finding in report.findings
                    )
                )
        self.assertTrue(
            any(
                finding.category == "unresolved-symbol"
                and "declared" in finding.message
                for finding in report.findings
            )
        )
        self.assertTrue(
            any(
                finding.category == "deleted-symbol" and "alive" in finding.message
                for finding in report.findings
            )
        )
        self.assertTrue(
            any(
                finding.category == "conditional-definition"
                and "record" in finding.message
                for finding in report.findings
            )
        )
        for name in ("bound", "handle"):
            self.assertFalse(
                any(f"{name!r}" in finding.message for finding in report.findings)
            )

        postponed = self.report_for(
            [
                self.code(
                    "from __future__ import annotations\n"
                    "def build(value: Later) -> Result:\n"
                    "    return value\n"
                    "Later = int\n"
                    "Result = int"
                )
            ]
        )
        self.assertFalse(
            any(
                "Later" in finding.message or "Result" in finding.message
                for finding in postponed.findings
            )
        )

        runtime_deferred = self.report_for(
            [
                self.code(
                    "module_value: LaterModule\n"
                    "class Model:\n"
                    "    class_value: LaterClass\n"
                    "LaterModule = int\n"
                    "LaterClass = int"
                )
            ]
        )
        for name in ("LaterModule", "LaterClass"):
            with self.subTest(name=name):
                annotation_finding = any(
                    finding.category == "definition-after-use"
                    and f"{name!r}" in finding.message
                    for finding in runtime_deferred.findings
                )
                self.assertEqual(
                    annotation_finding, not notebook_order.LAZY_ANNOTATIONS
                )

    def test_magics_metadata_syntax_and_star_import_have_explicit_routes(self) -> None:
        report = self.report_for(
            [
                self.code("%%sql\nselect 1"),
                self.code("select 1", metadata={"language": "sql"}),
                self.code("def :"),
                self.code("from package import *\nprint(unknown_symbol)"),
            ]
        )
        categories = self.categories(report)
        self.assertEqual(report.language_cells[1], "sql")
        self.assertEqual(report.language_cells[2], "sql")
        self.assertIn("parse", categories)
        self.assertIn("star-import", categories)
        self.assertIn("star-import-uncertain", categories)

    def test_install_restart_and_clean_python_order(self) -> None:
        report = self.report_for(
            [
                self.code("%pip install sample"),
                self.code("dbutils.library.restartPython()"),
                self.code("import sample\nsample.run()"),
            ]
        )
        self.assertFalse(
            any(
                finding.category == "install-without-restart"
                for finding in report.findings
            )
        )
        self.assertEqual(report.import_cells, [3])

    def test_mixed_install_lines_and_restarts_are_source_ordered(self) -> None:
        for install in ("%pip install demo", "!pip install demo"):
            with self.subTest(install=install):
                report = self.report_for(
                    [
                        self.code(
                            f"{install}\ndbutils.library.restartPython()\n"
                        )
                    ]
                )
                self.assertEqual(report.install_cells, [1])
                self.assertEqual(report.restart_cells, [1])
                self.assertEqual(report.first_non_setup_cell, 1)
                self.assertNotIn("install-without-restart", self.categories(report))

        late = self.report_for(
            [
                self.code(
                    "setup_value = 1\n%pip install demo\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertEqual(late.install_cells, [1])
        self.assertEqual(late.restart_cells, [1])
        self.assertEqual(late.first_non_setup_cell, 1)
        self.assertIn("install-after-code", self.categories(late))
        self.assertNotIn("install-without-restart", self.categories(late))

    def test_install_text_in_triple_strings_is_inert_on_closing_line(self) -> None:
        prefixes = {
            "ordinary": 'payload = """\n%pip install inert"""\n',
            "raw": 'payload = r"""\n!pip install inert"""\n',
            "bytes": 'payload = b"""\n%pip install inert"""\n',
            "f_string": 'payload = f"""\n%pip install {name}"""\nname = "x"\n',
            "generic_magic": 'payload = """\n%matplotlib inline"""\n',
        }
        for name, prefix in prefixes.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [
                        self.code(
                            f"{prefix}run()\ndef run():\n    return 1\n"
                        )
                    ]
                )
                self.assertEqual(report.install_cells, [])
                self.assertNotIn("install-without-restart", self.categories(report))
                self.assertIn("definition-after-use", self.categories(report))

    def test_cross_line_string_candidate_lookup_uses_indexed_operations(self) -> None:
        class CountingCandidates(set[int]):
            def __init__(self, values: set[int]) -> None:
                super().__init__(values)
                self.iterated = 0
                self.membership_checks = 0

            def __iter__(self):
                for value in super().__iter__():
                    self.iterated += 1
                    yield value

            def __contains__(self, value: object) -> bool:
                self.membership_checks += 1
                return super().__contains__(value)

        blocks: list[str] = []
        candidate_lines: set[int] = set()
        string_count = 256
        line_number = 1
        for index in range(string_count):
            blocks.append(f'value_{index} = """line one\nline two\n"""\n')
            line_number += 3
            if index % 4 == 0:
                blocks.append("%matplotlib inline\n")
                candidate_lines.add(line_number)
                line_number += 1
        source = "".join(blocks)
        candidates = CountingCandidates(candidate_lines)

        covered = notebook_order._original_cross_line_string_lines(
            source, candidates
        )
        normalized = notebook_order.normalize_python_cell(source)

        self.assertEqual(covered, set())
        self.assertEqual(candidates.iterated, len(candidate_lines))
        self.assertLessEqual(candidates.membership_checks, string_count)
        self.assertEqual(len(normalized.mapped_lines), len(candidate_lines))
        self.assertEqual(normalized.unsafe_install_lines, frozenset())

    def test_inert_closing_line_does_not_poison_following_real_install(self) -> None:
        prefixes = {
            "ordinary": 'payload = """\n%pip install inert"""\n',
            "raw": 'payload = r"""\n%pip install inert"""\n',
            "bytes": 'payload = b"""\n%pip install inert"""\n',
            "f_string": 'payload = f"""\n%pip install inert"""\n',
        }
        for name, prefix in prefixes.items():
            with self.subTest(name=name):
                report = self.report_for(
                    [
                        self.code(
                            f"{prefix}%pip install real\n"
                            "dbutils.library.restartPython()\n"
                        )
                    ]
                )
                self.assertEqual(report.install_cells, [1])
                self.assertEqual(report.restart_cells, [1])
                self.assertEqual(report.definition_cells["payload"], 1)
                self.assertIn("install-after-code", self.categories(report))
                self.assertNotIn("unsupported-install-order", self.categories(report))
                self.assertNotIn("install-without-restart", self.categories(report))

    def test_opaque_command_quotes_do_not_change_python_lexical_state(self) -> None:
        report = self.report_for(
            [
                self.code(
                    '%pip install "demo[extra]"\n'
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertEqual(report.install_cells, [1])
        self.assertEqual(report.restart_cells, [1])
        self.assertNotIn("install-without-restart", self.categories(report))

        quote_origin_source = (
            '%pip install """\n%pip install second\n"""\n'
            "dbutils.library.restartPython()\n"
        )
        quote_normalized = notebook_order.normalize_python_cell(quote_origin_source)
        self.assertEqual(
            [
                mapped.line
                for mapped in quote_normalized.mapped_lines
                if mapped.install
            ],
            [1, 2],
        )
        quote_origin = self.report_for([self.code(quote_origin_source)])
        self.assertEqual(quote_origin.install_cells, [1])
        self.assertEqual(quote_origin.restart_cells, [])
        self.assertIn("parse", self.categories(quote_origin))
        self.assertIn("install-without-restart", self.categories(quote_origin))

        unsafe = self.report_for([self.code('%pip install "demo;quoted"\n')])
        self.assertEqual(unsafe.install_cells, [1])
        self.assertIn("unsupported-install-order", self.categories(unsafe))
        self.assertIn("install-without-restart", self.categories(unsafe))

    def test_exact_casefold_install_forms_drop_accidental_prefixes(self) -> None:
        supported = (
            "%PIP install demo",
            "!PIP install demo",
            "PIP INSTALL demo",
            "PYTHON -M PIP install demo",
        )
        for install in supported:
            with self.subTest(install=install):
                report = self.report_for(
                    [
                        self.code(
                            f"{install}\ndbutils.library.restartPython()\n"
                        )
                    ]
                )
                self.assertEqual(report.install_cells, [1])
                self.assertEqual(report.restart_cells, [1])

        opaque = self.report_for(
            [
                self.code("%pipette install demo\n"),
                self.code("!pipette install demo\n"),
                self.code("%uv pip install demo\n"),
            ]
        )
        self.assertEqual(opaque.install_cells, [])
        self.assertNotIn("install-without-restart", self.categories(opaque))

        for source in ("pip installx demo\n", "python -m pipx install demo\n"):
            with self.subTest(source=source):
                parsed = self.report_for([self.code(source)])
                self.assertEqual(parsed.install_cells, [])
                self.assertIn("parse", self.categories(parsed))

    def test_placeholders_preserve_lines_and_do_not_become_real_code(self) -> None:
        source = (
            "\n"
            "%python\n"
            "%pip install one\n"
            "value = (\n"
            "    1 +\n"
            "    2\n"
            ")\n"
            "dbutils.library.restartPython()\n"
        )
        normalized = notebook_order.normalize_python_cell(source)
        tree = ast.parse(normalized.source)
        self.assertEqual(len(normalized.source), len(source))
        self.assertEqual(
            len(normalized.source.splitlines()), len(source.splitlines())
        )
        self.assertEqual(
            [statement.lineno for statement in tree.body], [2, 3, 4, 8]
        )
        report = self.report_for([self.code(source)])
        self.assertEqual(report.install_cells, [1])
        self.assertEqual(report.restart_cells, [1])
        self.assertEqual(report.first_non_setup_cell, 1)
        self.assertNotIn("install-after-code", self.categories(report))

        user_pass = self.report_for(
            [
                self.code(
                    "pass\n%pip install demo\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertIn("install-after-code", self.categories(user_pass))

    def test_mapped_pass_resource_accounting_keeps_genuine_limits(self) -> None:
        install = self.write_notebook(
            {"cells": [self.code("%pip install demo\n")]},
            "install-resource.ipynb",
        )
        install_report = notebook_order.check_notebook(
            install,
            limits=notebook_order.Limits(max_ast_nodes=1, max_ast_depth=1),
        )
        self.assertNotIn("ast_node_estimate_limit", self.categories(install_report))
        self.assertNotIn("ast_node_limit", self.categories(install_report))
        self.assertNotIn("ast_depth_limit", self.categories(install_report))
        self.assertIn("install-without-restart", self.categories(install_report))

        real_pass = self.write_notebook(
            {"cells": [self.code("pass\n")]},
            "real-pass-resource.ipynb",
        )
        pass_report = notebook_order.check_notebook(
            real_pass,
            limits=notebook_order.Limits(max_ast_nodes=10, max_ast_depth=1),
        )
        self.assertIn("ast_depth_limit", self.categories(pass_report))

        genuine = self.write_notebook(
            {"cells": [self.code("%pip install demo\nvalue = 1\n")]},
            "genuine-resource.ipynb",
        )
        genuine_report = notebook_order.check_notebook(
            genuine,
            limits=notebook_order.Limits(max_ast_nodes=1, max_ast_depth=8),
        )
        self.assertTrue(
            {"ast_node_estimate_limit", "ast_node_limit"}.intersection(
                self.categories(genuine_report)
            )
        )

    def test_one_analyzer_retains_order_and_restart_integrity_across_install(self) -> None:
        ordered = self.report_for(
            [
                self.code(
                    "run()\n%pip install demo\ndef run():\n    return 1\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertIn("definition-after-use", self.categories(ordered))

        rebound = self.report_for(
            [
                self.code(
                    "dbutils.library = replacement\n%pip install demo\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertEqual(rebound.restart_cells, [])
        self.assertIn("install-without-restart", self.categories(rebound))

        function = self.report_for(
            [
                self.code(
                    "def run():\n    return LATER\n%pip install demo\nrun()\n"
                    "LATER = 1\ndbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertIn("direct-function-global-order", self.categories(function))

        deleted = self.report_for(
            [
                self.code(
                    "value = 1\ndel value\n%pip install demo\nprint(value)\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertIn("deleted-symbol", self.categories(deleted))

    def test_repeated_install_order_and_both_restart_families(self) -> None:
        for restart in (
            "dbutils.library.restartPython()",
            "get_ipython().kernel.do_shutdown(restart=True)",
        ):
            with self.subTest(restart=restart):
                report = self.report_for(
                    [self.code(f"%pip install one\n!pip install two\n{restart}\n")]
                )
                self.assertEqual(report.install_cells, [1])
                self.assertEqual(report.restart_cells, [1])
                self.assertNotIn("install-after-code", self.categories(report))
                self.assertNotIn("install-without-restart", self.categories(report))

        code_between = self.report_for(
            [
                self.code(
                    "%pip install one\npass\n!pip install two\n"
                    "dbutils.library.restartPython()\n"
                )
            ]
        )
        self.assertIn("install-after-code", self.categories(code_between))
        self.assertNotIn("install-without-restart", self.categories(code_between))

        restart_first = self.report_for(
            [
                self.code(
                    "dbutils.library.restartPython()\n%pip install unreachable\n"
                )
            ]
        )
        self.assertEqual(restart_first.install_cells, [])
        self.assertEqual(restart_first.restart_cells, [1])

    def test_parse_and_resource_failures_keep_install_pending_without_restart(self) -> None:
        malformed = self.report_for(
            [
                self.code(
                    "%pip install demo\ndbutils.library.restartPython()\n"
                    "if True print('bad')\n"
                )
            ]
        )
        self.assertEqual(malformed.install_cells, [1])
        self.assertEqual(malformed.restart_cells, [])
        self.assertIn("parse", self.categories(malformed))
        self.assertIn("install-without-restart", self.categories(malformed))

        resource_path = self.write_notebook(
            {
                "cells": [
                    self.code(
                        "%pip install demo\ndbutils.library.restartPython()\n"
                        + "not " * 20
                        + "value\n"
                    )
                ]
            },
            "resource-tail.ipynb",
        )
        resource = notebook_order.check_notebook(
            resource_path,
            limits=notebook_order.Limits(max_ast_depth=5),
        )
        self.assertEqual(resource.install_cells, [1])
        self.assertEqual(resource.restart_cells, [])
        self.assertIn("ast_operator_chain_limit", self.categories(resource))
        self.assertIn("install-without-restart", self.categories(resource))

    def test_unsafe_install_order_warns_and_can_restart_later(self) -> None:
        unsafe_sources = (
            "value = (\n%pip install demo\n)\n",
            "value = 1 + \\\n%pip install demo\n",
            "if True:\n    %pip install demo\n",
            "%pip install demo; echo done\n",
        )
        for source in unsafe_sources:
            with self.subTest(source=source):
                report = self.report_for([self.code(source)])
                self.assertEqual(report.install_cells, [1])
                self.assertEqual(
                    self.categories(report).count("unsupported-install-order"), 1
                )
                self.assertIn("install-without-restart", self.categories(report))

        restarted = self.report_for(
            [
                self.code("value = (\n%pip install demo\n)\n"),
                self.code("dbutils.library.restartPython()\n"),
            ]
        )
        self.assertIn("unsupported-install-order", self.categories(restarted))
        self.assertNotIn("install-without-restart", self.categories(restarted))

    def test_unsupported_install_warning_keeps_cli_schema_and_exit_policy(self) -> None:
        secret = "private-package-argument"
        path = self.write_notebook(
            {
                "cells": [
                    self.code(f"%pip install demo; echo {secret}\n")
                ]
            },
            "unsupported-install.ipynb",
        )
        payloads: list[tuple[int, dict[str, object], str]] = []
        for warnings_fail in (False, True):
            command = [
                sys.executable,
                "-B",
                str(SCRIPTS / "check_notebook_runtime_order.py"),
                str(path),
                "--json",
            ]
            if warnings_fail:
                command.append("--warnings-fail")
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            payloads.append(
                (completed.returncode, json.loads(completed.stdout), completed.stderr)
            )

        self.assertEqual([item[0] for item in payloads], [0, 1])
        self.assertEqual(
            payloads[0][1]["results"][0]["report"],
            payloads[1][1]["results"][0]["report"],
        )
        report = payloads[0][1]["results"][0]["report"]
        self.assertEqual(payloads[0][1]["schema_version"], "1.0")
        self.assertTrue(
            any(
                finding["category"] == "unsupported-install-order"
                for finding in report["findings"]
            )
        )
        self.assertNotIn(secret, json.dumps(payloads))
        self.assertEqual([item[2] for item in payloads], ["", ""])

    def test_language_routing_and_generic_opaque_lines_remain_unchanged(self) -> None:
        report = self.report_for(
            [
                self.code("%pip install ignored", metadata={"language": "sql"}),
                self.code("%%sql\n%pip install ignored"),
                self.code("%matplotlib inline\nvalue = 1\n"),
                self.code("-- MAGIC %python\nother = 2\n"),
            ]
        )
        self.assertEqual(report.language_cells, {1: "sql", 2: "sql"})
        self.assertEqual(report.install_cells, [])
        self.assertEqual(report.definition_cells["value"], 3)
        self.assertEqual(report.definition_cells["other"], 4)
        self.assertNotIn("parse", self.categories(report))

    def test_bounds_shape_duplicate_and_nonfinite_errors_are_classified(self) -> None:
        dense = self.write_notebook({"cells": [self.code("x = 1"), self.code("y = 2")]})
        with self.assertRaisesRegex(notebook_order.NotebookInputError, "cell limit"):
            notebook_order.check_notebook(
                dense, limits=notebook_order.Limits(max_cells=1)
            )
        oversized = self.write_notebook(
            {"cells": [self.code("x = '" + "a" * 200 + "'")]}
        )
        with self.assertRaises(notebook_order.NotebookInputError) as byte_error:
            notebook_order.check_notebook(
                oversized, limits=notebook_order.Limits(max_bytes=32)
            )
        self.assertEqual(byte_error.exception.code, "byte_limit")
        line_limited = self.write_notebook({"cells": [self.code("x = 1\ny = 2")]})
        with self.assertRaises(notebook_order.NotebookInputError) as line_error:
            notebook_order.check_notebook(
                line_limited, limits=notebook_order.Limits(max_cell_lines=1)
            )
        self.assertEqual(line_error.exception.code, "cell_line_limit")
        deep = self.write_notebook({"cells": [self.code("(" * 20 + "x" + ")" * 20)]})
        deep_report = notebook_order.check_notebook(
            deep, limits=notebook_order.Limits(max_ast_depth=5)
        )
        self.assertIn("ast_depth_limit", self.categories(deep_report))
        unary_chain = self.write_notebook(
            {"cells": [self.code("not " * 3_000 + "value")]}
        )
        unary_report = notebook_order.check_notebook(
            unary_chain, limits=notebook_order.Limits(max_ast_depth=64)
        )
        self.assertIn("ast_operator_chain_limit", self.categories(unary_report))
        malformed = Path(tempfile.mkdtemp()) / "broken.ipynb"
        malformed.write_text("{", encoding="utf-8")
        with self.assertRaises(notebook_order.NotebookInputError) as invalid:
            notebook_order.check_notebook(malformed)
        self.assertEqual(invalid.exception.code, "invalid_json")
        shape = self.write_notebook({"cells": {"not": "an array"}})
        with self.assertRaises(notebook_order.NotebookInputError) as shape_error:
            notebook_order.check_notebook(shape)
        self.assertEqual(shape_error.exception.code, "invalid_shape")
        invalid_utf8 = Path(tempfile.mkdtemp()) / "invalid-utf8.ipynb"
        invalid_utf8.write_bytes(b"\xff\xfe")
        with self.assertRaises(notebook_order.NotebookInputError) as utf8_error:
            notebook_order.check_notebook(invalid_utf8)
        self.assertEqual(utf8_error.exception.code, "invalid_utf8")
        duplicate = self.write_notebook(
            {"cells": [self.code("x = 1", id="same"), self.code("y = 1", id="same")]}
        )
        with self.assertRaises(notebook_order.NotebookInputError) as duplicate_error:
            notebook_order.check_notebook(duplicate)
        self.assertEqual(duplicate_error.exception.code, "duplicate_cell_id")
        nonfinite = Path(tempfile.mkdtemp()) / "nonfinite.ipynb"
        nonfinite.write_text('{"cells": [], "value": NaN}', encoding="utf-8")
        with self.assertRaises(notebook_order.NotebookInputError) as nonfinite_error:
            notebook_order.check_notebook(nonfinite)
        self.assertEqual(nonfinite_error.exception.code, "invalid_json")

    def test_batch_json_returns_each_file_and_aggregate_without_traceback(self) -> None:
        good = self.write_notebook({"cells": [self.code("value = 1")]}, "good.ipynb")
        bad = Path(tempfile.mkdtemp()) / "bad.ipynb"
        bad.write_text("{", encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            str(SCRIPTS / "check_notebook_runtime_order.py"),
            str(good),
            str(bad),
            "--json",
        ]
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, encoding="utf-8"
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["aggregate"]["files"], 2)
        self.assertEqual(payload["aggregate"]["failed_files"], 1)
        self.assertEqual(len(payload["results"]), 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_direct_synchronous_function_call_warns_for_later_global_across_cells(self) -> None:
        report = self.report_for(
            [
                self.code(
                    "STATE = 2\ndef run_cell_011():\n    if STATE == 2:\n        return OUTPUT_TABLE\n    return None\n"
                ),
                self.code("run_cell_011()\n"),
                self.code("OUTPUT_TABLE = 'x'\n"),
            ]
        )

        findings = [
            finding
            for finding in report.findings
            if finding.category == "direct-function-global-order"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")
        self.assertEqual(findings[0].cell, 2)
        self.assertIn("run_cell_011", findings[0].message)
        self.assertIn("OUTPUT_TABLE", findings[0].message)

    def test_direct_function_ignores_local_annotations_but_keeps_values_and_complex_targets(self) -> None:
        report = self.report_for(
            [
                self.code(
                    "def run_cell_011():\n"
                    "    scratch: OUTPUT_TABLE\n"
                    "    cached: OTHER = 1\n"
                    "    result: ANNOTATION = RHS\n"
                    "    holder[INDEX]: TYPE = VALUE\n"
                    "    return 1\n"
                    "run_cell_011()\n"
                )
            ]
        )

        messages = [
            finding.message
            for finding in report.findings
            if finding.category == "direct-function-global-order"
        ]
        for name in ("RHS", "holder", "INDEX", "VALUE"):
            with self.subTest(required=name):
                self.assertTrue(any(name in message for message in messages))
        for name in ("OUTPUT_TABLE", "OTHER", "ANNOTATION", "TYPE"):
            with self.subTest(annotation_only=name):
                self.assertFalse(any(name in message for message in messages))

    def test_direct_function_global_analysis_excludes_locals_and_unverified_calls(self) -> None:
        report = self.report_for(
            [
                self.code(
                    "from contextlib import nullcontext\nOUTPUT_TABLE = 'ready'\n"
                    "def parameter(OUTPUT_TABLE):\n    return OUTPUT_TABLE\n"
                    "def local_value():\n    OUTPUT_TABLE = 'local'\n    return OUTPUT_TABLE\n"
                    "def comprehension():\n    return [OUTPUT_TABLE for OUTPUT_TABLE in ['local']]\n"
                    "def lexical_scopes():\n    import os as OUTPUT_TABLE\n    try:\n        pass\n    except Exception as ERROR:\n        OUTPUT_TABLE = ERROR\n    for OUTPUT_TABLE in []:\n        pass\n    with nullcontext() as OUTPUT_TABLE:\n        pass\n    return OUTPUT_TABLE\n"
                    "def walrus():\n    return (OUTPUT_TABLE := 'walrus')\n"
                    "def global_write():\n    global OUTPUT_TABLE\n    OUTPUT_TABLE = 'created'\n    return OUTPUT_TABLE\n"
                    "def nested():\n    return inner()\n"
                    "def inner():\n    return LATER\n"
                    "def generator():\n    yield LATER\n"
                    "async def asynchronous():\n    return LATER\n"
                    "def decorator(function):\n    return function\n"
                    "@decorator\ndef decorated():\n    return LATER\n"
                    "def dynamic():\n    return globals()['LATER']\n"
                    "def aliased():\n    return LATER\n"
                    "def recursive():\n    return recursive()\n"
                    "def argument_rebind():\n    return LATER\n"
                    "parameter('value')\nlocal_value()\ncomprehension()\nlexical_scopes()\nwalrus()\nglobal_write()\n"
                    "nested()\ngenerator()\nasynchronous()\ndecorated()\ndynamic()\n"
                    "alias = aliased\nalias()\nrecursive()\nargument_rebind((argument_rebind := lambda: None))\n"
                )
            ]
        )

        self.assertFalse(
            any(
                finding.category == "direct-function-global-order"
                for finding in report.findings
            )
        )

    def test_direct_function_summary_is_invalidated_by_rebind_delete_and_restart(self) -> None:
        report = self.report_for(
            [
                self.code(
                    "def rebindable():\n    return LATER\n"
                    "rebindable = lambda: None\nrebindable()\n"
                    "def deleted():\n    return LATER\ndel deleted\n"
                ),
                self.code(
                    "def reset_after_restart():\n    return LATER\n"
                    "dbutils.library.restartPython()\n"
                ),
                self.code("reset_after_restart()\n"),
            ]
        )

        self.assertFalse(
            any(
                finding.category == "direct-function-global-order"
                for finding in report.findings
            )
        )


class CheatsheetTests(unittest.TestCase):
    def test_quick_fixes_are_inspection_first_and_none_pattern_matches(self) -> None:
        asset = json.loads(
            (
                Path(__file__).resolve().parents[1] / "assets" / "error-cheatsheet.json"
            ).read_text(encoding="utf-8")
        )
        common = asset["common_errors"]
        self.assertRegex(
            "AttributeError: 'NoneType' object has no attribute 'id'",
            common["AttributeError_NoneType"]["pattern"],
        )
        fixes = " ".join(entry.get("quick_fix", "") for entry in common.values())
        self.assertNotIn("hasattr", fixes)
        self.assertNotIn("errors='replace'", fixes)
        self.assertNotIn("dict.get(key, default)", fixes)
        self.assertIn("Inspect", common["KeyError"]["quick_fix"])
        unpack_fix = common["ValueError_unpack"]["quick_fix"]
        self.assertIn("Inspect", unpack_fix)
        self.assertNotIn("slice", unpack_fix.lower())
        self.assertNotIn("*rest", unpack_fix)


if __name__ == "__main__":
    unittest.main()
