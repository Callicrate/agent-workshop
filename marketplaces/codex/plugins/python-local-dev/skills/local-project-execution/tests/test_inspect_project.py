"""Behavioral tests for the local project inspection helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "inspect_project.py"


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

            report = self.inspect(root)

            if sys.platform == "win32":
                self.assertEqual(report["python"]["venv_python_candidates"], [])
                self.assertEqual(
                    report["python"]["foreign_venv_python_candidates"],
                    [str(python_path)],
                )
                self.assertEqual(report["python"]["recommended_runner"], "python")
            else:
                self.assertEqual(
                    report["python"]["venv_python_candidates"],
                    [str(python_path)],
                )
                self.assertEqual(
                    report["python"]["recommended_runner"], str(python_path)
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
            else:
                self.assertEqual(report["python"]["venv_python_candidates"], [])
                self.assertEqual(
                    report["python"]["foreign_venv_python_candidates"],
                    [str(python_path)],
                )
                self.assertEqual(report["python"]["recommended_runner"], "python")

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

                self.assertEqual(report["python"]["recommended_runner"], "python")
                self.assertIn(
                    f"{command} command is not available",
                    report["diagnostics"][0],
                )

    def test_reports_malformed_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text('{"name":', encoding="utf-8")

            report = self.inspect(root)

            self.assertEqual(report["javascript"]["metadata"], {})
            self.assertIn("invalid JSON", report["diagnostics"][0])

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
