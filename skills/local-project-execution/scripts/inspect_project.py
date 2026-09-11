#!/usr/bin/env python3
"""Inspect a local project command surface using only the standard library."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional


PROJECT_MARKERS = {
    "package.json",
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "Makefile",
    "justfile",
}
VENV_NAMES = (".venv", "venv")
WINDOWS_VENV_PYTHON_PATHS = (Path("Scripts/python.exe"),)
POSIX_VENV_PYTHON_PATHS = (Path("bin/python"), Path("bin/python3"))


def find_upward(
    start: Path,
    names: set[str],
    *,
    ceiling: Optional[Path] = None,
) -> Optional[Path]:
    current = start.resolve()
    resolved_ceiling = ceiling.resolve() if ceiling is not None else None
    for candidate in [current, *current.parents]:
        if any((candidate / name).exists() for name in names):
            return candidate
        if candidate == resolved_ceiling:
            break
    return None


def read_package_metadata(package_json: Path) -> tuple[dict[str, Any], list[str]]:
    diagnostics: list[str] = []
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        diagnostics.append(
            f"{package_json}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        return {}, diagnostics
    except OSError as exc:
        diagnostics.append(f"{package_json}: could not be read: {exc}")
        return {}, diagnostics

    if not isinstance(data, dict):
        diagnostics.append(f"{package_json}: top-level JSON value must be an object")
        return {}, diagnostics

    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        scripts = {}

    metadata = {
        "name": data.get("name", ""),
        "version": data.get("version", ""),
        "type": data.get("type", ""),
        "package_manager": data.get("packageManager", ""),
        "main": data.get("main", ""),
        "bin": data.get("bin", {}),
        "exports": data.get("exports", {}),
        "files": data.get("files", []),
        "scripts": {str(key): str(value) for key, value in scripts.items()},
    }
    return metadata, diagnostics


def package_manager(root: Path, metadata: dict[str, Any]) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "package-lock.json").exists() or (root / "npm-shrinkwrap.json").exists():
        return "npm"
    declared_manager = metadata.get("package_manager")
    if isinstance(declared_manager, str):
        manager_name = declared_manager.partition("@")[0]
        if manager_name in {"npm", "pnpm", "yarn"}:
            return manager_name
    if (root / "package.json").exists():
        return "npm"
    return ""


def command_path(name: str) -> str:
    return shutil.which(name) or ""


def existing_names(root: Path, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if (root / name).exists()]


def venv_python_candidates(
    root: Path,
    venv_names: list[str],
) -> tuple[list[str], list[str]]:
    active_paths = (
        WINDOWS_VENV_PYTHON_PATHS
        if sys.platform == "win32"
        else POSIX_VENV_PYTHON_PATHS
    )
    foreign_paths = (
        POSIX_VENV_PYTHON_PATHS
        if sys.platform == "win32"
        else WINDOWS_VENV_PYTHON_PATHS
    )

    def existing(paths: tuple[Path, ...]) -> list[str]:
        return [
            str(candidate)
            for venv_name in venv_names
            for relative_path in paths
            if (candidate := root / venv_name / relative_path).exists()
        ]

    return existing(active_paths), existing(foreign_paths)


def recommended_python_runner(
    root: Path,
    venv_pythons: list[str],
    command_availability: dict[str, str],
) -> str:
    if (root / "uv.lock").exists() and command_availability["uv"]:
        return "uv run"
    if (root / "poetry.lock").exists() and command_availability["poetry"]:
        return "poetry run"
    if venv_pythons:
        return venv_pythons[0]
    return "python"


def inspect(start: Path) -> dict[str, Any]:
    cwd = start.expanduser().resolve()
    if not cwd.exists():
        raise FileNotFoundError(f"inspection path does not exist: {cwd}")
    if not cwd.is_dir():
        raise NotADirectoryError(f"inspection path is not a directory: {cwd}")

    git_root = find_upward(cwd, {".git"})
    if git_root is not None:
        repo_root = git_root
        project_root = find_upward(cwd, PROJECT_MARKERS, ceiling=repo_root) or repo_root
    else:
        repo_root = cwd
        project_root = find_upward(cwd, PROJECT_MARKERS) or repo_root
    package_json = project_root / "package.json"
    pyproject = project_root / "pyproject.toml"
    python_files = [
        name
        for name in (
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "uv.lock",
            "poetry.lock",
            "pytest.ini",
            "tox.ini",
        )
        if (project_root / name).exists()
    ]
    venvs = [name for name in VENV_NAMES if (project_root / name).exists()]
    venv_pythons, foreign_venv_pythons = venv_python_candidates(project_root, venvs)
    if package_json.exists():
        package_metadata, diagnostics = read_package_metadata(package_json)
    else:
        package_metadata, diagnostics = {}, []
    detected_package_manager = package_manager(project_root, package_metadata)
    command_availability = {
        "python": command_path("python"),
        "py": command_path("py"),
        "uv": command_path("uv"),
        "poetry": command_path("poetry"),
        "node": command_path("node"),
        "npm": command_path("npm"),
        "pnpm": command_path("pnpm"),
        "yarn": command_path("yarn"),
        "wsl": command_path("wsl"),
    }
    if (project_root / "uv.lock").exists() and not command_availability["uv"]:
        diagnostics.append("uv.lock exists, but the uv command is not available")
    if (project_root / "poetry.lock").exists() and not command_availability["poetry"]:
        diagnostics.append(
            "poetry.lock exists, but the poetry command is not available"
        )
    return {
        "cwd": str(cwd),
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "diagnostics": diagnostics,
        "command_availability": command_availability,
        "javascript": {
            "package_json": str(package_json) if package_json.exists() else "",
            "package_manager": detected_package_manager,
            "lockfiles": existing_names(
                project_root,
                (
                    "pnpm-lock.yaml",
                    "yarn.lock",
                    "package-lock.json",
                    "npm-shrinkwrap.json",
                ),
            ),
            "metadata": package_metadata,
            "scripts": package_metadata.get("scripts", {}),
            "build_outputs": existing_names(
                project_root, ("dist", "build", "lib", "out")
            ),
            "pack_command": "npm pack --dry-run"
            if detected_package_manager == "npm"
            else "",
        },
        "python": {
            "config_files": python_files,
            "virtualenv_candidates": venvs,
            "venv_python_candidates": venv_pythons,
            "foreign_venv_python_candidates": foreign_venv_pythons,
            "pyproject": str(pyproject) if pyproject.exists() else "",
            "python_version_file": str(project_root / ".python-version")
            if (project_root / ".python-version").exists()
            else "",
            "recommended_runner": recommended_python_runner(
                project_root,
                venv_pythons,
                command_availability,
            ),
        },
        "task_files": [
            str(path)
            for path in (
                project_root / "Makefile",
                project_root / "justfile",
                project_root / ".vscode" / "tasks.json",
            )
            if path.exists()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect likely local project commands"
    )
    parser.add_argument("path", nargs="?", default=".", help="Directory to inspect")
    args = parser.parse_args()
    try:
        result = inspect(Path(args.path))
    except (FileNotFoundError, NotADirectoryError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
