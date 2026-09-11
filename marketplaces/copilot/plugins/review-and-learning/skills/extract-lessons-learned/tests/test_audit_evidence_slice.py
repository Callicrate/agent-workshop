from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import audit_evidence_slice as audit  # noqa: E402

REAL_V4_ENV = "EXTRACT_LESSONS_REAL_V4_JSONL"
REAL_V4_REPORT_SHA256 = (
    "0c3a158d077d486f3d29f2071552512d4daad0eea933e5b7a1686feb7c49a988"
)


class AuditEvidenceSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_jsonl(self, name: str, records: list[object]) -> Path:
        path = self.root / name
        payload = "".join(
            f"{json.dumps(record, separators=(',', ':'))}\n" for record in records
        )
        path.write_text(payload, encoding="utf-8", newline="\n")
        return path.resolve()

    def test_valid_jsonl_snapshot_is_deterministic(self) -> None:
        path = self.write_jsonl(
            "slice.jsonl",
            [
                {
                    "session_id": "b",
                    "role": "user",
                    "text": "missed history again wrong must",
                },
                {
                    "session_id": "a",
                    "role": "assistant",
                    "content": "rate-limited request failed",
                },
                {"session_id": "a", "role": "user", "text": ""},
            ],
        )

        report = audit.build_report([path], audit.DEFAULT_PHRASES)

        self.assertEqual(
            report,
            {
                "inputs": [str(path)],
                "files_read": [str(path)],
                "parse_failures": {},
                "source_families": {"exported_jsonl": 1},
                "sessions": 2,
                "turns": 3,
                "empty_sessions": 0,
                "prompt_only_sessions": 1,
                "error_turns": 1,
                "error_categories": {"rate_limited": 1, "request_failed": 1},
                "high_frustration_sessions": ["b"],
                "duplicate_session_keys": {},
                "phrase_matches": {"missed history": 1, "rate-limited": 1},
                "session_summaries": [
                    {
                        "session_key": "a",
                        "source_paths": [str(path)],
                        "source_family": "exported_jsonl",
                        "records": 2,
                        "turns": 2,
                        "user_turns": 1,
                        "assistant_turns": 1,
                        "error_turns": 1,
                        "error_categories": {"rate_limited": 1, "request_failed": 1},
                        "empty": False,
                        "prompt_only": False,
                        "high_frustration": False,
                        "phrase_matches": {"rate-limited": 1},
                    },
                    {
                        "session_key": "b",
                        "source_paths": [str(path)],
                        "source_family": "exported_jsonl",
                        "records": 1,
                        "turns": 1,
                        "user_turns": 1,
                        "assistant_turns": 0,
                        "error_turns": 0,
                        "error_categories": {},
                        "empty": False,
                        "prompt_only": True,
                        "high_frustration": True,
                        "phrase_matches": {"missed history": 1},
                    },
                ],
            },
        )
        self.assertEqual(
            json.dumps(report, indent=2),
            json.dumps(audit.build_report([path], audit.DEFAULT_PHRASES), indent=2),
        )

    def test_jsonl_never_uses_path_read_text(self) -> None:
        path = self.write_jsonl(
            "slice.jsonl", [{"session_id": "s", "role": "user", "text": "hello"}]
        )
        with mock.patch.object(
            Path, "read_text", side_effect=AssertionError("read_text must not be used")
        ):
            report = audit.build_report([path], audit.DEFAULT_PHRASES)
        self.assertEqual(report["sessions"], 1)

    def test_malformed_late_line_discards_the_entire_file(self) -> None:
        malformed = self.root / "malformed.jsonl"
        malformed.write_bytes(
            b'{"session_id":"keep","role":"user","text":"discarded marker"}\n{"oops":\n'
        )
        valid = self.write_jsonl(
            "valid.jsonl", [{"session_id": "keep", "role": "assistant", "text": "done"}]
        )

        report = audit.build_report([malformed, valid], audit.DEFAULT_PHRASES)

        self.assertEqual(
            [item["session_key"] for item in report["session_summaries"]], ["keep"]
        )
        self.assertEqual(report["session_summaries"][0]["source_paths"], [str(valid)])
        self.assertEqual(report["duplicate_session_keys"], {})
        self.assertEqual(
            report["parse_failures"][str(malformed.resolve())],
            {"kind": "invalid_json", "line": 2, "column": 9},
        )
        serialized_failure = json.dumps(report["parse_failures"])
        self.assertNotIn("discarded marker", serialized_failure)
        self.assertNotIn("oops", serialized_failure)

    def test_invalid_utf8_is_value_free_and_atomic(self) -> None:
        path = self.root / "invalid.jsonl"
        path.write_bytes(b'{"session_id":"discard","text":"first"}\n{"text":"\xff"}\n')

        report = audit.build_report([path], audit.DEFAULT_PHRASES)

        self.assertEqual(report["sessions"], 0)
        self.assertEqual(
            report["parse_failures"][str(path.resolve())],
            {"kind": "invalid_utf8", "line": 2, "column": 10},
        )

    def test_duplicate_session_paths_are_stable_for_reversed_inputs(self) -> None:
        first = self.write_jsonl(
            "b.jsonl", [{"session_id": "shared", "role": "user", "text": "one"}]
        )
        second = self.write_jsonl(
            "a.jsonl", [{"session_id": "shared", "role": "assistant", "text": "two"}]
        )

        forward = audit.build_report([first, second], audit.DEFAULT_PHRASES)
        reverse = audit.build_report([second, first], audit.DEFAULT_PHRASES)

        expected_paths = sorted([str(first), str(second)])
        self.assertEqual(forward["files_read"], expected_paths)
        self.assertEqual(reverse["files_read"], expected_paths)
        self.assertEqual(forward["duplicate_session_keys"], {"shared": expected_paths})
        self.assertEqual(reverse["duplicate_session_keys"], {"shared": expected_paths})
        self.assertEqual(forward["session_summaries"], reverse["session_summaries"])

    def test_monolithic_json_requires_explicit_bounded_opt_in(self) -> None:
        path = self.root / "slice.json"
        path.write_text(
            json.dumps([{"session_id": "s", "role": "user", "text": "hello"}]),
            encoding="utf-8",
        )
        path = path.resolve()

        refused = audit.build_report([path], audit.DEFAULT_PHRASES)
        self.assertEqual(
            refused["parse_failures"][str(path)],
            {"kind": "monolithic_json_refused", "line": 1, "column": 1},
        )
        with self.assertRaisesRegex(ValueError, "positive max_input_bytes"):
            audit.build_report(
                [path], audit.DEFAULT_PHRASES, allow_monolithic_json=True
            )
        with self.assertRaisesRegex(ValueError, "requires allow_monolithic_json"):
            audit.build_report([path], audit.DEFAULT_PHRASES, max_input_bytes=100)

        accepted = audit.build_report(
            [path],
            audit.DEFAULT_PHRASES,
            allow_monolithic_json=True,
            max_input_bytes=path.stat().st_size,
        )
        self.assertEqual(accepted["sessions"], 1)
        too_large = audit.build_report(
            [path],
            audit.DEFAULT_PHRASES,
            allow_monolithic_json=True,
            max_input_bytes=path.stat().st_size - 1,
        )
        self.assertEqual(
            too_large["parse_failures"][str(path)],
            {"kind": "input_too_large", "line": 1, "column": path.stat().st_size},
        )

    def test_caller_supplied_record_limit_bounds_and_rejects_a_file(self) -> None:
        path = self.write_jsonl(
            "large.jsonl", [{"session_id": "discard", "text": "x" * 100}]
        )

        report = audit.build_report([path], audit.DEFAULT_PHRASES, max_record_bytes=20)

        self.assertEqual(report["sessions"], 0)
        self.assertEqual(
            report["parse_failures"][str(path)],
            {"kind": "record_too_large", "line": 1, "column": 21},
        )

    def test_explicit_unsupported_file_is_a_discovery_failure(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("not evidence", encoding="utf-8")
        path = path.resolve()

        report = audit.build_report([path], audit.DEFAULT_PHRASES)

        self.assertEqual(report["files_read"], [])
        self.assertEqual(
            report["parse_failures"],
            {
                str(path): {
                    "kind": "unsupported_input",
                    "line": 0,
                    "column": 0,
                }
            },
        )
        stdout = io.StringIO()
        with mock.patch.object(
            sys, "argv", ["audit_evidence_slice.py", "--json", str(path)]
        ):
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(audit.main(), 1)

    def test_explicit_directory_with_no_eligible_files_fails(self) -> None:
        directory = self.root / "empty-evidence"
        directory.mkdir()
        (directory / "notes.txt").write_text("not evidence", encoding="utf-8")
        directory = directory.resolve()

        report = audit.build_report([directory], audit.DEFAULT_PHRASES)

        self.assertEqual(report["files_read"], [])
        self.assertEqual(
            report["parse_failures"],
            {
                str(directory): {
                    "kind": "no_eligible_files",
                    "line": 0,
                    "column": 0,
                }
            },
        )

    def test_empty_eligible_jsonl_remains_a_successful_empty_slice(self) -> None:
        path = self.root / "empty.jsonl"
        path.write_bytes(b"")

        report = audit.build_report([path], audit.DEFAULT_PHRASES)

        self.assertEqual(report["files_read"], [str(path.resolve())])
        self.assertEqual(report["parse_failures"], {})
        self.assertEqual(report["sessions"], 0)

    def test_markdown_percent_encodes_untrusted_failure_paths(self) -> None:
        path = self.write_jsonl("empty.jsonl", [])
        report = audit.build_report([path], audit.DEFAULT_PHRASES)
        hostile_path = "C:\\work\\bad\n## heading `code` \x01.txt"
        report["parse_failures"] = {
            hostile_path: {"kind": "unsupported_input", "line": 0, "column": 0}
        }
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            audit.print_markdown(report)

        rendered = stdout.getvalue()
        escaped = "C:%5Cwork%5Cbad%0A%23%23%20heading%20%60code%60%20%01.txt"
        self.assertEqual(audit.escape_path_for_markdown(hostile_path), escaped)
        self.assertIn(f"path={escaped}; kind=unsupported_input", rendered)
        self.assertNotIn(hostile_path, rendered)
        self.assertNotIn("\n## heading", rendered)
        self.assertNotIn("`", rendered)
        json_round_trip = json.loads(json.dumps(report))
        self.assertIn(hostile_path, json_round_trip["parse_failures"])

    def test_cli_returns_nonzero_after_emitting_failure_report(self) -> None:
        path = self.root / "bad.jsonl"
        path.write_bytes(b"not-json\n")
        stdout = io.StringIO()
        with mock.patch.object(
            sys, "argv", ["audit_evidence_slice.py", "--json", str(path)]
        ):
            with contextlib.redirect_stdout(stdout):
                result = audit.main()

        self.assertEqual(result, 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(
            report["parse_failures"][str(path.resolve())]["kind"], "invalid_json"
        )

    def test_resource_defects_are_not_reclassified_as_parse_failures(self) -> None:
        path = self.write_jsonl("slice.jsonl", [{"session_id": "s", "text": "hello"}])
        for defect in (
            MemoryError("resource exhausted"),
            RecursionError("recursive input"),
        ):
            with self.subTest(defect=type(defect).__name__):
                with mock.patch.object(audit, "iter_jsonl_records", side_effect=defect):
                    with self.assertRaises(type(defect)):
                        audit.build_report([path], audit.DEFAULT_PHRASES)

    def test_memory_growth_tracks_final_state_not_record_volume(self) -> None:
        small = self.write_jsonl(
            "small.jsonl",
            [{"session_id": "same", "role": "user", "text": "ordinary text"}] * 100,
        )
        large = self.write_jsonl(
            "large.jsonl",
            [{"session_id": "same", "role": "user", "text": "ordinary text"}] * 5_000,
        )

        def peak_for(path: Path) -> tuple[int, int]:
            tracemalloc.start()
            try:
                report = audit.build_report([path], audit.DEFAULT_PHRASES)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            return report["session_summaries"][0]["records"], peak

        small_records, small_peak = peak_for(small)
        large_records, large_peak = peak_for(large)
        self.assertEqual((small_records, large_records), (100, 5_000))
        self.assertLess(large_peak, (small_peak * 4) + 100_000)
        self.assertLess(large_peak, large.stat().st_size)

    @unittest.skipUnless(
        os.environ.get(REAL_V4_ENV),
        f"set {REAL_V4_ENV} to run the real-corpus benchmark",
    )
    def test_real_v4_report_hash_and_peak_memory(self) -> None:
        source = Path(os.environ[REAL_V4_ENV]).resolve()
        tracemalloc.start()
        try:
            report = audit.build_report([source], audit.DEFAULT_PHRASES)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        rendered = json.dumps(report, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

        self.assertEqual(hashlib.sha256(rendered).hexdigest(), REAL_V4_REPORT_SHA256)
        self.assertLess(peak, source.stat().st_size)


if __name__ == "__main__":
    unittest.main()
