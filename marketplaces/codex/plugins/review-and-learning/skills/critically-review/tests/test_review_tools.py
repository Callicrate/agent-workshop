from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
MERGE = SCRIPTS / "merge_findings.py"
RENDER = SCRIPTS / "render_report.py"
VALIDATE = SCRIPTS / "validate_findings.py"
INIT = SCRIPTS / "init_review.py"
SURFACE = SCRIPTS / "surface_snapshot.py"

sys.path.insert(0, str(SCRIPTS))
import review_io  # noqa: E402
from review_io import MAX_OUTPUT_BYTES, ToolError, snapshot_input, write_output  # noqa: E402


def finding(
    identifier: str = "F-001", severity: str = "high", **changes: Any
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": identifier,
        "title": "Retry behavior masks a non-idempotent write",
        "category": "architecture",
        "issue_type": "missing-idempotency",
        "severity": severity,
        "confidence": "high",
        "evidence_quality": "A",
        "quote": "retry failed requests",
        "location": "design:1",
        "analysis": "Retries alone can duplicate a completed downstream side effect.",
        "why_it_matters": "A duplicate charge can change the decision.",
        "evidence": [
            {
                "source_type": "artifact",
                "reference": "design.md:1",
                "notes": "The retry claim is explicit.",
            }
        ],
        "recommended_fix": "Add an idempotency key and a durable deduplication boundary.",
        "observed_evidence": "The design names only retries.",
        "decision_impact": "Do not claim exactly-once delivery.",
        "repair_shape": "Define deduplication ownership and failure handling.",
        "verification_needed": "Exercise a timeout after the downstream side effect completes.",
    }
    value.update(changes)
    return value


def report(
    item: dict[str, Any],
    *,
    research_performed: bool = False,
    title: str = "Design review",
) -> dict[str, Any]:
    return {
        "document": {
            "title": title,
            "artifact_type": "design",
            "domain": "software",
            "review_mode": "standard",
        },
        "scope": {
            "materials_reviewed": ["design.md"],
            "research_performed": research_performed,
            "limitations": [],
        },
        "strengths": ["The downstream dependency set is named."],
        "findings": [item],
        "omissions": [],
        "open_questions": [],
        "overall_assessment": {
            "summary": "revise: The reliability claim is not supported.",
            "decision_impact": (
                "Next action: Define the idempotency boundary.\n"
                "Validation gate: A timeout-after-write test produces one side effect."
            ),
            "confidence": "high",
        },
    }


