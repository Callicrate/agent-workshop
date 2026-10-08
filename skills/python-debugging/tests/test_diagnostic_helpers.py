"""Regression coverage for privacy-safe Python diagnostic helpers."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_traceback  # noqa: E402
import check_notebook_runtime_order as notebook_order  # noqa: E402
from diagnostic_safety import error_envelope, sanitize_text  # noqa: E402


class DiagnosticSafetyTests(unittest.TestCase):
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
