"""Behavioral tests for the bounded REST Markdown validator."""

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


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_api_doc.py"
SPEC = importlib.util.spec_from_file_location("validate_api_doc", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidateApiDocTests(unittest.TestCase):
    def _file(self, directory: Path, name: str, contents: str) -> Path:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_endpoint_sections_are_headings_in_their_own_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = self._file(
                root,
                "endpoints.md",
                """# API

### Example request

## GET /first

Example request is prose, not a heading.

### Example response
### Success responses
### Error responses

~~~markdown
## GET /hidden
### Example request
### Example response
### Success responses
### Error responses
~~~

## POST /second
### Example request
### Example response
### Success responses
### Error responses
""",
            )
            result = MODULE.validate_file(document, root)
            self.assertEqual(
                ["endpoint 1: missing 'example request' heading"],
                result.errors,
            )

    def test_endpoint_scope_ends_at_same_or_shallower_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = self._file(
                root,
                "endpoints.md",
                """# API

### GET /first
#### Example request
#### Example response
#### Success responses

## General responses
#### Error responses

### POST /second
#### Example request
#### Example response
#### Success responses
#### Error responses
""",
            )
            result = MODULE.validate_file(document, root)
            self.assertEqual(
                ["endpoint 1: missing 'error responses' heading"], result.errors
            )

    def test_deeper_endpoint_heading_also_ends_the_prior_endpoint_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = self._file(
                root,
                "endpoints.md",
                """# API

## GET /first
### Example request
### Example response
### Success responses

#### POST /second
##### Example request
##### Example response
##### Success responses
##### Error responses
""",
            )
            result = MODULE.validate_file(document, root)
            self.assertEqual(
                ["endpoint 1: missing 'error responses' heading"], result.errors
            )

    def test_endpoint_layout_scales_linearly_without_tail_slicing(self) -> None:
        def elapsed(count: int) -> float:
            source = "# API\n" + "## GET /x\n" * count
            started = time.perf_counter()
            blocks = MODULE._endpoint_blocks(MODULE.mask_code(source))
            self.assertEqual(count, len(blocks))
            return time.perf_counter() - started

        timings = [elapsed(count) for count in (8_000, 16_000, 32_000, 65_000)]
        for smaller, larger in zip(timings, timings[1:]):
            self.assertLess(larger, smaller * 3.5)
        near_one_mib = "## GET /x\n" * (MODULE.MAX_FILE_BYTES // len("## GET /x\n"))
        started = time.perf_counter()
        self.assertTrue(MODULE._endpoint_blocks(MODULE.mask_code(near_one_mib)))
        near_one_mib_elapsed = time.perf_counter() - started
        self.assertLess(near_one_mib_elapsed, timings[-1] * 2.5)

    def test_nested_sections_and_commonmark_atx_closings_are_valid(self) -> None:
        source = """# API

## GET /items ###
### Details
#### Example request ##
#### Example response ###
#### Success responses #
#### Error responses ####

## GET /items ### trailing prose
"""
        masked = MODULE.mask_code(source)
        blocks = MODULE._endpoint_blocks(masked)
        self.assertEqual(1, len(blocks))
        headings = MODULE._section_headings(
            masked, blocks[0].start, blocks[0].end, blocks[0].level
        )
        self.assertTrue(set(MODULE.REQUIRED_ENDPOINT_SECTIONS).issubset(headings))

    def test_gfm_table_tokenizer_handles_escaped_pipes_and_parity(self) -> None:
        valid = """| Header \\| name | Value |
| :--- | ---: |
| literal \\| pipe | x |
"""
        invalid = """Header | Value
--- | --- | ---
x | y
"""
        invalid_delimiter = """| Header | Value |
| --- | -- |
"""
        self.assertEqual([], MODULE.find_table_issues(valid))
        self.assertEqual(
            ["Line 2: table delimiter column count does not match header"],
            MODULE.find_table_issues(invalid),
        )
        self.assertEqual(
            ["Line 2: invalid table delimiter row"],
            MODULE.find_table_issues(invalid_delimiter),
        )
        self.assertEqual(["a", "b \\\\"], MODULE._split_gfm_row(r"| a | b \\|"))
        self.assertEqual(["a", r"b \\\|"], MODULE._split_gfm_row(r"| a | b \\\|"))

    def test_code_mask_handles_many_unmatched_backtick_lengths(self) -> None:
        source = "".join("`" * length + "x" for length in range(1, 500))
        self.assertEqual(source, MODULE.mask_code(source))

    def test_strict_utf8_and_binary_errors_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = root / "invalid.md"
            invalid.write_bytes(b"# bad\xff")
            binary = root / "binary.md"
            binary.write_bytes(b"# bad\x00")
            control = root / "control.md"
            control.write_bytes(b"# bad\x01")
            self.assertEqual(
                ["markdown-1: invalid_utf8"], MODULE.validate_file(invalid, root).errors
            )
            self.assertEqual(
                ["markdown-1: binary_or_nul"], MODULE.validate_file(binary, root).errors
            )
            self.assertEqual(
                ["markdown-1: binary_or_nul"],
                MODULE.validate_file(control, root).errors,
            )

    def test_read_errors_are_stable_and_do_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = self._file(root, "readable.md", "# API\n")
            with patch.object(MODULE, "_read_utf8", return_value=(None, "read_error")):
                result = MODULE.validate_file(document, root)
            self.assertEqual(["markdown-1: read_error"], result.errors)

    def test_bounded_walk_casefold_excludes_reparse_and_caps(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary)
            outside = Path(outside_temporary)
            self._file(root, "NODE_MODULES/hidden.md", "# hidden")
            visible = self._file(root, "visible.md", "# visible")
            self._file(root, "second.md", "# second")
            self._file(outside, "outside.md", "# outside")
            link = root / "linked"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (NotImplementedError, OSError):
                pass
            files, issues = MODULE._bounded_markdown_files(root.resolve())
            self.assertEqual(
                ["second.md", visible.name], [item.relative for item in files]
            )
            self.assertEqual([], issues)
            with patch.object(MODULE, "MAX_FILES", 1):
                files, issues = MODULE._bounded_markdown_files(root.resolve())
            self.assertEqual(1, len(files))
            self.assertEqual(
                [(".", "file_limit")], [(item.path, item.code) for item in issues]
            )
            with patch.object(MODULE, "MAX_FILE_BYTES", 3):
                files, issues = MODULE._bounded_markdown_files(root.resolve())
            self.assertEqual([], files)
            self.assertEqual(
                [("second.md", "file_too_large"), ("visible.md", "file_too_large")],
                [(item.path, item.code) for item in issues],
            )

    def test_direct_non_markdown_target_fails_without_path_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "not-markdown.txt"
            target.write_text("plain", encoding="utf-8")
            output = StringIO()
            with (
                patch("sys.argv", ["validate_api_doc.py", str(target)]),
                redirect_stdout(output),
            ):
                exit_code = MODULE.main()
            self.assertEqual(1, exit_code)
            self.assertEqual(
                "[FAIL] target: expected a regular .md file\n", output.getvalue()
            )
            self.assertNotIn(str(target), output.getvalue())

    def test_cli_output_and_exit_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = self._file(root, "endpoint.md", "# API\n\n## GET /items\n")
            outputs: list[tuple[int, str]] = []
            for _ in range(2):
                output = StringIO()
                with (
                    patch("sys.argv", ["validate_api_doc.py", str(document)]),
                    redirect_stdout(output),
                ):
                    outputs.append((MODULE.main(), output.getvalue()))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(1, outputs[0][0])

    def test_directory_reports_use_contained_root_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._file(root, "docs/nested/endpoint.md", "# API\n\n## GET /items\n")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root)])
            rendered = output.getvalue()
            self.assertEqual(1, exit_code)
            self.assertIn("[FAIL] docs/nested/endpoint.md", rendered)
            self.assertNotIn("markdown-1", rendered)
            self.assertNotIn(str(root), rendered)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(root), "--json"])
            envelope = json.loads(output.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual(
                "docs/nested/endpoint.md", envelope["result"]["files"][0]["path"]
            )

    def test_starter_template_residue_is_warning_and_fails_final_validation(
        self,
    ) -> None:
        source = "# <api name> API\n\nClient: <package_name>\n"
        self.assertEqual(
            [
                "Line 1: starter template residue 'API name'",
                "Line 3: starter template residue 'package name'",
            ],
            MODULE.find_starter_template_residue(MODULE.mask_code(source)),
        )
        literal_markers = """Use `<api-name>` as a literal starter name.

```txt
<package-name>
```
"""
        self.assertEqual(
            [], MODULE.find_starter_template_residue(MODULE.mask_code(literal_markers))
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            literal_document = self._file(
                root, "literal-markers.md", f"# API\n\n{literal_markers}"
            )
            literal_result = MODULE.validate_file(literal_document, root)
            self.assertFalse(
                any(
                    "starter template residue" in warning
                    for warning in literal_result.warnings
                )
            )
            template = (
                Path(__file__).parents[1] / "templates" / "endpoint-single-file.md"
            )
            copied = self._file(root, "api.md", template.read_text(encoding="utf-8"))
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(copied), "--fail-on-warnings"])
            rendered = output.getvalue()
            self.assertEqual(1, exit_code)
            self.assertIn("[FAIL] markdown-1", rendered)
            self.assertNotIn("[PASS] markdown-1", rendered)
            self.assertIn("starter template residue 'API name'", rendered)
            self.assertIn("starter template residue 'package name'", rendered)

    def test_json_envelope_covers_usage_preflight_and_content_errors(self) -> None:
        sentinel = "SENTINEL_JSON_93841"
        cases: list[tuple[list[str], int]] = [
            (["--json"], 2),
            ([str(Path("missing") / f"access_token={sentinel}"), "--json"], 1),
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
            document = self._file(
                root,
                "endpoint.md",
                f"# API\n\n## GET /items?access_token={sentinel}\n",
            )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(document), "--json"])
            envelope = json.loads(output.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual("validation_failed", envelope["error"]["code"])
            self.assertNotIn(sentinel, json.dumps(envelope))

    def test_sensitive_assignments_are_removed_across_encodings(self) -> None:
        sentinel = "SENTINEL_REDACTION_72819"
        values = [
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
        for value in values:
            self.assertNotIn(sentinel, MODULE._redact_text(value))

    def test_output_privacy_handles_unicode_controls_and_unstable_encoding(
        self,
    ) -> None:
        sentinel = "SENTINEL_UNICODE_PRIVATE_21349"
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
            document = self._file(root, filename, "## GET /items\n")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = MODULE.main([str(document), "--json"])
            rendered = output.getvalue()
            self.assertEqual(1, exit_code)
            self.assertNotIn(sentinel, rendered)
            self.assertNotIn(filename, rendered)
            self.assertIn("markdown-1", rendered)
            machine_user = "MACHINE_USER_21349"
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

    def test_handle_bound_reads_reject_races_hardlinks_and_size_overflow(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary).resolve()
            outside = Path(outside_temporary).resolve()
            document = self._file(root, "race.md", "# original\n")
            candidate = MODULE._bounded_markdown_files(root)[0][0]
            replacement = self._file(outside, "replacement.md", "SENTINEL!!\n")
            os.replace(replacement, document)
            with patch.object(
                MODULE.os, "read", side_effect=AssertionError("replacement read")
            ):
                text, error = MODULE._read_utf8(root, candidate)
            self.assertIsNone(text)
            self.assertEqual("file_changed_before_read", error)

            document = self._file(root, "growth.md", "# stable\n")
            candidate = next(
                item
                for item in MODULE._bounded_markdown_files(root)[0]
                if item.relative == document.name
            )

            def grow(relative: str) -> None:
                with (root / relative).open("ab") as handle:
                    handle.write(b"SENTINEL_GROWTH")

            text, error = MODULE._read_utf8(root, candidate, grow)
            self.assertIsNone(text)
            self.assertEqual("file_changed_during_read", error)

            linked = self._file(root, "linked.md", "# hardlink\n")
            os.link(linked, outside / "outside-alias.md")
            candidate = next(
                item
                for item in MODULE._bounded_markdown_files(root)[0]
                if item.relative == linked.name
            )
            with patch.object(
                MODULE.os, "read", side_effect=AssertionError("hardlink read")
            ):
                text, error = MODULE._read_utf8(root, candidate)
            self.assertIsNone(text)
            self.assertEqual("hardlink_rejected", error)

            exact = self._file(root, "exact.md", "12345678")
            over = self._file(root, "over.md", "123456789")
            candidates = {
                item.relative: item for item in MODULE._bounded_markdown_files(root)[0]
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
            self._file(root, "swap.md", "# original\n")
            candidate = MODULE._bounded_markdown_files(root)[0][0]
            outside_file = self._file(outside, "outside.md", "SENTINEL_OUTSIDE_BYTES")
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

    def test_source_derived_error_redacts_secret_and_escapes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = "ghp_" + "x" * 16
            document = self._file(root, "unsafe[name].md", f"## GET /x?token={token}\n")
            output = StringIO()
            with (
                patch("sys.argv", ["validate_api_doc.py", str(document)]),
                redirect_stdout(output),
            ):
                exit_code = MODULE.main()
            self.assertEqual(1, exit_code)
            text = output.getvalue()
            self.assertNotIn(token, text)
            self.assertIn("markdown-1", text)
            self.assertNotIn("unsafe[name].md", text)
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
