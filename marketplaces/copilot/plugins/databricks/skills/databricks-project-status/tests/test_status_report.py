"""Tests for the Databricks project status report helper."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "status_report.py"
_SPEC = importlib.util.spec_from_file_location("databricks_project_status_report", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load report helper: {_SCRIPT}")
reporter = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = reporter
_SPEC.loader.exec_module(reporter)


class StatusReportTests(unittest.TestCase):
    @staticmethod
    def write_context_receipt(root: Path, profile: str = "TEST") -> Path:
        receipt = root / "databricks-context.json"
        receipt.write_text(json.dumps({"profiles": [{"profile": profile, "host": "https://configured.example", "current_user": {"ok": True, "principal": {"valid": True, "userName": "status.user@example.com", "id": "user-123"}}, "effective_context": {"version": 1, "ok": True, "profile": profile, "host": "https://effective.example"}}]}), encoding="utf-8")
        return receipt

    @staticmethod
    def complete_report(report: Path) -> None:
        text = report.read_text(encoding="utf-8")
        text = re.sub(
            reporter.MARKER_RE,
            "Evidence recorded from bounded API and metadata inspection with explicit limitations.",
            text,
        )
        text = text.replace("Unknown until verified", "Unavailable: profile verification was blocked")
        completed_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("| Live resource and history APIs |"):
                line = (
                    "| Live resource and history APIs | test fixture resources | "
                    "pages=1; raw=0; unique=0 | "
                    "requested=2026-08-03..2026-08-10; observed=2026-08-03..2026-08-10 | "
                    "complete | No limitations in the synthetic fixture |"
                )
            elif line.strip().startswith("|") and line.count("Unknown") >= 4:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if cells[-1] == "No":
                    cells[1:-1] = ["Not found", "dev", "team", "repository inventory", "Not applicable"]
                    cells[-1] = "Yes"
                else:
                    cells[1:] = ["Not applicable", "No resource", "No resource", "Stable", "Repository inventory"]
                line = "| " + " | ".join(cells) + " |"
            completed_lines.append(line)
        text = "\n".join(completed_lines) + "\n"
        text = text.replace(
            "|---|---|---|---|---|---|---|\n\n## Prioritized Recommendations",
            "|---|---|---|---|---|---|---|\n| F1 | Medium | High | job-1 / team | One bounded timeout | Tune timeout | Next three slots |\n\n## Prioritized Recommendations",
        )
        report.write_text(text, encoding="utf-8", newline="\n")

    def test_create_uses_required_path_and_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(
                root,
                "Example Project",
                "TEST",
                "20260810T123456",
                30,
                "UTC",
            )
            text = report.read_text(encoding="utf-8")

            self.assertEqual(report, root / "status-reports" / "20260810T123456-status.md")
            self.assertIn("# Databricks Project Status: Example Project", text)
            self.assertIn("**Current window:** `2026-07-11T00:00:00+00:00` to `2026-08-10T00:00:00+00:00`", text)
            self.assertIn("**Baseline window:** `2026-06-11T00:00:00+00:00` to `2026-07-11T00:00:00+00:00`", text)
            self.assertIn("**Databricks profile:** `TEST`", text)
            self.assertIn("**Workspace host / principal:** Unknown until verified", text)
            self.assertIn("**Repository branch:**", text)
            self.assertIn("**Repository commit:**", text)
            self.assertIn("**Repository worktree:** `Unavailable: not a Git worktree`", text)
            self.assertIn(
                "remove all STATUS-REPORT template comments after filling the report",
                reporter.validate_report(report, root),
            )

    def test_create_uses_one_matching_effective_context_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", self.write_context_receipt(root))
            text = report.read_text(encoding="utf-8")
            self.assertIn("**Workspace host / principal:** `https://effective.example` / `status.user@example.com`", text)
            self.assertNotIn("configured.example", text)

    def test_create_cli_accepts_context_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = self.write_context_receipt(root)
            argv = ["status_report.py", "create", "--project-root", str(root), "--project-name", "Example", "--profile", "TEST", "--timestamp", "20260810T123456", "--context-file", str(receipt)]
            stdout = StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(stdout):
                exit_code = reporter.main()
            self.assertEqual(exit_code, 0)
            self.assertIn("https://effective.example", Path(stdout.getvalue().strip()).read_text(encoding="utf-8"))

    def test_context_receipt_accepts_utf8_bom_output_from_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = self.write_context_receipt(root)
            receipt.write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8-sig")
            self.assertIn("https://effective.example", reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", receipt).read_text(encoding="utf-8"))

    def test_context_receipt_rejects_missing_malformed_and_multiple_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", root / "missing.json")
            malformed = root / "malformed.json"
            malformed.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid JSON"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", malformed)
            receipt = self.write_context_receipt(root)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["profiles"].append(payload["profiles"][0].copy())
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one receipt"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", receipt)
            self.assertFalse((root / "status-reports").exists())

    def test_context_receipt_rejects_mismatched_host_and_principal_proofs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = self.write_context_receipt(root)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["profiles"][0]["effective_context"]["profile"] = "OTHER"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "effective host"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", receipt)
            receipt = self.write_context_receipt(root)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["profiles"][0]["effective_context"]["host"] = "https://wrong.example/path"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "effective host"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", receipt)
            receipt = self.write_context_receipt(root)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["profiles"][0]["current_user"]["principal"] = {"valid": True}
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current-user principal"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC", receipt)

    def test_completed_report_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(
                root,
                "Example Project",
                "TEST",
                "20260810T123456",
                7,
                "UTC",
            )
            self.complete_report(report)

            self.assertEqual(reporter.validate_report(report, root), [])

    def test_wrong_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "status-reports"
            report_dir.mkdir()
            report = report_dir / "status.md"
            report.write_text("# incomplete\n", encoding="utf-8")

            problems = reporter.validate_report(report, Path(temp_dir))

            self.assertIn("filename must match YYYYMMDDTHHmmSS-status.md", problems)

    def test_invalid_filename_timestamp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "status-reports"
            report_dir.mkdir()
            report = report_dir / "20260810T999999-status.md"
            report.write_text("# incomplete\n", encoding="utf-8")

            problems = reporter.validate_report(report, Path(temp_dir))

            self.assertTrue(any("invalid timestamp" in problem for problem in problems))

    def test_empty_report_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            text = re.sub(reporter.MARKER_RE, "Evidence recorded.", report.read_text(encoding="utf-8"))
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertTrue(any("section lacks substantive evidence" in problem for problem in problems))
            self.assertTrue(any("replace scaffold" in problem for problem in problems))

    def test_report_must_belong_to_supplied_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as other_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")

            problems = reporter.validate_report(report, Path(other_dir))

            self.assertTrue(any("project root" in problem for problem in problems))

    def test_unknown_timezone_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "unknown IANA timezone"):
                reporter.create_report(
                    Path(temp_dir),
                    "Example",
                    "TEST",
                    "20260810T123456",
                    30,
                    "Not/A_Timezone",
                )

    def test_nonexistent_project_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "typoed-project"

            with self.assertRaisesRegex(ValueError, "project root must already exist"):
                reporter.create_report(missing, "Example", "TEST", "20260810T123456", 30, "UTC")

            self.assertFalse(missing.exists())

    def test_blank_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "explicit Databricks CLI profile"):
                reporter.create_report(Path(temp_dir), "Example", "  ", "20260810T123456", 30, "UTC")

    def test_unverified_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "explicit Databricks CLI profile"):
                reporter.create_report(
                    Path(temp_dir),
                    "Example",
                    "Unverified",
                    "20260810T123456",
                    30,
                    "UTC",
                )

    def test_complete_day_windows_follow_schedule_timezone_across_dst(self) -> None:
        try:
            ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("IANA timezone data is unavailable")

        generated = reporter.parse_timestamp("2026-03-10T12:00:00Z")
        review_start, review_end, baseline_start, baseline_end = reporter.complete_day_windows(
            generated,
            3,
            "America/New_York",
        )

        self.assertEqual(review_end.isoformat(), "2026-03-10T00:00:00-04:00")
        self.assertEqual(review_start.isoformat(), "2026-03-07T00:00:00-05:00")
        self.assertEqual(baseline_end, review_start)
        self.assertEqual(baseline_start.isoformat(), "2026-03-04T00:00:00-05:00")

    def test_existing_report_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")

            with self.assertRaises(FileExistsError):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")

    def test_dirty_git_worktree_is_disclosed(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("Git is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            tracked = root / "project.txt"
            tracked.write_text("committed\n", encoding="utf-8")
            subprocess.run(["git", "add", "project.txt"], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Status Report Test",
                    "-c",
                    "user.email=status-report@example.invalid",
                    "commit",
                    "-qm",
                    "initial",
                ],
                cwd=root,
                check=True,
            )
            tracked.write_text("dirty\n", encoding="utf-8")

            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")

            self.assertIn("**Repository worktree:** `dirty`", text)
            self.assertEqual(reporter.validate_report(report, root), [])

    def test_symlinked_report_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as target_dir:
            root = Path(temp_dir)
            link = root / "status-reports"
            try:
                os.symlink(target_dir, link, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links are unavailable")

            with self.assertRaisesRegex(ValueError, "must not be a symbolic link"):
                reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")

    def test_validate_cli_returns_nonzero_for_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            argv = [
                "status_report.py",
                "validate",
                str(report),
                "--project-root",
                str(root),
            ]

            with patch.object(sys, "argv", argv), redirect_stdout(StringIO()):
                exit_code = reporter.main()

            self.assertEqual(exit_code, 1)

    def test_generated_timestamp_must_match_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8").replace(
                "`2026-08-10T12:34:56Z` UTC",
                "`2026-08-10T12:34:57Z` UTC",
            )
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn("filename timestamp does not match Generated at", problems)

    def test_windows_must_be_contiguous_and_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8").replace(
                "2026-06-11T00:00:00+00:00",
                "2026-06-12T00:00:00+00:00",
            )
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn("current and baseline windows must have equal calendar-day lengths", problems)

    def test_latest_window_must_end_at_latest_completed_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            replacements = {
                "2026-08-03T00:00:00+00:00": "2026-07-04T00:00:00+00:00",
                "2026-08-10T00:00:00+00:00": "2026-07-11T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00": "2026-06-27T00:00:00+00:00",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn(
                "latest-complete-days window must end at the latest completed local midnight",
                problems,
            )

    def test_reasoned_custom_window_can_be_historical(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            replacements = {
                "2026-08-03T00:00:00+00:00": "2026-07-04T00:00:00+00:00",
                "2026-08-10T00:00:00+00:00": "2026-07-11T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00": "2026-06-27T00:00:00+00:00",
                "`Latest complete days`": "`change-bounded: July incident review`",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)
            report.write_text(text, encoding="utf-8", newline="\n")

            self.assertEqual(reporter.validate_report(report, root), [])

    def test_health_states_and_dimensions_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            text = text.replace("- **Overall status:** `unknown`", "- **Overall status:** `fine`")
            text = text.replace(
                "| Reliability / uptime | Not applicable |",
                "| Reliability / uptime | Fine |",
            )
            text = text.replace("| Serving | Not applicable |", "")
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn("overall status must be healthy, watch, degraded, critical, or unknown", problems)
            self.assertTrue(any("invalid health scorecard status" in problem for problem in problems))
            self.assertTrue(any("health scorecard is missing dimensions" in problem for problem in problems))

    def test_evidence_coverage_contract_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            text = text.replace("| complete | No limitations", "| stable | No limitations")
            text = text.replace("pages=1; raw=0; unique=0", "one page")
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertTrue(any("invalid evidence coverage state" in problem for problem in problems))

    def test_findings_require_valid_unique_ids_severity_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            valid = "| F1 | Medium | High | job-1 / team | One bounded timeout | Tune timeout | Next three slots |"
            invalid = "| F1 | Maybe | Dunno | job-1 / team | One bounded timeout | Tune timeout | Next three slots |"
            text = text.replace(valid, f"{invalid}\n{invalid}")
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertTrue(any("finding IDs must be non-empty and unique" in problem for problem in problems))
            self.assertTrue(any("invalid finding severity" in problem for problem in problems))
            self.assertTrue(any("invalid finding confidence" in problem for problem in problems))

    def test_overall_status_cannot_understate_scorecard_severity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 7, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8")
            text = text.replace("- **Overall status:** `unknown`", "- **Overall status:** `healthy`")
            text = text.replace(
                "| Reliability / uptime | Not applicable |",
                "| Reliability / uptime | critical |",
            )
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn(
                "overall status must not be healthier than the most severe scorecard dimension",
                problems,
            )

    def test_required_headings_must_be_real_headings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = reporter.create_report(root, "Example", "TEST", "20260810T123456", 30, "UTC")
            self.complete_report(report)
            text = report.read_text(encoding="utf-8").replace(
                "## Executive Summary",
                "```text\n## Executive Summary\n```",
                1,
            )
            report.write_text(text, encoding="utf-8", newline="\n")

            problems = reporter.validate_report(report, root)

            self.assertIn("missing required heading: ## Executive Summary", problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
