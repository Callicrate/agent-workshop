"""Behavioral tests for the local project inspection helper."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "inspect_project.py"


def load_inspector_module():
    spec = importlib.util.spec_from_file_location("inspect_project", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("project inspector could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_inspector(
    path: Path,
    *,
    path_environment: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = None
    if path_environment is not None:
        environment = {**os.environ, "PATH": path_environment}
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(path)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )


class InspectProjectTests(unittest.TestCase):
    def inspect(self, path: Path) -> dict[str, Any]:
        result = run_inspector(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_nonvenv_runner(self, report: dict[str, Any]) -> None:
        availability = report["command_availability"]
        runner = report["python"]["recommended_runner"]
        status = report["python"]["recommended_runner_status"]
        if availability["python"]:
            self.assertEqual(
                (runner, status), (availability["python"], "python_resolved")
            )
        elif availability["py"]:
            self.assertEqual((runner, status), (availability["py"], "py_resolved"))
        elif Path(sys.executable).is_file():
            self.assertEqual(
                (runner, status),
                (sys.executable, "current_interpreter_fallback"),
            )
        else:
            self.assertEqual((runner, status), ("", "unavailable"))

    def test_recommends_posix_virtualenv_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'demo'\n",
                encoding="utf-8",
            )
            python_path = root / ".venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.touch()
            if sys.platform != "win32":
                python_path.chmod(python_path.stat().st_mode | stat.S_IXUSR)

            report = self.inspect(root)

            if sys.platform == "win32":
                self.assertEqual(report["python"]["venv_python_candidates"], [])
                self.assertEqual(
                    report["python"]["foreign_venv_python_candidates"],
                    [str(python_path)],
                )
                self.assert_nonvenv_runner(report)
            else:
                self.assertEqual(
                    report["python"]["venv_python_candidates"],
                    [str(python_path)],
                )
                self.assertEqual(
                    report["python"]["recommended_runner"], str(python_path)
                )
                self.assertEqual(
                    report["python"]["recommended_runner_status"],
                    "venv_candidate_permission_checked",
                )

    def test_preserves_windows_virtualenv_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").touch()
            python_path = root / "venv" / "Scripts" / "python.exe"
            python_path.parent.mkdir(parents=True)
            python_path.touch()

            report = self.inspect(root)

            if sys.platform == "win32":
                self.assertEqual(
                    report["python"]["venv_python_candidates"],
                    [str(python_path)],
                )
                self.assertEqual(
                    report["python"]["recommended_runner"], str(python_path)
                )
                self.assertEqual(
                    report["python"]["recommended_runner_status"],
                    "venv_candidate_file",
                )
            else:
                self.assertEqual(report["python"]["venv_python_candidates"], [])
                self.assertEqual(
                    report["python"]["foreign_venv_python_candidates"],
                    [str(python_path)],
                )
                self.assert_nonvenv_runner(report)

    def test_does_not_recommend_unavailable_environment_manager(self) -> None:
        for lockfile, command in (("uv.lock", "uv"), ("poetry.lock", "poetry")):
            with (
                self.subTest(lockfile=lockfile),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                (root / lockfile).touch()

                result = run_inspector(root, path_environment="")
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)

                self.assertEqual(report["python"]["recommended_runner"], sys.executable)
                self.assertEqual(
                    report["python"]["recommended_runner_status"],
                    "current_interpreter_fallback",
                )
                self.assertIn(
                    f"{command} command is not available",
                    report["diagnostics"][0],
                )

    def test_ignores_directory_as_virtualenv_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").touch()
            candidate = root / "venv" / "Scripts" / "python.exe"
            candidate.mkdir(parents=True)

            report = self.inspect(root)

            self.assertNotIn(str(candidate), report["python"]["venv_python_candidates"])
            self.assertNotIn(
                str(candidate), report["python"]["foreign_venv_python_candidates"]
            )
            self.assert_nonvenv_runner(report)

    @unittest.skipIf(sys.platform == "win32", "POSIX executable bits are not active")
    def test_ignores_nonexecutable_posix_virtualenv_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").touch()
            candidate = root / ".venv" / "bin" / "python"
            candidate.parent.mkdir(parents=True)
            candidate.touch()
            candidate.chmod(stat.S_IRUSR | stat.S_IWUSR)

            report = self.inspect(root)

            self.assertEqual(report["python"]["venv_python_candidates"], [])
            self.assert_nonvenv_runner(report)

    def test_reports_unavailable_runner_without_a_current_interpreter(self) -> None:
        inspector = load_inspector_module()
        unavailable_commands = {
            "python": "",
            "py": "",
            "uv": "",
            "poetry": "",
        }
        with patch.object(inspector.sys, "executable", ""):
            self.assertEqual(
                inspector.recommended_python_runner(
                    Path("."), [], unavailable_commands
                ),
                ("", "unavailable"),
            )

    def test_reports_malformed_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text('{"name":', encoding="utf-8")

            report = self.inspect(root)

            self.assertEqual(report["javascript"]["metadata"], {})
            self.assertIn("invalid JSON", report["diagnostics"][0])
            self.assertIn("package_json_invalid_json", report["diagnostic_codes"])

    def test_projects_manifest_without_echoing_command_or_package_payloads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = "SYNTHETIC_COMMAND_BODY_MARKER"
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "version": "1.2.3",
                        "type": "module",
                        "packageManager": "npm@10.0.0",
                        "main": "dist/index.js",
                        "scripts": {
                            "build": marker,
                            "test": f"echo {marker}",
                        },
                        "bin": {"demo": marker},
                        "exports": {".": marker},
                        "files": [marker],
                    }
                ),
                encoding="utf-8",
            )

            result = run_inspector(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(marker, result.stdout)
            report = json.loads(result.stdout)

            javascript = report["javascript"]
            self.assertEqual(javascript["manifest_projection_version"], 2)
            self.assertEqual(
                javascript["metadata"],
                {
                    "name": "demo",
                    "version": "1.2.3",
                    "type": "module",
                    "package_manager": "npm@10.0.0",
                    "main": "dist/index.js",
                    "truncated_fields": [],
                },
            )
            self.assertEqual(
                javascript["scripts"],
                {"count": 2, "names": ["build", "test"], "truncated": False},
            )
            self.assertEqual(
                javascript["omissions"],
                {
                    "script_commands": True,
                    "manifest_fields": ["bin", "exports", "files"],
                },
            )

    def test_bounds_manifest_read_and_reports_parse_failures_by_code(self) -> None:
        cases = {
            "invalid_utf8": (b"\xff", "package_json_invalid_utf8"),
            "nonfinite": (b'{"score": NaN}', "package_json_nonfinite_json"),
            "overflowed_number": (b'{"score": 1e9999}', "package_json_nonfinite_json"),
            "oversized_integer": (
                b'{"count": ' + b"9" * 5_000 + b"}",
                "package_json_invalid_json",
            ),
            "non_object": (b"[]", "package_json_non_object"),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            for name, (contents, expected_code) in cases.items():
                with self.subTest(name=name):
                    manifest.write_bytes(contents)
                    report = self.inspect(root)
                    self.assertEqual(report["javascript"]["metadata"], {})
                    self.assertIn(expected_code, report["diagnostic_codes"])

            nested: object = {}
            for _ in range(33):
                nested = {"child": nested}
            manifest.write_text(json.dumps(nested), encoding="utf-8")
            report = self.inspect(root)
            self.assertIn("package_json_too_deep", report["diagnostic_codes"])

            oversized_marker = "SYNTHETIC_OVERSIZE_MANIFEST_MARKER"
            manifest.write_bytes(
                b'{"payload":"'
                + oversized_marker.encode("ascii")
                + b"x" * (4 * 1024 * 1024)
                + b'"}'
            )
            result = run_inspector(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stdout), 100_000)
            self.assertNotIn(oversized_marker, result.stdout)
            report = json.loads(result.stdout)
            self.assertIn("package_json_oversize", report["diagnostic_codes"])

    def test_reports_manifest_projection_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "n" * 513,
                        "scripts": {
                            f"task-{index:03d}": "ignored" for index in range(201)
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = self.inspect(root)
            javascript = report["javascript"]
            self.assertEqual(len(javascript["metadata"]["name"]), 512)
            self.assertEqual(javascript["metadata"]["truncated_fields"], ["name"])
            self.assertEqual(javascript["scripts"]["count"], 201)
            self.assertEqual(len(javascript["scripts"]["names"]), 200)
            self.assertTrue(javascript["scripts"]["truncated"])

    def test_omits_pack_command_when_detected_manager_is_unavailable(self) -> None:
        for lockfile, manager in (
            ("package-lock.json", "npm"),
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
        ):
            with (
                self.subTest(manager=manager),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                (root / "package.json").write_text("{}", encoding="utf-8")
                (root / lockfile).touch()

                result = run_inspector(root, path_environment="")
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)

                javascript = report["javascript"]
                self.assertEqual(javascript["package_manager"], manager)
                self.assertFalse(javascript["package_manager_available"])
                self.assertEqual(javascript["pack_command"], "")
                self.assertEqual(
                    javascript["pack_command_status"], "command_unavailable"
                )
                self.assertIn(
                    f"{manager}_command_unavailable", report["diagnostic_codes"]
                )

    def test_uses_declared_package_manager_without_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps({"name": "demo", "packageManager": "pnpm@10.0.0"}),
                encoding="utf-8",
            )

            report = self.inspect(root)

            self.assertEqual(report["javascript"]["package_manager"], "pnpm")
            self.assertEqual(report["javascript"]["pack_command"], "")

    def test_rejects_missing_inspection_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            result = run_inspector(missing)

            self.assertEqual(result.returncode, 2)
            self.assertIn("inspection path does not exist", result.stderr)

    def test_inner_repo_excludes_project_marker_above_its_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outer = Path(temp_dir)
            (outer / "pyproject.toml").touch()
            inner = outer / "inner"
            (inner / ".git").mkdir(parents=True)
            cwd = inner / "src"
            cwd.mkdir()

            report = self.inspect(cwd)

            self.assertEqual(report["repo_root"], str(inner))
            self.assertEqual(report["project_root"], str(inner))

    def test_selects_nearest_project_marker_inside_monorepo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "pyproject.toml").touch()
            package = root / "packages" / "worker"
            (package / "package.json").parent.mkdir(parents=True)
            (package / "package.json").write_text("{}", encoding="utf-8")
            cwd = package / "src"
            cwd.mkdir()

            report = self.inspect(cwd)

            self.assertEqual(report["repo_root"], str(root))
            self.assertEqual(report["project_root"], str(package))

    def test_git_root_is_project_fallback_for_git_directory_and_file(self) -> None:
        for git_kind in ("directory", "file"):
            with (
                self.subTest(git_kind=git_kind),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                git_marker = root / ".git"
                if git_kind == "directory":
                    git_marker.mkdir()
                else:
                    git_marker.write_text("gitdir: ../worktree.git\n", encoding="utf-8")
                cwd = root / "nested"
                cwd.mkdir()

                report = self.inspect(cwd)

                self.assertEqual(report["repo_root"], str(root))
                self.assertEqual(report["project_root"], str(root))

    def test_no_git_root_keeps_unrestricted_marker_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pyproject.toml").touch()
            cwd = root / "nested" / "deeper"
            cwd.mkdir(parents=True)

            report = self.inspect(cwd)

            self.assertEqual(report["repo_root"], str(cwd))
            self.assertEqual(report["project_root"], str(root))

    def test_cli_json_is_deterministic_for_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()

            first = run_inspector(root)
            second = run_inspector(root)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(json.loads(first.stdout)["project_root"], str(root))


if __name__ == "__main__":
    unittest.main()
