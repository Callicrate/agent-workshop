#!/usr/bin/env python3
"""Inspect a local project command surface using only the standard library."""

from __future__ import annotations

import argparse
import json
from math import isfinite
import os
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
PACKAGE_JSON_MAX_BYTES = 1024 * 1024
PACKAGE_JSON_MAX_DEPTH = 32
PACKAGE_SCALAR_MAX_CHARS = 512
PACKAGE_SCRIPT_NAME_MAX_CHARS = 128
PACKAGE_SCRIPT_NAME_MAX_COUNT = 200
DIAGNOSTIC_TEXT = {
    "package_json_unreadable": "package.json could not be read",
    "package_json_oversize": "package.json exceeds the 1048576-byte inspection limit",
    "package_json_invalid_utf8": "package.json is not valid UTF-8",
    "package_json_nonfinite_json": "package.json contains non-finite JSON values",
    "package_json_too_deep": "package.json exceeds the supported nesting depth",
    "package_json_invalid_json": "package.json contains invalid JSON",
    "package_json_non_object": "package.json top-level JSON value must be an object",
    "uv_command_unavailable": "uv.lock exists, but the uv command is not available",
    "poetry_command_unavailable": "poetry.lock exists, but the poetry command is not available",
}


class NonFiniteJsonError(ValueError):
    """Raised when a package manifest uses a non-finite JSON extension."""


def add_diagnostic(
    diagnostics: list[str], diagnostic_codes: list[str], code: str
) -> None:
    """Add compatible fixed text plus a machine-readable diagnostic code."""

    diagnostics.append(DIAGNOSTIC_TEXT[code])
    diagnostic_codes.append(code)


def _reject_nonfinite_json(_: str) -> Any:
    raise NonFiniteJsonError


def _exceeds_json_depth(value: Any) -> bool:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > PACKAGE_JSON_MAX_DEPTH:
            return True
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return False


def _contains_nonfinite_json(value: Any) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not isfinite(current):
            return True
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _bounded_text(value: Any, maximum_characters: int) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    return value[:maximum_characters], len(value) > maximum_characters


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


