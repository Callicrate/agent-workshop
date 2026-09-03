"""Offline regression coverage for DAB launch-run identity extraction."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = SKILL_ROOT / "scripts" / "extract_launch_run_id.py"
WORKFLOW_PATH = SKILL_ROOT / "references" / "deploy-monitor-workflow.md"
SPEC = importlib.util.spec_from_file_location("extract_launch_run_id", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPER
SPEC.loader.exec_module(HELPER)


class ExtractLaunchRunIdTests(unittest.TestCase):
    workspace_host = "https://workspace.example"

    def test_direct_launch_identity_beats_concurrent_newest_run(self) -> None:
        """The old list-runs --limit 1 fallback would select B, not launch A."""
        launch_a = "Run URL: https://workspace.example/?o=555#job/700/run/101\n"
        concurrent_newest_run_b = 202

        selected = HELPER.extract_launch_run_id(launch_a, self.workspace_host)

        self.assertEqual(selected, 101)
        self.assertNotEqual(selected, concurrent_newest_run_b)

    def test_current_cli_hash_url_accepts_a_normalized_matching_host(self) -> None:
        selected = HELPER.extract_launch_run_id(
            "Run URL: https://WORKSPACE.EXAMPLE/?o=555#job/700/run/101\n",
            self.workspace_host,
        )
        self.assertEqual(selected, 101)

    def test_canonical_job_run_url_is_accepted(self) -> None:
        selected = HELPER.extract_launch_run_id(
            "Run URL: https://workspace.example/jobs/700/runs/101\n",
            self.workspace_host,
        )
        self.assertEqual(selected, 101)

    def test_current_cli_canonical_url_with_workspace_id_is_accepted(self) -> None:
        selected = HELPER.extract_launch_run_id(
            "Run URL: https://workspace.example/jobs/700/runs/101?o=555\n",
            self.workspace_host,
        )
        self.assertEqual(selected, 101)

    def test_missing_or_ambiguous_launch_identity_rejects_without_guessing(self) -> None:
        for launch_output, expected_code in (
            ("bundle started\n", "monitoring_identity_missing"),
            (
                "Run URL: https://workspace.example/#job/700/run/101\n"
                "Run URL: https://workspace.example/#job/700/run/202\n",
                "monitoring_identity_ambiguous",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(HELPER.IdentityError) as raised:
                    HELPER.extract_launch_run_id(launch_output, self.workspace_host)
                self.assertEqual(raised.exception.code, expected_code)

    def test_run_url_must_match_the_verified_workspace_host(self) -> None:
        with self.assertRaises(HELPER.IdentityError) as raised:
            HELPER.extract_launch_run_id(
                "Run URL: https://other-workspace.example/?o=555#job/700/run/101\n",
                self.workspace_host,
            )
        self.assertEqual(raised.exception.code, "monitoring_identity_invalid_run_url")

    def test_malformed_url_is_a_concrete_blocker(self) -> None:
        with self.assertRaises(HELPER.IdentityError) as raised:
            HELPER.extract_launch_run_id(
                "Run URL: https://[broken/?o=555#job/700/run/101\n",
                self.workspace_host,
            )
        self.assertEqual(raised.exception.code, "monitoring_identity_invalid_run_url")

    def test_hash_url_query_and_origin_negative_matrix(self) -> None:
        invalid_urls = (
            "https://workspace.example/#job/700/run/101",
            "https://workspace.example/?o=0#job/700/run/101",
            "https://workspace.example/?o=not-a-number#job/700/run/101",
            "https://workspace.example/?o=555&o=556#job/700/run/101",
            "https://workspace.example/?x=555#job/700/run/101",
            "https://workspace.example/?o=555&#job/700/run/101",
            "https://workspace.example/jobs/700/runs/101?o=0",
            "https://workspace.example/jobs/700/runs/101?o=555&o=556",
            "https://workspace.example/jobs/700/runs/101?x=555",
            "https://workspace.example/jobs/700/runs/101?o=555#job/700/run/101",
            "https://user@workspace.example/?o=555#job/700/run/101",
            "https://workspace.example:443/?o=555#job/700/run/101",
            "https://workspace.example/unexpected?o=555#job/700/run/101",
        )
        for run_url in invalid_urls:
            with self.subTest(run_url=run_url):
                with self.assertRaises(HELPER.IdentityError) as raised:
                    HELPER.extract_launch_run_id(f"Run URL: {run_url}\n", self.workspace_host)
                self.assertEqual(
                    raised.exception.code, "monitoring_identity_invalid_run_url"
                )

    def test_launch_output_file_has_an_exact_bounded_binary_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch.txt"
            path.write_bytes(b"x" * HELPER.MAX_LAUNCH_OUTPUT_BYTES)
            self.assertEqual(
                len(HELPER._read_launch_output(path)), HELPER.MAX_LAUNCH_OUTPUT_BYTES
            )

            path.write_bytes(b"x" * (HELPER.MAX_LAUNCH_OUTPUT_BYTES + 1))
            with self.assertRaises(HELPER.IdentityError) as raised:
                HELPER._read_launch_output(path)
            self.assertEqual(
                raised.exception.code, "monitoring_identity_launch_output_too_large"
            )

    def test_workflow_preserves_failed_launch_exit_before_extraction(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("target bundle/project root containing `databricks.yml`", workflow)
        self.assertIn("Never write a capture into the skill package.", workflow)
        self.assertIn("$launchExitCode = $LASTEXITCODE", workflow)
        self.assertIn("if ($launchExitCode -ne 0)", workflow)
        self.assertIn("& python $extractor", workflow)
        self.assertLess(
            workflow.index("if ($launchExitCode -ne 0)"),
            workflow.index("& python $extractor"),
        )
        self.assertIn("$extractorExitCode = $LASTEXITCODE", workflow)
        self.assertIn("exit $extractorExitCode", workflow)
        self.assertNotIn("| tee bundle-run.launch.txt", workflow)

    def test_workflow_propagates_extractor_failure_after_finally_cleanup(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is required for the workflow execution regression")

        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        match = __import__("re").search(
            r"```powershell\r?\n(?P<code>.*?)```", workflow, __import__("re").DOTALL
        )
        self.assertIsNotNone(match)
        assert match is not None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "launch.txt"
            extractor = root / "scripts" / "extract_launch_run_id.py"
            extractor.parent.mkdir()
            extractor.write_text("raise SystemExit(2)\n", encoding="utf-8")
            quoted_capture = str(capture).replace("'", "''")
            quoted_root = str(root).replace("\\", "/").replace("'", "''")
            quoted_python = sys.executable.replace("'", "''")
            code = match.group("code")
            code = code.replace(
                '$launchCapture = Join-Path ([System.IO.Path]::GetTempPath()) "dab-launch-$([guid]::NewGuid().ToString(\'N\')).txt"',
                f"$launchCapture = '{quoted_capture}'",
            )
            code = (
                code.replace("<resource-key>", "resource")
                .replace("<profile>", "profile")
                .replace("<target>", "target")
                .replace("<verified-workspace-host>", "https://workspace.example")
                .replace("<skill-root>", quoted_root)
            )
            command = "\n".join(
                (
                    "$ErrorActionPreference = 'Continue'",
                    "function databricks { Write-Output 'Run URL: https://workspace.example/?o=555#job/700/run/101'; & cmd /c exit 0 }",
                    f"function python {{ & '{quoted_python}' @args }}",
                    code,
                )
            )
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(capture.exists(), "finally did not remove the capture")


if __name__ == "__main__":
    unittest.main()
