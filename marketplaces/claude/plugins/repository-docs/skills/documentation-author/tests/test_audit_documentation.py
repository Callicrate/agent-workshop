"""Behavioral tests for the bounded documentation audit helper."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_documentation.py"
SPEC = importlib.util.spec_from_file_location("audit_documentation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuditDocumentationTests(unittest.TestCase):
    def _write(self, root: Path, name: str, contents: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_code_mask_handles_many_unmatched_backtick_lengths(self) -> None:
        source = "".join("`" * length + "x" for length in range(1, 500))
        self.assertEqual(source, MODULE._mask_code(source))

    def test_markdown_subset_routes_links_and_masks_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in (
                "docs/child(a).md",
                "docs/ref.md",
                "docs/image.md",
                "docs/escaped(name).md",
            ):
                self._write(root, path, "# target\n")
            self._write(
                root,
                "README.md",
                """# Docs
[inline](docs/child(a).md "A title")
![image](<docs/image.md> 'image title')
![escaped](<docs/escaped\\(name\\).md> "escaped title")
[full][named] [collapsed][] [short]
[named]: <docs/ref.md> "full title"
[collapsed]: docs/ref.md
[short]: docs/ref.md
[fragment](#local)
[outside](../outside.md)
[external](https://example.invalid/a?token=secret)
`[ignored](../ignored.md)`
~~~markdown
[hidden](../hidden.md)
~~~
```
[also-hidden](../also-hidden.md)
```
""",
            )
            report = MODULE.build_report(root.resolve())
            links = report["markdown_links"]
            statuses = {item["status"] for item in links}
            self.assertTrue(
                {"ok", "fragment", "outside_root", "external"}.issubset(statuses)
            )
            self.assertTrue(
                all(set(item) == {"ordinal", "source", "status"} for item in links)
            )
            rendered = json.dumps(report, sort_keys=True)
            self.assertNotIn("https://", rendered)
            self.assertNotIn("secret", rendered)
            self.assertNotIn("ignored.md", rendered)
            self.assertNotIn("also-hidden.md", rendered)

    def test_unmatched_bracket_scaling_and_one_mib_cap(self) -> None:
        def elapsed(count: int, repeats: int = 12) -> float:
            source = "[" * count
            start = time.perf_counter()
            for _ in range(repeats):
                self.assertEqual([], MODULE._markdown_destinations(source))
            return time.perf_counter() - start

        timings = [elapsed(count) for count in (1_000, 2_000, 4_000)]
        self.assertLess(timings[1], timings[0] * 3.3)
        self.assertLess(timings[2], timings[1] * 3.3)
        start = time.perf_counter()
        self.assertEqual([], MODULE._markdown_destinations("[" * MODULE.MAX_FILE_BYTES))
        self.assertLess(time.perf_counter() - start, 2.5)

    def test_walk_is_casefold_bounded_and_deterministic(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary)
            outside = Path(outside_temporary)
            self._write(root, "README.md", "# visible\n")
            self._write(root, "second.md", "# second\n")
            self._write(root, "NODE_MODULES/hidden.md", "# hidden\n")
            self._write(outside, "outside.md", "# outside\n")
            try:
                os.symlink(outside, root / "junction", target_is_directory=True)
            except (NotImplementedError, OSError):
                pass
            first = MODULE.build_report(root.resolve())
            second = MODULE.build_report(root.resolve())
            self.assertEqual(first, second)
            self.assertEqual(["README.md", "second.md"], first["existing_markdown"])
            with patch.object(MODULE, "MAX_FILES", 1):
                count_capped = MODULE.build_report(root.resolve())
            self.assertEqual(
                [{"path": ".", "code": "file_limit", "count": 1}],
                count_capped["scan_errors"],
            )
            with patch.object(MODULE, "MAX_FILE_BYTES", 3):
                capped = MODULE.build_report(root.resolve())
            self.assertEqual(
                [
                    {
                        "path": "README.md",
                        "code": "file_too_large",
                        "count": 2,
                    },
                ],
                capped["scan_errors"],
            )

    def test_known_paths_are_actionable_and_repeated_scan_errors_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(root, "docs/guide.md", "[missing](missing.md)\n")
            self._write(root, "notes.ipynb", '{"cells": []}')
            report = MODULE.build_report(root.resolve())
            self.assertEqual(["docs/guide.md"], report["existing_markdown"])
            self.assertEqual("docs/guide.md", report["markdown_links"][0]["source"])
            self.assertEqual("notes.ipynb", report["notebook_summaries"][0]["path"])
            with patch.object(MODULE, "MAX_FILE_BYTES", 1):
                capped = MODULE.build_report(root.resolve())
            self.assertEqual(
                [
                    {
                        "path": "docs/guide.md",
                        "code": "file_too_large",
                        "count": 2,
                    }
                ],
                capped["scan_errors"],
            )

    def test_scan_error_prefers_a_safe_path_over_an_unsafe_first_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(root, "access_token=private.md", "oversized")
            self._write(root, "docs/guide.md", "oversized")
            with patch.object(MODULE, "MAX_FILE_BYTES", 1):
                report = MODULE.build_report(root.resolve())
            self.assertEqual(
                [
                    {
                        "path": "docs/guide.md",
                        "code": "file_too_large",
                        "count": 2,
                    }
                ],
                report["scan_errors"],
            )
            output = StringIO()
            with redirect_stdout(output):
                MODULE.print_markdown_report(report)
            self.assertIn(
                "docs/guide.md`: file\\_too\\_large (count=2)", output.getvalue()
            )

    def test_markdown_paths_preserve_punctuation_and_backticks(self) -> None:
        self.assertEqual("`docs/[guide].md`", MODULE._markdown_code_span("docs/[guide].md"))
        self.assertEqual("`` `guide`.md ``", MODULE._markdown_code_span("`guide`.md"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(root, "docs/[guide].md", "[missing](missing.md)\n")
            self._write(root, "docs/`guide`.md", "[missing](missing.md)\n")
            report = MODULE.build_report(root.resolve())
            output = StringIO()
            with redirect_stdout(output):
                MODULE.print_markdown_report(report)
            rendered = output.getvalue()
            self.assertIn("`docs/[guide].md` link #1", rendered)
            self.assertIn("``docs/`guide`.md`` link #1", rendered)

    def test_strict_file_and_notebook_errors_have_stable_json_and_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.md").write_bytes(b"# invalid\xff")
            (root / "nul.md").write_bytes(b"# invalid\x00")
            (root / "control.md").write_bytes(b"# invalid\x01")
            self._write(root, "json.ipynb", "{")
            self._write(root, "root.ipynb", "[]")
            self._write(root, "cells.ipynb", "{}")
            self._write(root, "cell.ipynb", '{"cells": ["not an object"]}')
            report = MODULE.build_report(root.resolve())
            self.assertEqual(
                [
                    {"path": "bad.md", "code": "invalid_utf8"},
                    {"path": "control.md", "code": "binary_or_nul"},
                    {"path": "nul.md", "code": "binary_or_nul"},
                    {"path": "cell.ipynb", "code": "invalid_notebook_cell"},
                    {"path": "cells.ipynb", "code": "invalid_notebook_cells"},
                    {"path": "json.ipynb", "code": "invalid_notebook_json"},
                    {"path": "root.ipynb", "code": "invalid_notebook_root"},
                ],
                report["file_errors"],
            )
            output = StringIO()
            with (
                patch("sys.argv", ["audit_documentation.py", str(root), "--json"]),
                redirect_stdout(output),
            ):
                exit_code = MODULE.main()
            self.assertEqual(1, exit_code)
            self.assertNotIn(str(root), output.getvalue())

    def test_read_errors_are_stable_and_do_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(root, "README.md", "# docs\n")
            with patch.object(MODULE, "_read_utf8", return_value=(None, "read_error")):
                report = MODULE.build_report(root.resolve())
            self.assertEqual(
                [{"path": "README.md", "code": "read_error"}], report["file_errors"]
            )

    def test_recursive_redaction_and_human_markdown_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            github = "ghp_" + "z" * 16
            self._write(
                root,
                "README.md",
                f"[token](https://example.invalid/{github})\n[missing](unsafe[name].md)\n",
            )
            report = MODULE.build_report(root.resolve())
            json_report = json.dumps(report, sort_keys=True)
            self.assertNotIn(github, json_report)
            output = StringIO()
            with redirect_stdout(output):
                MODULE.print_markdown_report(report)
            self.assertNotIn(github, output.getvalue())
            self.assertNotIn("unsafe[name].md", output.getvalue())
            self.assertIn("README.md` link #2", output.getvalue())

    def test_json_envelope_covers_usage_preflight_and_content_errors(self) -> None:
        sentinel = "SENTINEL_JSON_41827"
        cases: list[tuple[list[str], int]] = [
            (["--json"], 2),
            ([str(Path("missing") / f"client_secret={sentinel}"), "--json"], 1),
        ]
        for arguments, expected_exit in cases:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main(arguments)
            envelope = json.loads(output.getvalue())
            self.assertEqual(expected_exit, exit_code)
            self.assertEqual({"error", "ok", "result"}, set(envelope))
            self.assertFalse(envelope["ok"])
            self.assertNotIn(sentinel, json.dumps(envelope))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.md").write_bytes(b"# invalid\xff")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root), "--json"])
            envelope = json.loads(output.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual("audit_failed", envelope["error"]["code"])
            self.assertEqual(
                "invalid_utf8", envelope["result"]["file_errors"][0]["code"]
            )

    def test_destinations_and_secret_variants_never_reach_json(self) -> None:
        sentinel = "SENTINEL_PRIVATE_62094"
        variants = [
            f"access_token={sentinel}",
            f"REFRESH-TOKEN: {sentinel}",
            f"client.secret={sentinel}",
            f"X-API-Key={sentinel}",
            f"password={sentinel}",
            f"X-Signature={sentinel}",
            f"session_id={sentinel}",
            f"oauth-code={sentinel}",
            f"code={sentinel}",
            f"access%5Ftoken%3D{sentinel}",
            f"Authorization: Bearer {sentinel}\r\nInjected: yes",
        ]
        for value in variants:
            self.assertNotIn(sentinel, MODULE._redact_text(value))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destinations = "\n".join(
                f"[private-{index}](https://example.invalid/?{value})"
                for index, value in enumerate(variants)
            )
            self._write(root, "README.md", f"# Docs\n{destinations}\n")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root), "--json"])
            envelope = json.loads(output.getvalue())
            self.assertEqual(0, exit_code)
            self.assertNotIn(sentinel, json.dumps(envelope))
            self.assertTrue(
                all(
                    set(item) == {"ordinal", "source", "status"}
                    for item in envelope["result"]["markdown_links"]
                )
            )

    def test_output_privacy_handles_unicode_controls_and_unstable_encoding(
        self,
    ) -> None:
        sentinel = "SENTINEL_UNICODE_PRIVATE_35401"
        variants = [
            f"access＿token＝{sentinel}",
            f"access\u2066_token={sentinel}",
            f"access%2525255Ftoken%2525253D{sentinel}",
        ]
        for value in variants:
            self.assertNotIn(sentinel, MODULE._redact_text(value))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filename = f"access＿token＝{sentinel}.md"
            self._write(root, filename, "# Docs\n")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root), "--json"])
            rendered = output.getvalue()
            self.assertEqual(0, exit_code)
            self.assertNotIn(sentinel, rendered)
            self.assertNotIn(filename, rendered)
            self.assertIn("markdown-1", rendered)
            machine_user = "MACHINE_USER_35401"
            output = StringIO()
            with patch.dict(os.environ, {"USERNAME": machine_user}):
                with redirect_stdout(output):
                    exit_code = MODULE.main(
                        [
                            str(Path("missing") / f"access＿token＝{sentinel}"),
                            "--json",
                        ]
                    )
            self.assertEqual(1, exit_code)
            self.assertNotIn(sentinel, output.getvalue())
            self.assertNotIn(machine_user, output.getvalue())

    def test_notebook_limits_duplicate_keys_and_json_envelope(self) -> None:
        def notebook_error(contents: str) -> str:
            _, issue = MODULE._summarize_notebook(
                Path("unused.ipynb"), "notebook-1", contents
            )
            self.assertIsNotNone(issue)
            assert issue is not None
            return issue.code

        self.assertEqual(
            "notebook_integer_digit_limit",
            notebook_error('{"cells": [], "count": ' + "9" * 10_000 + "}"),
        )
        at_limit = MODULE.MAX_NOTEBOOK_NESTING - 2
        self.assertIsNone(
            MODULE._summarize_notebook(
                Path("unused.ipynb"),
                "notebook-1",
                '{"cells": [], "metadata": '
                + "[" * at_limit
                + "0"
                + "]" * at_limit
                + "}",
            )[1]
        )
        self.assertEqual(
            "notebook_nesting_limit",
            notebook_error(
                '{"cells": [], "metadata": '
                + "[" * (at_limit + 1)
                + "0"
                + "]" * (at_limit + 1)
                + "}"
            ),
        )
        at_list_limit = ",".join(
            '{"cell_type": "code"}' for _ in range(MODULE.MAX_NOTEBOOK_LIST_ITEMS)
        )
        self.assertIsNone(
            MODULE._summarize_notebook(
                Path("unused.ipynb"), "notebook-1", '{"cells": [' + at_list_limit + "]}"
            )[1]
        )
        self.assertEqual(
            "notebook_list_limit",
            notebook_error('{"cells": [' + at_list_limit + ', {"cell_type": "code"}]}'),
        )
        with patch.object(MODULE, "MAX_NOTEBOOK_BYTES", 10):
            self.assertEqual("notebook_byte_limit", notebook_error('{"cells": []}'))
        with patch.object(MODULE, "MAX_NOTEBOOK_STRING_CHARS", 3):
            self.assertEqual(
                "notebook_string_limit",
                notebook_error('{"cells": [], "metadata": "four"}'),
            )
        with patch.object(MODULE, "MAX_NOTEBOOK_MAP_ITEMS", 2):
            self.assertEqual(
                "notebook_map_limit",
                notebook_error('{"cells": [], "metadata": {}, "nbformat": 4}'),
            )
        with patch.object(MODULE, "MAX_NOTEBOOK_NODES", 3):
            self.assertEqual(
                "notebook_node_limit",
                notebook_error('{"cells": [], "metadata": 1, "nbformat": 4}'),
            )
        with patch.object(MODULE, "MAX_NOTEBOOK_LIST_ITEMS", 2):
            self.assertIsNone(
                MODULE._summarize_notebook(
                    Path("unused.ipynb"),
                    "notebook-1",
                    '{"cells": [{"cell_type": "code"}, {"cell_type": "markdown"}]}',
                )[1]
            )
            self.assertEqual(
                "notebook_list_limit",
                notebook_error(
                    '{"cells": [{"cell_type": "code"}, {"cell_type": "markdown"}, {"cell_type": "code"}]}'
                ),
            )
        self.assertEqual(
            "duplicate_notebook_key",
            notebook_error('{"cells": [], "cells": []}'),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root,
                "deep.ipynb",
                "{" + '"cells": [], "metadata": ' + "[" * 100 + "0" + "]" * 100 + "}",
            )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root), "--json"])
            envelope = json.loads(output.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual({"error", "ok", "result"}, set(envelope))
            self.assertEqual("audit_failed", envelope["error"]["code"])

    def test_commonmark_duplicate_definitions_nested_images_and_titles(self) -> None:
        source = """[first][duplicate]
[duplicate]: first.md "first title"
[duplicate]: second.md "second title"
![outer ![inner](inner.png)](outer.png)
[outer [inner](nested-link.md)](outer.md)
[angle](<angle.md> 'title')
[bad-inline](bad.md trailing prose)
[bad-reference]: bad-reference.md trailing prose
"""
        self.assertEqual(
            ["first.md", "outer.png", "inner.png", "outer.md", "angle.md"],
            MODULE._markdown_destinations(source),
        )

    def test_sibling_link_checks_scale_linearly_with_a_constant_boundary(self) -> None:
        def source_for(count: int) -> str:
            return " ".join(
                f"[sibling-{index}](target-{index}.md)" for index in range(count)
            )

        for count in (1_000, 2_000, 4_000, 8_000):
            with patch.object(
                MODULE,
                "_is_nested_plain_link_label",
                wraps=MODULE._is_nested_plain_link_label,
            ) as checks:
                destinations = MODULE._markdown_destinations(source_for(count))
            self.assertEqual(count, len(destinations))
            self.assertEqual(count, checks.call_count)

        sibling = "[sibling](near-one-mib-target.md) "
        near_one_mib = sibling * (MODULE.MAX_FILE_BYTES // len(sibling))
        with patch.object(
            MODULE,
            "_is_nested_plain_link_label",
            wraps=MODULE._is_nested_plain_link_label,
        ) as checks:
            destinations = MODULE._markdown_destinations(near_one_mib)
        self.assertEqual(len(near_one_mib) // len(sibling), len(destinations))
        self.assertEqual(len(destinations), checks.call_count)

    def test_handle_bound_reads_reject_races_hardlinks_and_size_overflow(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary).resolve()
            outside = Path(outside_temporary).resolve()
            document = self._write(root, "race.md", "# original\n")
            candidate = MODULE._bounded_files(root)[0][0]
            replacement = self._write(outside, "replacement.md", "SENTINEL!!\n")
            os.replace(replacement, document)
            with patch.object(
                MODULE.os, "read", side_effect=AssertionError("replacement read")
            ):
                text, error = MODULE._read_utf8(root, candidate)
            self.assertIsNone(text)
            self.assertEqual("file_changed_before_read", error)

            document = self._write(root, "growth.md", "# stable\n")
            candidate = next(
                item
                for item in MODULE._bounded_files(root)[0]
                if item.relative == document.name
            )

            def grow(relative: str) -> None:
                with (root / relative).open("ab") as handle:
                    handle.write(b"SENTINEL_GROWTH")

            text, error = MODULE._read_utf8(root, candidate, grow)
            self.assertIsNone(text)
            self.assertEqual("file_changed_during_read", error)

            linked = self._write(root, "linked.md", "# hardlink\n")
            os.link(linked, outside / "outside-alias.md")
            candidate = next(
                item
                for item in MODULE._bounded_files(root)[0]
                if item.relative == linked.name
            )
            with patch.object(
                MODULE.os, "read", side_effect=AssertionError("hardlink read")
            ):
                text, error = MODULE._read_utf8(root, candidate)
            self.assertIsNone(text)
            self.assertEqual("hardlink_rejected", error)

            exact = self._write(root, "exact.md", "12345678")
            over = self._write(root, "over.md", "123456789")
            candidates = {
                item.relative: item for item in MODULE._bounded_files(root)[0]
            }
            with patch.object(MODULE, "MAX_FILE_BYTES", 8):
                text, error = MODULE._read_utf8(root, candidates[exact.name])
                self.assertEqual("12345678", text)
                self.assertIsNone(error)
                with patch.object(
                    MODULE.os, "read", side_effect=AssertionError("over-cap read")
                ):
                    text, error = MODULE._read_utf8(root, candidates[over.name])
                self.assertIsNone(text)
                self.assertEqual("file_too_large", error)

    def test_junction_routing_is_rejected_before_read(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary).resolve()
            outside = Path(outside_temporary).resolve()
            self._write(root, "swap.md", "# original\n")
            candidate = MODULE._bounded_files(root)[0][0]
            outside_file = self._write(outside, "outside.md", "SENTINEL_OUTSIDE_BYTES")
            with (
                patch.object(
                    MODULE, "_windows_final_path", return_value=str(outside_file)
                ),
                patch.object(
                    MODULE.os, "read", side_effect=AssertionError("outside read")
                ),
            ):
                text, error = MODULE._read_utf8(root, candidate)
            self.assertIsNone(text)
            self.assertEqual("outside_root", error)


if __name__ == "__main__":
    unittest.main()