class ReviewToolsTests(unittest.TestCase):
    @staticmethod
    def _windows_basic_file_info(path: Path) -> tuple[int, int, int, int, int]:
        """Read FILE_BASIC_INFO so the race test can restore exact timestamps."""
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        descriptor = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            information = FileBasicInfo()
            get_information = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).GetFileInformationByHandleEx
            get_information.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            get_information.restype = wintypes.BOOL
            if not get_information(
                msvcrt.get_osfhandle(descriptor),
                0,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise OSError(ctypes.get_last_error(), "file information failed")
            return (
                int(information.CreationTime),
                int(information.LastAccessTime),
                int(information.LastWriteTime),
                int(information.ChangeTime),
                int(information.FileAttributes),
            )
        finally:
            os.close(descriptor)

    @classmethod
    def _restore_windows_write_and_change_times(
        cls, path: Path, original: tuple[int, int, int, int, int]
    ) -> None:
        """Restore both mutable timestamps with SetFileInformationByHandle."""
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        descriptor = os.open(os.fspath(path), os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            current = cls._windows_basic_file_info(path)
            information = FileBasicInfo(
                current[0], current[1], original[2], original[3], current[4]
            )
            set_information = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).SetFileInformationByHandle
            set_information.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            set_information.restype = wintypes.BOOL
            if not set_information(
                msvcrt.get_osfhandle(descriptor),
                0,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise OSError(ctypes.get_last_error(), "file timestamp restore failed")
        finally:
            os.close(descriptor)

    maxDiff = None

    def run_tool(
        self, script: Path, *arguments: str
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-B", str(script), *arguments],
            cwd=SKILL_ROOT,
            check=False,
            capture_output=True,
        )

    def write_json(self, path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def tool_json(
        self, completed: subprocess.CompletedProcess[bytes]
    ) -> dict[str, Any]:
        self.assertEqual(completed.stderr, b"")
        return json.loads(completed.stdout.decode("utf-8"))

    def test_merge_is_order_independent_and_preserves_variants(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            inputs = [
                directory / name
                for name in ("a.json", "b.json", "c.json", "d.json", "e.json")
            ]
            output_ab = directory / "ab.json"
            output_ba = directory / "ba.json"
            self.write_json(
                inputs[0], report(finding(severity="high"), research_performed=False)
            )
            self.write_json(
                inputs[1], report(finding(severity="low"), research_performed=True)
            )
            self.write_json(
                inputs[2], report(finding(confidence="low"), research_performed=False)
            )
            self.write_json(
                inputs[3],
                report(
                    finding(analysis="A different causal analysis."),
                    research_performed=False,
                ),
            )
            self.write_json(
                inputs[4],
                report(
                    finding(
                        evidence=[
                            {
                                "source_type": "artifact",
                                "reference": "design.md:1",
                                "notes": "Different evidence note.",
                            }
                        ]
                    ),
                    research_performed=False,
                ),
            )

            self.assertEqual(
                self.run_tool(
                    MERGE, *(str(item) for item in inputs), "--output", str(output_ab)
                ).returncode,
                0,
            )
            self.assertEqual(
                self.run_tool(
                    MERGE,
                    *(str(item) for item in reversed(inputs)),
                    "--output",
                    str(output_ba),
                ).returncode,
                0,
            )
            self.assertEqual(output_ab.read_bytes(), output_ba.read_bytes())

            merged = json.loads(output_ab.read_text(encoding="utf-8"))
            self.assertEqual(len(merged["findings"]), 5)
            self.assertEqual(
                [item["severity"] for item in merged["findings"]].count("low"), 1
            )
            self.assertEqual(
                [item["confidence"] for item in merged["findings"]].count("low"), 1
            )
            self.assertIn(
                "A different causal analysis.",
                {item["analysis"] for item in merged["findings"]},
            )
            self.assertIn(
                "Different evidence note.",
                {item["evidence"][0]["notes"] for item in merged["findings"]},
            )
            self.assertTrue(merged["scope"]["research_performed"])
            self.assertEqual(
                [item["id"] for item in merged["findings"]],
                ["F-001", "F-002", "F-003", "F-004", "F-005"],
            )

    def test_conflicting_source_metadata_is_explicit_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            first = directory / "first.json"
            second = directory / "second.json"
            output = directory / "merged.json"
            self.write_json(first, report(finding(), title="First document"))
            second_report = report(finding(), title="Second document")
            second_report["overall_assessment"] = {
                "summary": "stop: The conflicting evidence changes the decision.",
                "decision_impact": (
                    "Next action: Reconcile the conflicting source.\n"
                    "Validation gate: The source assessments agree on the decision."
                ),
                "confidence": "low",
            }
            self.write_json(second, second_report)
            self.assertEqual(
                self.run_tool(
                    MERGE, str(first), str(second), "--output", str(output)
                ).returncode,
                0,
            )
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                merged["document"]["title"], "Merged Critical Review Findings"
            )
            self.assertEqual(merged["overall_assessment"]["confidence"], "low")
            self.assertEqual(
                merged["overall_assessment"],
                {
                    "summary": (
                        "insufficient-evidence: Source assessments conflict, so the "
                        "merge cannot establish one decision without adjudication."
                    ),
                    "decision_impact": (
                        "Next action: Reconcile the conflicting source assessments "
                        "without discarding their evidence.\n"
                        "Validation gate: One decision-ready assessment is supported "
                        "by the retained source evidence."
                    ),
                    "confidence": "low",
                },
            )
            self.assertIn(
                "conflict",
                json.dumps(merged["overall_assessment"], sort_keys=True).lower(),
            )
            self.assertEqual(
                {record["document"]["title"] for record in merged["source_reports"]},
                {"First document", "Second document"},
            )
            self.assertEqual(
                {
                    record["overall_assessment"]["summary"]
                    for record in merged["source_reports"]
                },
                {
                    report(finding())["overall_assessment"]["summary"],
                    second_report["overall_assessment"]["summary"],
                },
            )

    def test_exact_duplicates_dedupe_and_preserve_origins(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            first = directory / "a.json"
            second = directory / "b.json"
            output = directory / "merged.json"
            self.write_json(first, report(finding("F-001")))
            self.write_json(second, report(finding("F-002")))
            self.assertEqual(
                self.run_tool(
                    MERGE, str(first), str(second), "--output", str(output)
                ).returncode,
                0,
            )
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(merged["findings"]), 1)
            self.assertEqual(merged["findings"][0]["id"], "F-001")
            self.assertEqual(len(merged["findings"][0]["origins"]), 2)
            self.assertEqual(
                merged["overall_assessment"], report(finding())["overall_assessment"]
            )
            self.assertEqual(
                {item["finding_id"] for item in merged["findings"][0]["origins"]},
                {"F-001", "F-002"},
            )

    def test_false_only_research_remains_false_and_lists_validate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "findings.json"
            output = directory / "merged.json"
            self.write_json(source, [finding()])
            validated = self.run_tool(VALIDATE, "--report", str(source))
            self.assertEqual(validated.returncode, 0)
            self.assertEqual(self.tool_json(validated)["kind"], "findings-list")
            self.assertEqual(
                self.run_tool(MERGE, str(source), "--output", str(output)).returncode, 0
            )
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(merged["scope"]["research_performed"])
            self.assertEqual(
                merged["source_reports"][0]["document"]["artifact_type"], "unknown"
            )
            self.assertEqual(
                merged["overall_assessment"],
                {
                    "summary": (
                        "insufficient-evidence: Standalone findings do not establish "
                        "a document-level decision."
                    ),
                    "decision_impact": (
                        "Next action: Review the source artifact and adjudicate the "
                        "standalone findings.\n"
                        "Validation gate: A decision-ready assessment is supported by "
                        "the artifact and retained findings."
                    ),
                    "confidence": "low",
                },
            )
            self.assertNotIn(
                "conflict",
                json.dumps(merged["overall_assessment"], sort_keys=True).lower(),
            )

    def test_empty_inherited_provenance_rejects_and_mixed_metadata_is_explicit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            invalid = directory / "empty-provenance.json"
            invalid_report = report(finding())
            invalid_report["source_reports"] = []
            self.write_json(invalid, invalid_report)
            rejected = self.run_tool(
                VALIDATE, "--report", str(invalid), "--json-errors"
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(
                self.tool_json(rejected)["error"]["code"],
                "invalid-schema:source_reports",
            )

            full_report = directory / "full.json"
            standalone = directory / "standalone.json"
            output = directory / "merged.json"
            self.write_json(full_report, report(finding(), title="Full report"))
            self.write_json(standalone, [finding("F-002")])
            merged_result = self.run_tool(
                MERGE, str(full_report), str(standalone), "--output", str(output)
            )
            self.assertEqual(merged_result.returncode, 0)
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                merged["document"]["title"], "Merged Critical Review Findings"
            )
            self.assertEqual(merged["overall_assessment"]["confidence"], "low")
            self.assertTrue(
                any(
                    record["document"]["artifact_type"] == "unknown"
                    for record in merged["source_reports"]
                )
            )

    def test_invalid_severity_rejects_before_write_and_alias_never_changes_input(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            invalid = directory / "invalid.json"
            output = directory / "output.json"
            self.write_json(invalid, report(finding(severity="severe")))
            rejected = self.run_tool(
                MERGE, str(invalid), "--output", str(output), "--json-errors"
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(
                self.tool_json(rejected)["error"]["code"],
                "invalid-schema:findings.0.severity",
            )
            self.assertFalse(output.exists())

            valid = directory / "valid.json"
            self.write_json(valid, report(finding()))
            before = valid.read_bytes()
            aliased = self.run_tool(
                MERGE, str(valid), "--output", str(valid), "--force", "--json-errors"
            )
            self.assertEqual(aliased.returncode, 2)
            self.assertEqual(self.tool_json(aliased)["error"]["code"], "output-alias")
            self.assertEqual(valid.read_bytes(), before)

    def test_decision_contract_accepts_all_four_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            for verdict in ("proceed", "revise", "stop", "insufficient-evidence"):
                with self.subTest(verdict=verdict):
                    source = directory / f"{verdict}.json"
                    value = report(finding())
                    value["overall_assessment"]["summary"] = (
                        f"{verdict}: The reviewed evidence supports this verdict."
                    )
                    self.write_json(source, value)
                    accepted = self.run_tool(VALIDATE, "--report", str(source))
                    self.assertEqual(accepted.returncode, 0)

    def test_invalid_decision_contract_and_unclassified_unknowns_reject(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            cases = [
                ("blank-summary", ("overall_assessment", "summary"), "   "),
                (
                    "arbitrary-verdict",
                    ("overall_assessment", "summary"),
                    "approve: This token is not part of the contract.",
                ),
                ("missing-rationale", ("overall_assessment", "summary"), "revise:"),
                (
                    "blank-rationale",
                    ("overall_assessment", "summary"),
                    "revise:   ",
                ),
                (
                    "summary-trailing-newline",
                    ("overall_assessment", "summary"),
                    "revise: Resolve the defect.\n",
                ),
                (
                    "blank-decision-impact",
                    ("overall_assessment", "decision_impact"),
                    "\t\n",
                ),
                (
                    "arbitrary-decision-impact",
                    ("overall_assessment", "decision_impact"),
                    "Reconcile this later.",
                ),
                (
                    "missing-action",
                    ("overall_assessment", "decision_impact"),
                    "Validation gate: Evidence is complete.",
                ),
                (
                    "blank-action",
                    ("overall_assessment", "decision_impact"),
                    "Next action:   \nValidation gate: Evidence is complete.",
                ),
                (
                    "missing-gate",
                    ("overall_assessment", "decision_impact"),
                    "Next action: Reconcile the evidence.",
                ),
                (
                    "blank-gate",
                    ("overall_assessment", "decision_impact"),
                    "Next action: Reconcile the evidence.\nValidation gate:   ",
                ),
                (
                    "wrong-order",
                    ("overall_assessment", "decision_impact"),
                    "Validation gate: Evidence is complete.\nNext action: Reconcile it.",
                ),
                (
                    "decision-trailing-newline",
                    ("overall_assessment", "decision_impact"),
                    "Next action: Reconcile it.\nValidation gate: Evidence is complete.\n",
                ),
                (
                    "decision-extra-line",
                    ("overall_assessment", "decision_impact"),
                    "Next action: Reconcile it.\nValidation gate: Evidence is complete.\nExtra: no",
                ),
                ("unclassified-unknown", ("open_questions",), ["Who owns this?"]),
                ("blank-classified-unknown", ("open_questions",), ["[blocking]   "]),
            ]
            for label, path, invalid_value in cases:
                with self.subTest(label=label):
                    source = directory / f"{label}.json"
                    value = report(finding())
                    target: Any = value
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = invalid_value
                    self.write_json(source, value)
                    rejected = self.run_tool(
                        VALIDATE, "--report", str(source), "--json-errors"
                    )
                    self.assertEqual(rejected.returncode, 1)
                    self.assertTrue(
                        self.tool_json(rejected)["error"]["code"].startswith(
                            "invalid-schema:"
                        )
                    )

    def test_create_only_force_hardlink_and_symlink_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "source.json"
            output = directory / "output.json"
            self.write_json(source, report(finding()))
            output.write_text("unchanged", encoding="utf-8")
            existing = self.run_tool(
                MERGE, str(source), "--output", str(output), "--json-errors"
            )
            self.assertEqual(existing.returncode, 2)
            self.assertEqual(self.tool_json(existing)["error"]["code"], "output-exists")
            self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(
                self.run_tool(
                    MERGE, str(source), "--output", str(output), "--force"
                ).returncode,
                0,
            )

            hardlink = directory / "hardlink.json"
            os.link(source, hardlink)
            hardlink_result = self.run_tool(
                MERGE,
                str(source),
                "--output",
                str(hardlink),
                "--force",
                "--json-errors",
            )
            self.assertEqual(hardlink_result.returncode, 2)
            self.assertEqual(
                self.tool_json(hardlink_result)["error"]["code"], "output-alias"
            )

            target = directory / "target.json"
            target.write_text("target stays", encoding="utf-8")
            symlink = directory / "symlink.json"
            try:
                os.symlink(target, symlink)
            except (NotImplementedError, OSError):
                self.skipTest("the current platform cannot create a test symlink")
            symlink_result = self.run_tool(
                MERGE, str(source), "--output", str(symlink), "--force", "--json-errors"
            )
            self.assertEqual(symlink_result.returncode, 2)
            self.assertEqual(
                self.tool_json(symlink_result)["error"]["code"], "output-reparse"
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "target stays")

    def test_all_writers_reject_unrelated_hardlink_outputs_without_changing_victims(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "source.json"
            self.write_json(source, report(finding()))

            def assert_hardlink_rejected(
                script: Path, target: Path, *arguments: str
            ) -> None:
                victim = directory / f"{target.name}.victim"
                victim.write_text("victim remains intact", encoding="utf-8")
                os.link(victim, target)
                result = self.run_tool(
                    script,
                    *arguments,
                    "--output",
                    str(target),
                    "--force",
                    "--json-errors",
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(
                    self.tool_json(result)["error"]["code"], "output-hardlink"
                )
                self.assertEqual(
                    victim.read_text(encoding="utf-8"), "victim remains intact"
                )

            assert_hardlink_rejected(MERGE, directory / "merge.json", str(source))
            assert_hardlink_rejected(
                RENDER, directory / "render.md", "--report", str(source)
            )

            snapshot_root = directory / "surface-root"
            snapshot_root.mkdir()
            (snapshot_root / "entry.txt").write_text("entry", encoding="utf-8")
            assert_hardlink_rejected(
                SURFACE, directory / "surface.json", "--root", str(snapshot_root)
            )

            workspace = directory / "workspace"
            self.assertEqual(
                self.run_tool(INIT, "--output", str(workspace)).returncode, 0
            )
            init_victim = directory / "init.victim"
            init_victim.write_text("init victim remains intact", encoding="utf-8")
            (workspace / "report.md").unlink()
            os.link(init_victim, workspace / "report.md")
            init_result = self.run_tool(
                INIT, "--output", str(workspace), "--force", "--json-errors"
            )
            self.assertEqual(init_result.returncode, 2)
            self.assertEqual(
                self.tool_json(init_result)["error"]["code"], "output-hardlink"
            )
            self.assertEqual(
                init_victim.read_text(encoding="utf-8"), "init victim remains intact"
            )

    def test_force_behavior_is_explicit_for_every_writer(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "source.json"
            self.write_json(source, report(finding()))
            expected_returncode = 0 if os.name == "nt" else 2

            def assert_force(script: Path, target: Path, *arguments: str) -> None:
                target.write_text("old output", encoding="utf-8")
                result = self.run_tool(
                    script,
                    *arguments,
                    "--output",
                    str(target),
                    "--force",
                    "--json-errors",
                )
                self.assertEqual(result.returncode, expected_returncode)
                if expected_returncode:
                    self.assertEqual(
                        self.tool_json(result)["error"]["code"],
                        "output-force-unsupported",
                    )
                    self.assertEqual(target.read_text(encoding="utf-8"), "old output")
                else:
                    self.assertNotEqual(target.read_bytes(), b"old output")

            assert_force(MERGE, directory / "merge.json", str(source))
            assert_force(RENDER, directory / "render.md", "--report", str(source))

            snapshot_root = directory / "surface-root"
            snapshot_root.mkdir()
            assert_force(
                SURFACE, directory / "surface.json", "--root", str(snapshot_root)
            )

            workspace = directory / "workspace"
            self.assertEqual(
                self.run_tool(INIT, "--output", str(workspace)).returncode, 0
            )
            init_result = self.run_tool(
                INIT,
                "--output",
                str(workspace),
                "--title",
                "forced",
                "--force",
                "--json-errors",
            )
            self.assertEqual(init_result.returncode, expected_returncode)
            if expected_returncode:
                self.assertEqual(
                    self.tool_json(init_result)["error"]["code"],
                    "output-force-unsupported",
                )
            else:
                findings = json.loads(
                    (workspace / "findings.json").read_text(encoding="utf-8")
                )
                self.assertEqual(findings["document"]["title"], "forced")

    def test_malformed_unknown_deep_blank_and_duplicate_inputs_fail_stably(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            malformed = directory / "malformed.json"
            malformed.write_bytes(b"\xff")
            malformed_result = self.run_tool(
                VALIDATE, "--report", str(malformed), "--json-errors"
            )
            self.assertEqual(malformed_result.returncode, 2)
            self.assertEqual(
                self.tool_json(malformed_result)["error"]["code"], "invalid-utf8"
            )

            invalid_json = directory / "invalid-json.json"
            invalid_json.write_text("{", encoding="utf-8")
            invalid_json_result = self.run_tool(
                VALIDATE, "--report", str(invalid_json), "--json-errors"
            )
            self.assertEqual(invalid_json_result.returncode, 2)
            self.assertEqual(
                self.tool_json(invalid_json_result)["error"]["code"], "invalid-json"
            )
            invalid_json_human = self.run_tool(VALIDATE, "--report", str(invalid_json))
            self.assertEqual(invalid_json_human.returncode, 2)
            self.assertEqual(invalid_json_human.stdout, b"")
            self.assertEqual(
                invalid_json_human.stderr.decode("utf-8").strip(), "ERROR: invalid-json"
            )

            wrong_type = directory / "wrong-type.json"
            wrong_type.write_text("42", encoding="utf-8")
            wrong_type_result = self.run_tool(
                VALIDATE, "--report", str(wrong_type), "--json-errors"
            )
            self.assertEqual(wrong_type_result.returncode, 1)
            self.assertEqual(
                self.tool_json(wrong_type_result)["error"]["code"],
                "unsupported-json-shape",
            )

            deep = directory / "deep.json"
            deep.write_text("[" * 33 + "]" * 33, encoding="utf-8")
            deep_result = self.run_tool(
                VALIDATE, "--report", str(deep), "--json-errors"
            )
            self.assertEqual(deep_result.returncode, 2)
            self.assertEqual(
                self.tool_json(deep_result)["error"]["code"], "json-too-deep"
            )

            blank = directory / "blank.json"
            self.write_json(blank, [finding(identifier="")])
            blank_result = self.run_tool(
                VALIDATE, "--report", str(blank), "--json-errors"
            )
            self.assertEqual(blank_result.returncode, 1)
            self.assertEqual(
                self.tool_json(blank_result)["error"]["code"], "invalid-finding-id"
            )

            duplicate = directory / "duplicate.json"
            self.write_json(
                duplicate,
                report(finding(), title="Duplicate")
                | {
                    "findings": [
                        finding("F-001"),
                        finding("F-001", title="Another finding"),
                    ]
                },
            )
            duplicate_result = self.run_tool(
                VALIDATE, "--report", str(duplicate), "--json-errors"
            )
            self.assertEqual(duplicate_result.returncode, 1)
            self.assertEqual(
                self.tool_json(duplicate_result)["error"]["code"],
                "duplicate-finding-id",
            )

            unknown = directory / "unknown.json"
            data = report(finding())
            data["unknown"] = "reject"
            self.write_json(unknown, data)
            unknown_result = self.run_tool(
                VALIDATE, "--report", str(unknown), "--json-errors"
            )
            self.assertEqual(unknown_result.returncode, 1)
            self.assertEqual(
                self.tool_json(unknown_result)["error"]["code"], "invalid-schema"
            )

            too_large = directory / "too-large.json"
            too_large.write_bytes(b" " * (1_048_577))
            too_large_result = self.run_tool(
                VALIDATE, "--report", str(too_large), "--json-errors"
            )
            self.assertEqual(too_large_result.returncode, 2)
            self.assertEqual(
                self.tool_json(too_large_result)["error"]["code"], "input-too-large"
            )

            too_many_evidence = directory / "too-many-evidence.json"
            self.write_json(
                too_many_evidence,
                [
                    finding(
                        evidence=[
                            {"source_type": "artifact", "reference": "d", "notes": "n"}
                        ]
                        * 51
                    )
                ],
            )
            evidence_result = self.run_tool(
                VALIDATE, "--report", str(too_many_evidence), "--json-errors"
            )
            self.assertEqual(evidence_result.returncode, 1)
            self.assertEqual(
                self.tool_json(evidence_result)["error"]["code"],
                "invalid-schema:evidence",
            )

            surrogate_analysis = directory / "surrogate-analysis.json"
            surrogate_item = finding(analysis="\ud800")
            surrogate_analysis.write_text(
                json.dumps([surrogate_item], ensure_ascii=True), encoding="utf-8"
            )
            analysis_result = self.run_tool(
                VALIDATE, "--report", str(surrogate_analysis), "--json-errors"
            )
            self.assertEqual(analysis_result.returncode, 2)
            self.assertEqual(
                self.tool_json(analysis_result)["error"]["code"],
                "invalid-unicode-scalar",
            )

            surrogate_nested = directory / "surrogate-nested.json"
            nested_item = finding(
                evidence=[
                    {"source_type": "artifact", "reference": "d", "notes": "\udfff"}
                ]
            )
            surrogate_nested.write_text(
                json.dumps([nested_item], ensure_ascii=True), encoding="utf-8"
            )
            nested_result = self.run_tool(
                VALIDATE, "--report", str(surrogate_nested), "--json-errors"
            )
            self.assertEqual(nested_result.returncode, 2)
            self.assertEqual(
                self.tool_json(nested_result)["error"]["code"], "invalid-unicode-scalar"
            )

            with self.assertRaisesRegex(ToolError, "output-too-large"):
                write_output(
                    "x" * (MAX_OUTPUT_BYTES + 1),
                    str(directory / "oversized.json"),
                    [],
                    force=False,
                )

    def test_render_uses_only_fixed_headings_and_inert_literals(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "hostile.json"
            output = directory / "report.md"
            hostile = '# injected\n![remote](https://example.invalid/pixel)\n<img src="https://example.invalid/x">\n```\n\x1b'
            item = finding(
                title=hostile,
                quote=hostile,
                analysis=hostile,
                observed_evidence=hostile,
            )
            self.write_json(source, report(item, title=hostile))
            rendered = self.run_tool(
                RENDER, "--report", str(source), "--output", str(output)
            )
            self.assertEqual(rendered.returncode, 0)
            markdown = output.read_text(encoding="utf-8")
            headings = [line for line in markdown.splitlines() if line.startswith("#")]
            self.assertEqual(
                headings,
                [
                    "# Critical Review Report",
                    "## Document",
                    "## Scope and Materials Reviewed",
                    "## Overall Assessment",
                    "## What Holds Up",
                    "## Detailed Findings",
                    "### Finding 1",
                    "## Material Omissions",
                    "## Open Questions",
                    "## Decision and Next Action",
                ],
            )
            self.assertNotIn("\x1b", markdown)
            self.assertIn("\\u001b", markdown)
            self.assertIn("````text", markdown)
            self.assertIn("Next action: Define the idempotency boundary.", markdown)
            self.assertEqual(markdown.count('"revise:'), 1)
            self.assertEqual(
                markdown.count("Next action: Define the idempotency boundary."), 1
            )

    def test_render_validates_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "invalid.json"
            output = directory / "report.md"
            self.write_json(source, report(finding(severity="bad")))
            result = self.run_tool(
                RENDER,
                "--report",
                str(source),
                "--output",
                str(output),
                "--json-errors",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                self.tool_json(result)["error"]["code"],
                "invalid-schema:findings.0.severity",
            )
            self.assertFalse(output.exists())

    def test_output_primitive_rechecks_hardlink_and_parent_swap_races(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            source = directory / "input.json"
            source.write_text("input is unchanged", encoding="utf-8")
            output = directory / "output.json"
            snapshot = snapshot_input(str(source))
            original_hook = review_io._TEST_OUTPUT_HOOK
            try:

                def swap_to_input(stage: str, target: Path) -> None:
                    self.assertEqual(stage, "before-publish")
                    os.link(source, target)

                review_io._TEST_OUTPUT_HOOK = swap_to_input
                with self.assertRaisesRegex(ToolError, "output-alias"):
                    write_output("new output", str(output), [snapshot], force=False)
                self.assertEqual(
                    source.read_text(encoding="utf-8"), "input is unchanged"
                )
                self.assertTrue(output.exists())
                output.unlink()

                parent = directory / "parent"
                outside = directory / "outside"
                parent.mkdir()
                outside.mkdir()
                swapped = False

                def swap_parent(stage: str, target: Path) -> None:
                    nonlocal swapped
                    self.assertEqual(stage, "before-publish")
                    child = """
from pathlib import Path
import os
import subprocess
import sys

parent, moved, outside = map(Path, sys.argv[1:])
parent.rename(moved)
if os.name == "nt":
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(parent), str(outside)], check=False)
    raise SystemExit(result.returncode)
os.symlink(outside, parent, target_is_directory=True)
"""
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            child,
                            str(parent),
                            str(directory / "moved-parent"),
                            str(outside),
                        ],
                        capture_output=True,
                        check=False,
                    )
                    swapped = completed.returncode == 0

                review_io._TEST_OUTPUT_HOOK = swap_parent
                if os.name == "nt":
                    try:
                        written = write_output(
                            "new output", str(parent / "output.json"), [], force=False
                        )
                    except ToolError as exc:
                        self.assertTrue(swapped)
                        self.assertEqual(exc.code, "output-parent-changed")
                    else:
                        self.assertFalse(swapped)
                        self.assertEqual(written, parent / "output.json")
                else:
                    with self.assertRaisesRegex(ToolError, "output-parent-changed"):
                        write_output(
                            "new output", str(parent / "output.json"), [], force=False
                        )
                self.assertFalse((outside / "output.json").exists())
                if os.name != "nt":
                    self.assertTrue(swapped)

                late_parent = directory / "late-parent"
                late_outside = directory / "late-outside"
                late_parent.mkdir()
                late_outside.mkdir()

                def swap_after_final_validation(stage: str, target: Path) -> None:
                    if stage == "before-publish":
                        return
                    self.assertEqual(stage, "after-final-validation")
                    child = """
from pathlib import Path
import os
import subprocess
import sys

parent, moved, outside = map(Path, sys.argv[1:])
parent.rename(moved)
if os.name == "nt":
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(parent), str(outside)], check=False)
    raise SystemExit(result.returncode)
os.symlink(outside, parent, target_is_directory=True)
"""
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            child,
                            str(late_parent),
                            str(directory / "late-moved-parent"),
                            str(late_outside),
                        ],
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0)

                review_io._TEST_OUTPUT_HOOK = swap_after_final_validation
                with self.assertRaisesRegex(ToolError, "output-parent-changed"):
                    write_output(
                        "new output", str(late_parent / "output.json"), [], force=False
                    )
                self.assertFalse((late_outside / "output.json").exists())
            finally:
                review_io._TEST_OUTPUT_HOOK = original_hook
                snapshot.close()

    @unittest.skipUnless(os.name == "nt", "Windows junction coverage")
    def test_windows_junction_output_path_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            outside = directory / "outside"
            junction = directory / "junction"
            outside.mkdir()
            completed = subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                self.skipTest("the current Windows host cannot create a test junction")
            with self.assertRaisesRegex(ToolError, "output-reparse"):
                write_output("safe", str(junction / "output.json"), [], force=False)
            self.assertFalse((outside / "output.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows force-path race coverage")
    def test_windows_force_rechecks_parent_before_handle_relative_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            parent = directory / "parent"
            outside = directory / "outside"
            moved = directory / "moved-parent"
            parent.mkdir()
            outside.mkdir()
            target = parent / "output.json"
            target.write_text("unchanged", encoding="utf-8")
            original_hook = review_io._TEST_OUTPUT_HOOK
            try:

                def swap_before_force_open(stage: str, hook_target: Path) -> None:
                    self.assertEqual(stage, "before-force-open")
                    self.assertEqual(hook_target, target)
                    parent.rename(moved)
                    completed = subprocess.run(
                        ["cmd", "/d", "/c", "mklink", "/J", str(parent), str(outside)],
                        capture_output=True,
                        check=False,
                    )
                    if completed.returncode != 0:
                        self.skipTest(
                            "the current Windows host cannot create a test junction"
                        )

                review_io._TEST_OUTPUT_HOOK = swap_before_force_open
                with self.assertRaisesRegex(ToolError, "output-parent-changed"):
                    write_output("new", str(target), [], force=True)
                self.assertFalse((outside / "output.json").exists())
                self.assertEqual(
                    (moved / "output.json").read_text(encoding="utf-8"), "unchanged"
                )
            finally:
                review_io._TEST_OUTPUT_HOOK = original_hook

    @unittest.skipUnless(os.name == "nt", "Windows force-version race coverage")
    def test_windows_force_refuses_preflight_growth_and_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            target = directory / "output.json"
            original_hook = review_io._TEST_OUTPUT_HOOK
            cases = [
                ("growth", "old data plus appended data", False),
                ("shrink", "x", False),
                ("same-size-overwrite", "new data", True),
                ("replacement", "replacement", False),
            ]
            try:
                for label, changed, touch_timestamp in cases:
                    with self.subTest(label=label):
                        target.write_text("old data", encoding="utf-8")

                        def mutate_before_force_open(
                            stage: str, hook_target: Path
                        ) -> None:
                            self.assertEqual(stage, "before-force-open")
                            self.assertEqual(hook_target, target)
                            if label == "replacement":
                                target.unlink()
                            target.write_text(changed, encoding="utf-8")
                            if touch_timestamp:
                                current = target.stat().st_mtime_ns
                                os.utime(
                                    target,
                                    ns=(current, current + 1_000_000_000),
                                )

                        review_io._TEST_OUTPUT_HOOK = mutate_before_force_open
                        with self.assertRaisesRegex(ToolError, "output-changed"):
                            write_output(
                                "replacement output", str(target), [], force=True
                            )
                        self.assertEqual(target.read_text(encoding="utf-8"), changed)

                target.write_text("unchanged", encoding="utf-8")
                review_io._TEST_OUTPUT_HOOK = None
                self.assertEqual(
                    write_output("replacement output", str(target), [], force=True),
                    target,
                )
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "replacement output"
                )
            finally:
                review_io._TEST_OUTPUT_HOOK = original_hook

    @unittest.skipUnless(os.name == "nt", "Windows force-content race coverage")
    def test_windows_force_refuses_timestamp_restored_content_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            target = directory / "output.json"
            old_data = "old-data"
            new_data = "new-data"
            original_hook = review_io._TEST_OUTPUT_HOOK
            try:
                target.write_text(old_data, encoding="utf-8")
                original_times = self._windows_basic_file_info(target)

                def rewrite_and_restore_times(stage: str, hook_target: Path) -> None:
                    self.assertEqual(stage, "before-force-open")
                    self.assertEqual(hook_target, target)
                    target.write_text(new_data, encoding="utf-8")
                    self._restore_windows_write_and_change_times(target, original_times)
                    restored = self._windows_basic_file_info(target)
                    self.assertEqual(restored[2:4], original_times[2:4])

                review_io._TEST_OUTPUT_HOOK = rewrite_and_restore_times
                with self.assertRaisesRegex(ToolError, "output-changed"):
                    write_output("replacement output", str(target), [], force=True)
                self.assertEqual(target.read_text(encoding="utf-8"), new_data)

                target.write_text(old_data, encoding="utf-8")
                original_times = self._windows_basic_file_info(target)

                def restore_identical_content(stage: str, hook_target: Path) -> None:
                    if stage == "before-force-truncate":
                        return
                    self.assertEqual(stage, "before-force-open")
                    self.assertEqual(hook_target, target)
                    target.write_text(old_data, encoding="utf-8")
                    self._restore_windows_write_and_change_times(target, original_times)

                review_io._TEST_OUTPUT_HOOK = restore_identical_content
                self.assertEqual(
                    write_output("replacement output", str(target), [], force=True),
                    target,
                )
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "replacement output"
                )
            finally:
                review_io._TEST_OUTPUT_HOOK = original_hook

    @unittest.skipUnless(os.name == "nt", "Windows force-content bounds coverage")
    def test_windows_force_content_digest_bounds_and_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            target = directory / "output.json"

            target.write_bytes(b"x" * MAX_OUTPUT_BYTES)
            self.assertEqual(
                write_output("replacement output", str(target), [], force=True),
                target,
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "replacement output")

            too_large = b"x" * (MAX_OUTPUT_BYTES + 1)
            target.write_bytes(too_large)
            with self.assertRaisesRegex(ToolError, "output-too-large"):
                write_output("replacement output", str(target), [], force=True)
            self.assertEqual(target.read_bytes(), too_large)

            unreadable = b"old-data"
            target.write_bytes(unreadable)
            with mock.patch.object(review_io.os, "read", side_effect=OSError):
                with self.assertRaisesRegex(ToolError, "output-unreadable"):
                    write_output("replacement output", str(target), [], force=True)
            self.assertEqual(target.read_bytes(), unreadable)

    def test_init_review_create_only_behavior_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            workspace = Path(raw_dir) / "review"
            initialized = self.run_tool(
                INIT, "--output", str(workspace), "--title", "Initial title"
            )
            self.assertEqual(initialized.returncode, 0)
            findings = json.loads(
                (workspace / "findings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(findings["document"]["title"], "Initial title")
            self.assertFalse(findings["scope"]["research_performed"])
            repeated = self.run_tool(
                INIT, "--output", str(workspace), "--title", "Replacement title"
            )
            self.assertEqual(repeated.returncode, 0)
            self.assertIn(b"WARNING:", repeated.stderr)
            self.assertEqual(
                json.loads((workspace / "findings.json").read_text(encoding="utf-8"))[
                    "document"
                ]["title"],
                "Initial title",
            )


if __name__ == "__main__":
    unittest.main()