def read_package_metadata(
    package_json: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    """Return a bounded, safe package manifest projection and diagnostics."""

    diagnostics: list[str] = []
    diagnostic_codes: list[str] = []
    try:
        with package_json.open("rb") as manifest_file:
            raw_data = manifest_file.read(PACKAGE_JSON_MAX_BYTES + 1)
    except OSError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_unreadable")
        return {}, {}, diagnostics, diagnostic_codes
    if len(raw_data) > PACKAGE_JSON_MAX_BYTES:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_oversize")
        return {}, {}, diagnostics, diagnostic_codes
    try:
        manifest_text = raw_data.decode("utf-8")
    except UnicodeDecodeError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_invalid_utf8")
        return {}, {}, diagnostics, diagnostic_codes
    try:
        data = json.loads(manifest_text, parse_constant=_reject_nonfinite_json)
    except NonFiniteJsonError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_nonfinite_json")
        return {}, {}, diagnostics, diagnostic_codes
    except RecursionError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_too_deep")
        return {}, {}, diagnostics, diagnostic_codes
    except json.JSONDecodeError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_invalid_json")
        return {}, {}, diagnostics, diagnostic_codes
    except ValueError:
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_invalid_json")
        return {}, {}, diagnostics, diagnostic_codes

    if not isinstance(data, dict):
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_non_object")
        return {}, {}, diagnostics, diagnostic_codes
    if _exceeds_json_depth(data):
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_too_deep")
        return {}, {}, diagnostics, diagnostic_codes
    if _contains_nonfinite_json(data):
        add_diagnostic(diagnostics, diagnostic_codes, "package_json_nonfinite_json")
        return {}, {}, diagnostics, diagnostic_codes

    scripts = data.get("scripts")
    script_names = sorted(scripts) if isinstance(scripts, dict) else []
    bounded_script_names: list[str] = []
    names_truncated = len(script_names) > PACKAGE_SCRIPT_NAME_MAX_COUNT
    for name in script_names[:PACKAGE_SCRIPT_NAME_MAX_COUNT]:
        bounded_name, name_truncated = _bounded_text(
            name, PACKAGE_SCRIPT_NAME_MAX_CHARS
        )
        bounded_script_names.append(bounded_name)
        names_truncated = names_truncated or name_truncated

    metadata: dict[str, Any] = {}
    truncated_fields: list[str] = []
    for source_name, output_name in (
        ("name", "name"),
        ("version", "version"),
        ("type", "type"),
        ("packageManager", "package_manager"),
        ("main", "main"),
    ):
        bounded_value, was_truncated = _bounded_text(
            data.get(source_name), PACKAGE_SCALAR_MAX_CHARS
        )
        metadata[output_name] = bounded_value
        if was_truncated:
            truncated_fields.append(output_name)
    metadata["truncated_fields"] = truncated_fields

    omitted_fields = [field for field in ("bin", "exports", "files") if field in data]
    projection = {
        "scripts": {
            "count": len(script_names),
            "names": bounded_script_names,
            "truncated": names_truncated,
        },
        "omissions": {
            "script_commands": "scripts" in data,
            "manifest_fields": omitted_fields,
        },
    }

    return metadata, projection, diagnostics, diagnostic_codes


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


def package_pack_command(
    manager: str,
    command_availability: dict[str, str],
    diagnostics: list[str],
    diagnostic_codes: list[str],
) -> tuple[str, str]:
    """Return a command only when its package-manager executable resolves."""

    if not manager:
        return "", "not_applicable"
    if not command_availability[manager]:
        diagnostics.append(f"{manager} package manager is not available")
        diagnostic_codes.append(f"{manager}_command_unavailable")
        return "", "command_unavailable"
    if manager == "npm":
        return "npm pack --dry-run", "available"
    return "", "not_recommended_for_manager"


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

    def existing(paths: tuple[Path, ...], *, require_executable: bool) -> list[str]:
        return [
            str(candidate)
            for venv_name in venv_names
            for relative_path in paths
            if (candidate := root / venv_name / relative_path).is_file()
            and (not require_executable or os.access(candidate, os.X_OK))
        ]

    return (
        existing(active_paths, require_executable=sys.platform != "win32"),
        existing(foreign_paths, require_executable=False),
    )


def current_python_executable() -> str:
    """Return the current interpreter only when its static path is a file."""

    if not sys.executable:
        return ""
    candidate = Path(sys.executable)
    return str(candidate) if candidate.is_file() else ""


def recommended_python_runner(
    root: Path,
    venv_pythons: list[str],
    command_availability: dict[str, str],
) -> tuple[str, str]:
    if (root / "uv.lock").exists() and command_availability["uv"]:
        return "uv run", "uv_resolved"
    if (root / "poetry.lock").exists() and command_availability["poetry"]:
        return "poetry run", "poetry_resolved"
    if venv_pythons:
        return (
            venv_pythons[0],
            "venv_candidate_file"
            if sys.platform == "win32"
            else "venv_candidate_permission_checked",
        )
    if command_availability["python"]:
        return command_availability["python"], "python_resolved"
    if command_availability["py"]:
        return command_availability["py"], "py_resolved"
    if current_executable := current_python_executable():
        return current_executable, "current_interpreter_fallback"
    return "", "unavailable"


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
        package_metadata, package_projection, diagnostics, diagnostic_codes = (
            read_package_metadata(package_json)
        )
    else:
        package_metadata, package_projection, diagnostics, diagnostic_codes = (
            {},
            {},
            [],
            [],
        )
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
        add_diagnostic(diagnostics, diagnostic_codes, "uv_command_unavailable")
    if (project_root / "poetry.lock").exists() and not command_availability["poetry"]:
        add_diagnostic(diagnostics, diagnostic_codes, "poetry_command_unavailable")
    pack_command, pack_command_status = package_pack_command(
        detected_package_manager,
        command_availability,
        diagnostics,
        diagnostic_codes,
    )
    recommended_runner, recommended_runner_status = recommended_python_runner(
        project_root,
        venv_pythons,
        command_availability,
    )
    return {
        "cwd": str(cwd),
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "diagnostics": diagnostics,
        "diagnostic_codes": diagnostic_codes,
        "command_availability": command_availability,
        "javascript": {
            "manifest_projection_version": 2,
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
            "scripts": package_projection.get(
                "scripts", {"count": 0, "names": [], "truncated": False}
            ),
            "omissions": package_projection.get(
                "omissions", {"script_commands": False, "manifest_fields": []}
            ),
            "build_outputs": existing_names(
                project_root, ("dist", "build", "lib", "out")
            ),
            "package_manager_available": bool(
                detected_package_manager
                and command_availability[detected_package_manager]
            ),
            "pack_command": pack_command,
            "pack_command_status": pack_command_status,
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
            "recommended_runner": recommended_runner,
            "recommended_runner_status": recommended_runner_status,
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
