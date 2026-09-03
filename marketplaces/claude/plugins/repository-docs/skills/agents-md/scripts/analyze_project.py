#!/usr/bin/env python3
"""Analyze a project to bootstrap AGENTS.md creation.

This script scans a project directory to identify:
- Languages and frameworks from config files
- Linter/formatter configurations
- Naming conventions from source files
- Testing patterns from test files
- Domain terminology from comments and docs

Usage:
    python analyze_project.py /path/to/project
    python analyze_project.py /path/to/project --output json
    python analyze_project.py /path/to/project --output markdown

Output:
    Structured report suitable for AGENTS.md generation.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from bounded_io import (
    ArgumentParseError,
    InputFileError,
    ValueFreeArgumentParser,
    command_line_error,
    error_envelope,
    is_within,
    read_repo_text,
    resolve_repo_root,
)
from codex_instruction_sources import (
    DEFAULT_PROJECT_DOC_BYTE_LIMIT,
    audit_codex_project_instruction_sources,
    markdown_code_span,
)

# =============================================================================
# Configuration File Detection
# =============================================================================

CONFIG_FILES: dict[str, dict[str, Any]] = {
    # Python
    "pyproject.toml": {"language": "Python", "frameworks": ["detect"]},
    "setup.py": {"language": "Python"},
    "requirements.txt": {"language": "Python"},
    "Pipfile": {"language": "Python"},
    "poetry.lock": {"language": "Python", "tools": ["Poetry"]},
    "uv.lock": {"language": "Python", "tools": ["uv"]},
    # JavaScript/TypeScript
    "package.json": {"language": "JavaScript/TypeScript", "frameworks": ["detect"]},
    "tsconfig.json": {"language": "TypeScript"},
    "deno.json": {"language": "TypeScript", "runtime": "Deno"},
    # Rust
    "Cargo.toml": {"language": "Rust"},
    # Go
    "go.mod": {"language": "Go"},
    # Java/Kotlin
    "pom.xml": {"language": "Java", "tools": ["Maven"]},
    "build.gradle": {"language": "Java/Kotlin", "tools": ["Gradle"]},
    "build.gradle.kts": {"language": "Kotlin", "tools": ["Gradle"]},
    # .NET
    "*.csproj": {"language": "C#"},
    "*.fsproj": {"language": "F#"},
    # Ruby
    "Gemfile": {"language": "Ruby"},
    # PHP
    "composer.json": {"language": "PHP"},
    # Databricks
    "databricks.yml": {"platform": "Databricks", "tools": ["Asset Bundles"]},
}

LOCKFILES: dict[str, str] = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "Yarn",
    "bun.lock": "Bun",
    "bun.lockb": "Bun",
    "uv.lock": "uv",
    "poetry.lock": "Poetry",
    "Pipfile.lock": "Pipenv",
    "Cargo.lock": "Cargo",
    "go.sum": "Go modules",
    "Gemfile.lock": "Bundler",
    "composer.lock": "Composer",
}

LINTER_FILES: dict[str, dict[str, str]] = {
    # Python
    ".ruff.toml": {"tool": "Ruff", "language": "Python"},
    "ruff.toml": {"tool": "Ruff", "language": "Python"},
    ".pylintrc": {"tool": "Pylint", "language": "Python"},
    "pyrightconfig.json": {"tool": "Pyright", "language": "Python"},
    "mypy.ini": {"tool": "mypy", "language": "Python"},
    ".flake8": {"tool": "Flake8", "language": "Python"},
    # JavaScript/TypeScript
    ".eslintrc": {"tool": "ESLint", "language": "JavaScript/TypeScript"},
    ".eslintrc.js": {"tool": "ESLint", "language": "JavaScript/TypeScript"},
    ".eslintrc.json": {"tool": "ESLint", "language": "JavaScript/TypeScript"},
    "eslint.config.js": {"tool": "ESLint", "language": "JavaScript/TypeScript"},
    "eslint.config.mjs": {"tool": "ESLint", "language": "JavaScript/TypeScript"},
    # Formatters
    ".prettierrc": {"tool": "Prettier", "language": "JavaScript/TypeScript"},
    ".prettierrc.json": {"tool": "Prettier", "language": "JavaScript/TypeScript"},
    # SQL
    ".sqlfluff": {"tool": "SQLFluff", "language": "SQL"},
    # Rust
    "rustfmt.toml": {"tool": "rustfmt", "language": "Rust"},
    "clippy.toml": {"tool": "Clippy", "language": "Rust"},
}

TEST_PATTERNS: dict[str, dict[str, str]] = {
    "pytest.ini": {"framework": "pytest", "language": "Python"},
    "conftest.py": {"framework": "pytest", "language": "Python"},
    "jest.config.js": {"framework": "Jest", "language": "JavaScript/TypeScript"},
    "jest.config.ts": {"framework": "Jest", "language": "TypeScript"},
    "vitest.config.ts": {"framework": "Vitest", "language": "TypeScript"},
    "vitest.config.js": {"framework": "Vitest", "language": "JavaScript"},
    "playwright.config.ts": {"framework": "Playwright", "language": "TypeScript"},
    "cypress.config.ts": {"framework": "Cypress", "language": "TypeScript"},
}

SKIP_DIR_NAMES: set[str] = {
    ".git",
    ".gradle",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "generated",
    "htmlcov",
    "node_modules",
    "out",
    "site-packages",
    "target",
    "tmp",
    "vendor",
    "vendors",
    "venv",
}

MAX_NAMING_FILES = 10
MAX_PATTERN_FILES = 20
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_DIRECTORIES = 2_000
DEFAULT_MAX_ENTRIES = 20_000
DEFAULT_MAX_FILE_BYTES = 262_144
DEFAULT_MAX_TOTAL_BYTES = 4_194_304
SOURCE_SUFFIXES = {
    ".cs",
    ".fs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sql",
    ".ts",
    ".tsx",
}
GENERATED_BOUNDARY_DIR_NAMES = {
    "generated",
    "gen",
    "vendor",
    "vendors",
    "migrations",
    "schema",
    "schemas",
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ProjectAnalysis:
    """Complete project analysis result."""

    schema_version: str = "1.3"
    project_path: str = ""
    agents_files: list[str] = field(default_factory=list)
    codex_project_instruction_audit: dict[str, Any] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    package_managers: list[dict[str, str]] = field(default_factory=list)
    command_inventory: list[dict[str, str]] = field(default_factory=list)
    python_version_hints: list[dict[str, str]] = field(default_factory=list)
    linters: list[dict[str, str]] = field(default_factory=list)
    formatters: list[dict[str, str]] = field(default_factory=list)
    test_frameworks: list[dict[str, str]] = field(default_factory=list)
    naming_conventions: dict[str, str] = field(default_factory=dict)
    config_files: list[str] = field(default_factory=list)
    generated_candidates: list[str] = field(default_factory=list)
    source_files_sampled: int = 0
    detected_patterns: list[str] = field(default_factory=list)
    detected_facts: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    uncertainty_items: list[dict[str, Any]] = field(default_factory=list)
    scan_summary: dict[str, Any] = field(default_factory=dict)
    analysis_certainty: str = "complete"


@dataclass(frozen=True)
class ScanLimits:
    """Deterministic limits for one repository inventory pass."""

    max_directories: int = DEFAULT_MAX_DIRECTORIES
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES


@dataclass
class ScanTracker:
    """Aggregate bounded-scan observations without retaining file content."""

    limits: ScanLimits
    max_directories_seen: int = 0
    max_entries_seen: int = 0
    files_read: int = 0
    bytes_read: int = 0
    truncated: bool = False
    text_cache: dict[Path, str] = field(default_factory=dict)


def add_uncertainty(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    """Append one diagnostic once while preserving stable discovery order."""
    identity = (
        item.get("kind"),
        item.get("code"),
        item.get("path"),
        item.get("message"),
    )
    if any(
        (
            existing.get("kind"),
            existing.get("code"),
            existing.get("path"),
            existing.get("message"),
        )
        == identity
        for existing in items
    ):
        return
    items.append(item)


def safe_relative(path: Path, project_path: Path) -> str:
    """Render a lexical project-relative path without resolving it outside."""
    try:
        return path.relative_to(project_path).as_posix()
    except ValueError:
        return path.name


def read_analysis_text(
    project_path: Path,
    path: Path,
    *,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> str | None:
    """Read one candidate within per-file and cumulative analyzer limits."""
    relative = safe_relative(path, project_path)
    try:
        cache_key = path.resolve(strict=True)
    except OSError:
        cache_key = path.absolute()
    if cache_key in tracker.text_cache:
        return tracker.text_cache[cache_key]
    remaining = tracker.limits.max_total_bytes - tracker.bytes_read
    if remaining < 1:
        tracker.truncated = True
        add_uncertainty(
            uncertainty_items,
            {
                "kind": "scan-truncated",
                "code": "total-byte-limit",
                "message": "Content inspection stopped at the cumulative byte limit",
            },
        )
        return None
    limit = min(tracker.limits.max_file_bytes, remaining)
    try:
        _, text, byte_count = read_repo_text(
            project_path,
            path,
            label="analysis-candidate",
            byte_limit=limit,
        )
    except InputFileError as exc:
        is_too_large = exc.code.endswith("too-large")
        if is_too_large:
            tracker.truncated = True
        if is_too_large and remaining < tracker.limits.max_file_bytes:
            code = "total-byte-limit"
            message = "Content inspection stopped at the cumulative byte limit"
        elif is_too_large:
            code = "per-file-byte-limit"
            message = "A candidate exceeded the per-file byte limit"
        else:
            code = exc.code
            message = exc.message
        add_uncertainty(
            uncertainty_items,
            {
                "kind": "scan-truncated" if is_too_large else "unreadable-candidate",
                "code": code,
                "path": relative,
                "message": message,
            },
        )
        return None
    tracker.files_read += 1
    tracker.bytes_read += byte_count
    tracker.text_cache[cache_key] = text
    return text


def is_directory_alias(path: Path) -> bool:
    """Recognize symlink and Windows junction directory aliases."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def walk_project_entries(
    project_path: Path,
    *,
    max_depth: int | None,
    follow_symlinks: bool,
    include_boundary_directories: bool,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Yield bounded, sorted, contained directory entries without ``os.walk`` lists."""
    stack: list[tuple[Path, int]] = [(project_path, 0)]
    seen_directories: set[tuple[int, int]] = set()
    directories_seen = 0
    entries_seen = 0

    while stack:
        if directories_seen >= tracker.limits.max_directories:
            tracker.truncated = True
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "scan-truncated",
                    "code": "directory-limit",
                    "message": "Repository inventory stopped at the directory limit",
                },
            )
            return

        root_path, depth = stack.pop()
        try:
            resolved_root = root_path.resolve(strict=True)
            root_stat = resolved_root.stat()
        except OSError:
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "unreadable-candidate",
                    "code": "directory-unavailable",
                    "path": safe_relative(root_path, project_path),
                    "message": "A repository directory could not be inspected safely",
                },
            )
            continue
        if not is_within(resolved_root, project_path):
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "excluded-alias",
                    "code": "outside-root-directory-alias",
                    "path": safe_relative(root_path, project_path),
                    "message": "A directory alias resolving outside the repository was excluded",
                },
            )
            continue
        identity = (root_stat.st_dev, root_stat.st_ino)
        if identity in seen_directories:
            continue
        seen_directories.add(identity)
        directories_seen += 1
        tracker.max_directories_seen = max(
            tracker.max_directories_seen, directories_seen
        )

        directory_names: list[str] = []
        file_names: list[str] = []
        entry_limit_reached = False
        try:
            with os.scandir(root_path) as entries:
                for entry in entries:
                    if entries_seen >= tracker.limits.max_entries:
                        entry_limit_reached = True
                        tracker.truncated = True
                        add_uncertainty(
                            uncertainty_items,
                            {
                                "kind": "scan-truncated",
                                "code": "entry-limit",
                                "message": "Repository inventory stopped at the entry limit",
                            },
                        )
                        break
                    entries_seen += 1
                    tracker.max_entries_seen = max(
                        tracker.max_entries_seen, entries_seen
                    )

                    candidate = root_path / entry.name
                    try:
                        resolved_candidate = candidate.resolve(strict=True)
                    except OSError:
                        continue
                    if not is_within(resolved_candidate, project_path):
                        add_uncertainty(
                            uncertainty_items,
                            {
                                "kind": "excluded-alias",
                                "code": (
                                    "outside-root-directory-alias"
                                    if resolved_candidate.is_dir()
                                    else "outside-root-file-alias"
                                ),
                                "path": safe_relative(candidate, project_path),
                                "message": "A file-system alias resolving outside the repository was excluded",
                            },
                        )
                        continue
                    if resolved_candidate.is_dir():
                        if entry.name in SKIP_DIR_NAMES and not (
                            include_boundary_directories
                            and entry.name.lower() in GENERATED_BOUNDARY_DIR_NAMES
                        ):
                            continue
                        if is_directory_alias(candidate) and not follow_symlinks:
                            continue
                        directory_names.append(entry.name)
                        continue
                    if not resolved_candidate.is_file():
                        continue
                    try:
                        if resolved_candidate.stat().st_nlink > 1:
                            add_uncertainty(
                                uncertainty_items,
                                {
                                    "kind": "excluded-alias",
                                    "code": "hardlink-file-alias",
                                    "path": safe_relative(candidate, project_path),
                                    "message": "A multiply-linked file alias was excluded",
                                },
                            )
                            continue
                    except OSError:
                        continue
                    file_names.append(entry.name)
        except OSError:
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "unreadable-candidate",
                    "code": "directory-unavailable",
                    "path": safe_relative(root_path, project_path),
                    "message": "A repository directory could not be inspected safely",
                },
            )
            continue

        directory_names.sort(key=str.casefold)
        file_names.sort(key=str.casefold)
        yield root_path, directory_names, file_names
        if entry_limit_reached:
            return
        if max_depth is not None and depth >= max_depth and directory_names:
            tracker.truncated = True
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "scan-truncated",
                    "code": "depth-limit",
                    "path": safe_relative(root_path, project_path),
                    "message": "Repository inventory stopped at the depth limit",
                },
            )
        elif max_depth is None or depth < max_depth:
            stack.extend(
                (root_path / name, depth + 1) for name in reversed(directory_names)
            )


# =============================================================================
# Analysis Functions
# =============================================================================


def iter_project_files(
    project_path: Path,
    *,
    suffixes: set[str] | None = None,
    names: set[str] | None = None,
    max_files: int | None = None,
    max_depth: int | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    filename_patterns: set[str] | None = None,
    tracker: ScanTracker | None = None,
    uncertainty_items: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Return a bounded sample of files while pruning generated directories."""
    project_path = resolve_repo_root(project_path)
    tracker = tracker or ScanTracker(ScanLimits())
    uncertainty_items = uncertainty_items if uncertainty_items is not None else []
    matches: list[Path] = []
    for root_path, _, filenames in walk_project_entries(
        project_path,
        max_depth=max_depth,
        follow_symlinks=follow_symlinks,
        include_boundary_directories=False,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    ):
        for filename in filenames:
            path = root_path / filename
            relative_path = path.relative_to(project_path).as_posix()
            if include_patterns and not any(
                fnmatch.fnmatch(relative_path, pattern) for pattern in include_patterns
            ):
                continue
            if exclude_patterns and any(
                fnmatch.fnmatch(relative_path, pattern) for pattern in exclude_patterns
            ):
                continue
            if suffixes is not None and path.suffix not in suffixes:
                continue
            if names is not None and filename not in names:
                continue
            if filename_patterns is not None and not any(
                fnmatch.fnmatch(filename, pattern) for pattern in filename_patterns
            ):
                continue
            if max_files is not None and len(matches) >= max_files:
                tracker.truncated = True
                add_uncertainty(
                    uncertainty_items,
                    {
                        "kind": "scan-truncated",
                        "code": "file-limit",
                        "message": "Repository inventory stopped at the file limit",
                    },
                )
                return matches
            matches.append(path)

    return matches


def find_agents_files(
    project_path: Path,
    *,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> list[str]:
    """Find root and nested AGENTS.md files."""
    paths = iter_project_files(
        project_path,
        names={"AGENTS.md"},
        max_files=100,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    )
    return sorted(path.relative_to(project_path).as_posix() for path in paths)


def find_generated_candidates(
    project_path: Path,
    *,
    max_depth: int | None = None,
    follow_symlinks: bool = False,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> list[str]:
    """Find directory names that often define generated, vendored, or append-only boundaries."""
    candidates: list[str] = []
    for root_path, dirnames, _ in walk_project_entries(
        project_path,
        max_depth=max_depth,
        follow_symlinks=follow_symlinks,
        include_boundary_directories=True,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    ):
        for dirname in dirnames:
            if dirname.lower() not in GENERATED_BOUNDARY_DIR_NAMES:
                continue
            path = root_path / dirname
            candidates.append(path.relative_to(project_path).as_posix() + "/")

    return sorted(set(candidates))


def dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return paths in stable order without duplicates."""
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        normalized = path.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path)
    return sorted(deduped, key=lambda path: path.as_posix())


def find_config_files(
    project_path: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> dict[str, list[Path]]:
    """Find all configuration files in the project."""
    found: dict[str, list[Path]] = {
        "language_configs": [],
        "linter_configs": [],
        "test_configs": [],
        "lockfiles": [],
    }

    recursive_language_names = {
        name
        for name in CONFIG_FILES
        if "*" not in name and name not in {"requirements.txt", "Pipfile"}
    }
    recursive_language_names.update(LOCKFILES)
    found["language_configs"].extend(
        iter_project_files(
            project_path,
            names=recursive_language_names,
            max_files=max_files,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
    )

    wildcard_language_names = {name for name in CONFIG_FILES if "*" in name}
    if wildcard_language_names:
        found["language_configs"].extend(
            iter_project_files(
                project_path,
                filename_patterns=wildcard_language_names,
                max_files=max_files,
                tracker=tracker,
                uncertainty_items=uncertainty_items,
            )
        )

    found["linter_configs"].extend(
        iter_project_files(
            project_path,
            names=set(LINTER_FILES),
            max_files=max_files,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
    )
    found["test_configs"].extend(
        iter_project_files(
            project_path,
            names=set(TEST_PATTERNS),
            max_files=max_files,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
    )

    found["lockfiles"].extend(
        iter_project_files(
            project_path,
            names=set(LOCKFILES),
            max_files=max_files,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
    )

    for key, paths in found.items():
        found[key] = dedupe_paths(paths)

    return found


def detect_languages_and_frameworks(
    config_files: list[Path],
    project_path: Path,
    *,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    """Detect languages and frameworks from config files."""
    languages: set[str] = set()
    frameworks: set[str] = set()
    tools: set[str] = set()

    for config_path in config_files:
        filename = config_path.name

        for pattern, info in CONFIG_FILES.items():
            if "*" in pattern:
                if fnmatch.fnmatch(filename, pattern):
                    if "language" in info:
                        languages.add(info["language"])
            elif filename == pattern:
                if "language" in info:
                    languages.add(info["language"])
                if "tools" in info:
                    tools.update(info["tools"])
                if "platform" in info:
                    frameworks.add(info["platform"])

        # Deep inspection for specific files
        if filename == "pyproject.toml":
            content = read_analysis_text(
                project_path,
                config_path,
                tracker=tracker,
                uncertainty_items=uncertainty_items,
            )
            if content is not None:
                if "fastapi" in content.lower():
                    frameworks.add("FastAPI")
                if "django" in content.lower():
                    frameworks.add("Django")
                if "flask" in content.lower():
                    frameworks.add("Flask")
                if "pyspark" in content.lower() or "databricks" in content.lower():
                    frameworks.add("PySpark")
                if "torch" in content.lower() or "pytorch" in content.lower():
                    frameworks.add("PyTorch")
                if "transformers" in content.lower():
                    frameworks.add("HuggingFace Transformers")
                if "mlflow" in content.lower():
                    tools.add("MLflow")
                if "[tool.ruff]" in content:
                    tools.add("Ruff")
                if "[tool.black]" in content:
                    tools.add("Black")
                if "[tool.pytest" in content:
                    tools.add("pytest")

        if filename == "package.json":
            content = read_analysis_text(
                project_path,
                config_path,
                tracker=tracker,
                uncertainty_items=uncertainty_items,
            )
            if content is None:
                continue
            try:
                data = json.loads(content)
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                }
                if "react" in deps:
                    frameworks.add("React")
                if "next" in deps:
                    frameworks.add("Next.js")
                if "vue" in deps:
                    frameworks.add("Vue")
                if "express" in deps:
                    frameworks.add("Express")
                if "fastify" in deps:
                    frameworks.add("Fastify")
                if "vitest" in deps:
                    tools.add("Vitest")
                if "jest" in deps:
                    tools.add("Jest")
                if "typescript" in deps:
                    languages.add("TypeScript")
            except (json.JSONDecodeError, TypeError):
                add_uncertainty(
                    uncertainty_items,
                    {
                        "kind": "unreadable-candidate",
                        "code": "invalid-json",
                        "path": config_path.relative_to(project_path).as_posix(),
                        "message": "A package.json candidate was not valid JSON",
                    },
                )

    return languages, frameworks, tools


def detect_linters_and_formatters(
    linter_configs: list[Path],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Detect linters and formatters from config files."""
    linters: list[dict[str, str]] = []
    formatters: list[dict[str, str]] = []

    formatters_set = {"Prettier", "Black", "rustfmt"}

    for config_path in linter_configs:
        filename = config_path.name
        if filename in LINTER_FILES:
            info = LINTER_FILES[filename]
            entry = {"tool": info["tool"], "config": str(config_path.name)}

            if info["tool"] in formatters_set:
                formatters.append(entry)
            else:
                linters.append(entry)

    return linters, formatters


def detect_test_frameworks(test_configs: list[Path]) -> list[dict[str, str]]:
    """Detect test frameworks from config files."""
    frameworks: list[dict[str, str]] = []

    for config_path in test_configs:
        filename = config_path.name
        for pattern, info in TEST_PATTERNS.items():
            if filename == pattern or filename.endswith(pattern):
                frameworks.append(
                    {"framework": info["framework"], "config": str(config_path.name)}
                )
                break

    return frameworks


def detect_package_managers(
    lockfiles: list[Path], project_path: Path
) -> list[dict[str, str]]:
    """Detect package managers from lockfiles."""
    managers: list[dict[str, str]] = []
    for path in lockfiles:
        manager = LOCKFILES.get(path.name)
        if not manager:
            continue
        managers.append(
            {
                "manager": manager,
                "evidence": path.relative_to(project_path).as_posix(),
            }
        )
    return sorted(
        managers, key=lambda item: (item["manager"].lower(), item["evidence"])
    )


def parse_package_scripts(
    package_json_files: list[Path],
    project_path: Path,
    *,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Collect package.json scripts without trying to validate commands."""
    commands: list[dict[str, str]] = []

    for path in package_json_files:
        content = read_analysis_text(
            project_path,
            path,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
        if content is None:
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            continue

        for script_name, command in sorted(scripts.items()):
            if not isinstance(script_name, str) or not isinstance(command, str):
                continue
            commands.append(
                {
                    "path": path.relative_to(project_path).as_posix(),
                    "script": script_name,
                    "command": command,
                }
            )

    return sorted(commands, key=lambda item: (item["path"], item["script"]))


def parse_pyproject_details(
    pyproject_files: list[Path],
    project_path: Path,
    *,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], set[str]]:
    """Parse enough pyproject.toml metadata to identify pytest and Python version hints."""
    test_frameworks: list[dict[str, str]] = []
    python_version_hints: list[dict[str, str]] = []
    tools: set[str] = set()

    for path in pyproject_files:
        content = read_analysis_text(
            project_path,
            path,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
        if content is None:
            continue
        try:
            data = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            add_uncertainty(
                uncertainty_items,
                {
                    "kind": "unreadable-candidate",
                    "code": "invalid-toml",
                    "path": path.relative_to(project_path).as_posix(),
                    "message": "A pyproject candidate was not valid TOML",
                },
            )
            continue

        relative_path = path.relative_to(project_path).as_posix()
        project = data.get("project")
        if isinstance(project, dict):
            requires_python = project.get("requires-python")
            if isinstance(requires_python, str):
                python_version_hints.append(
                    {
                        "path": relative_path,
                        "source": "project.requires-python",
                        "value": requires_python,
                    }
                )

        tool = data.get("tool")
        if isinstance(tool, dict):
            pytest_config = tool.get("pytest")
            if isinstance(pytest_config, dict) and "ini_options" in pytest_config:
                tools.add("pytest")
                test_frameworks.append({"framework": "pytest", "config": relative_path})

            poetry = tool.get("poetry")
            if isinstance(poetry, dict):
                dependencies = poetry.get("dependencies")
                if isinstance(dependencies, dict) and isinstance(
                    dependencies.get("python"), str
                ):
                    python_version_hints.append(
                        {
                            "path": relative_path,
                            "source": "tool.poetry.dependencies.python",
                            "value": dependencies["python"],
                        }
                    )

    return (
        sorted(test_frameworks, key=lambda item: item["config"]),
        sorted(python_version_hints, key=lambda item: (item["path"], item["source"])),
        tools,
    )


def analyze_naming_conventions(
    project_path: Path,
    *,
    max_depth: int | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> dict[str, str]:
    """Analyze naming conventions from source files."""
    conventions: dict[str, str] = {}

    # Find Python files
    python_files = iter_project_files(
        project_path,
        suffixes={".py"},
        max_files=min(MAX_NAMING_FILES, max_files),
        max_depth=max_depth,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    )
    if python_files:
        function_names: list[str] = []
        class_names: list[str] = []
        variable_names: list[str] = []

        func_pattern = re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE)
        class_pattern = re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)
        var_pattern = re.compile(r"^\s*(\w+)\s*=", re.MULTILINE)

        for py_file in python_files:
            content = read_analysis_text(
                project_path,
                py_file,
                tracker=tracker,
                uncertainty_items=uncertainty_items,
            )
            if content is None:
                continue
            function_names.extend(func_pattern.findall(content))
            class_names.extend(class_pattern.findall(content))
            var_matches = var_pattern.findall(content)
            variable_names.extend(
                v for v in var_matches if not v.startswith("_") and v.isupper()
            )

        # Analyze function naming
        if function_names:
            snake_case = sum(1 for n in function_names if "_" in n and n.islower())
            camel_case = sum(
                1
                for n in function_names
                if n[0].islower() and any(c.isupper() for c in n)
            )
            if snake_case > camel_case:
                conventions["python_functions"] = "snake_case"
            elif camel_case > snake_case:
                conventions["python_functions"] = "camelCase"

        # Analyze class naming
        if class_names:
            pascal_case = sum(1 for n in class_names if n[0].isupper())
            if pascal_case == len(class_names):
                conventions["python_classes"] = "PascalCase"

    # Find TypeScript/JavaScript files
    ts_files = iter_project_files(
        project_path,
        suffixes={".ts", ".tsx"},
        max_files=min(MAX_NAMING_FILES, max_files),
        max_depth=max_depth,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    )
    if ts_files:
        func_names: list[str] = []
        ts_func_pattern = re.compile(r"(?:function|const|let)\s+(\w+)\s*[=\(]")

        for ts_file in ts_files:
            content = read_analysis_text(
                project_path,
                ts_file,
                tracker=tracker,
                uncertainty_items=uncertainty_items,
            )
            if content is not None:
                func_names.extend(ts_func_pattern.findall(content))

        if func_names:
            camel = sum(
                1 for n in func_names if n[0].islower() and any(c.isupper() for c in n)
            )
            if camel > len(func_names) // 2:
                conventions["typescript_functions"] = "camelCase"

    return conventions


def sample_source_files(
    project_path: Path,
    *,
    max_depth: int | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    tracker: ScanTracker,
    uncertainty_items: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """Sample source files and detect patterns."""
    patterns: list[str] = []
    source_files = iter_project_files(
        project_path,
        suffixes=SOURCE_SUFFIXES,
        max_files=max_files,
        max_depth=max_depth,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=uncertainty_items,
    )
    count = len(source_files)

    # Check for common patterns in Python
    py_files = [path for path in source_files if path.suffix == ".py"][
        : min(MAX_PATTERN_FILES, max_files)
    ]

    type_hints = 0
    docstrings = 0
    logging_usage = 0
    print_usage = 0
    pathlib_usage = 0

    for py_file in py_files:
        content = read_analysis_text(
            project_path,
            py_file,
            tracker=tracker,
            uncertainty_items=uncertainty_items,
        )
        if content is None:
            continue
        if re.search(r"def\s+\w+\([^)]*:\s*\w+", content):
            type_hints += 1
        if '"""' in content or "'''" in content:
            docstrings += 1
        if "import logging" in content or "logger" in content.lower():
            logging_usage += 1
        if "print(" in content:
            print_usage += 1
        if "from pathlib" in content:
            pathlib_usage += 1

    if py_files:
        if type_hints > len(py_files) // 2:
            patterns.append("Type hints used consistently")
        if docstrings > len(py_files) // 2:
            patterns.append("Docstrings present in most files")
        if logging_usage > print_usage:
            patterns.append("Logging preferred over print statements")
        elif print_usage > logging_usage:
            patterns.append("Print statements used (consider logging)")

    if pathlib_usage > len(py_files) // 3:
        patterns.append("pathlib.Path used for file operations")

    return count, patterns


def generate_suggestions(analysis: ProjectAnalysis) -> list[str]:
    """Compatibility field for schema 1.0 callers.

    Schema 1.1 reports missing or ambiguous evidence as uncertainty_items instead
    of content recommendations.
    """
    return []


def generate_uncertainty_items(analysis: ProjectAnalysis) -> list[dict[str, Any]]:
    """Return facts the analyzer could not prove from sampled files."""
    items: list[dict[str, Any]] = []

    if not analysis.languages:
        items.append(
            {
                "kind": "missing-evidence",
                "message": "Primary language was not detected from sampled configs",
            }
        )
    if not analysis.frameworks:
        items.append(
            {
                "kind": "missing-evidence",
                "message": "Primary framework or execution model was not detected",
            }
        )
    if not analysis.package_managers:
        items.append(
            {
                "kind": "missing-evidence",
                "message": "Package manager lockfile was not detected",
            }
        )
    if not analysis.command_inventory:
        items.append(
            {"kind": "missing-evidence", "message": "Package script inventory is empty"}
        )
    if not analysis.linters:
        items.append(
            {"kind": "missing-evidence", "message": "Linter config was not detected"}
        )
    if not analysis.formatters:
        items.append(
            {"kind": "missing-evidence", "message": "Formatter config was not detected"}
        )
    if not analysis.test_frameworks:
        items.append(
            {
                "kind": "missing-evidence",
                "message": "Test framework config was not detected",
            }
        )

    package_managers = sorted({item["manager"] for item in analysis.package_managers})
    if len(package_managers) > 1:
        items.append(
            {
                "kind": "ambiguous-evidence",
                "message": "Multiple package-manager lockfile types were detected",
                "evidence": package_managers,
            }
        )

    return items


def build_detected_facts(analysis: ProjectAnalysis) -> list[dict[str, Any]]:
    """Return coarse provenance for analyzer facts so callers do not treat them as authority."""
    facts: list[dict[str, Any]] = []

    for language in analysis.languages:
        facts.append(
            {
                "kind": "language",
                "name": language,
                "evidence": analysis.config_files,
                "confidence": "high" if analysis.config_files else "low",
            }
        )
    for framework in analysis.frameworks:
        facts.append(
            {
                "kind": "framework",
                "name": framework,
                "evidence": analysis.config_files,
                "confidence": "medium",
            }
        )
    for tool in analysis.tools:
        facts.append(
            {
                "kind": "tool",
                "name": tool,
                "evidence": analysis.config_files,
                "confidence": "medium",
            }
        )
    for package_manager in analysis.package_managers:
        facts.append(
            {
                "kind": "package_manager",
                "name": package_manager["manager"],
                "evidence": [package_manager["evidence"]],
                "confidence": "high",
            }
        )

    return facts


# =============================================================================
# Output Formatting
# =============================================================================


def format_markdown(analysis: ProjectAnalysis) -> str:
    """Format analysis as Markdown."""
    lines: list[str] = []
    lines.append("# Project Analysis Report")
    lines.append("")
    lines.append(f"**Project**: {markdown_code_span(analysis.project_path)}")
    lines.append(f"**Schema**: {markdown_code_span(analysis.schema_version)}")
    lines.append("")

    if analysis.agents_files:
        lines.append("## AGENTS.md Files Found")
        lines.append("")
        for path in analysis.agents_files:
            lines.append(f"- {markdown_code_span(path)}")
        lines.append("")

    if analysis.codex_project_instruction_audit:
        audit = analysis.codex_project_instruction_audit
        lines.append("## Codex Project Instruction Audit")
        lines.append("")
        lines.append(f"- Candidate files: {audit['candidate_count']}")
        lines.append(
            f"- Candidate metadata bytes: {audit['candidate_metadata_total_bytes']}"
        )
        lines.append(f"- Advisory byte limit: {audit['byte_limit']}")
        lines.append(
            f"- Runtime attestation: {markdown_code_span(audit['runtime_attestation'])}"
        )
        selected_chain_bounds = audit["selected_chain_bounds"]
        precedence = " -> ".join(
            markdown_code_span(item)
            for item in selected_chain_bounds["documented_precedence"]
        )
        lines.append(f"- Documented per-directory precedence: {precedence}")
        lines.append(
            "- Selected-chain bounds: "
            f"`{selected_chain_bounds['lower_bound_bytes']}` to "
            f"`{selected_chain_bounds['upper_bound_bytes']}` bytes "
            f"({selected_chain_bounds['status']})"
        )
        if selected_chain_bounds.get("could_exceed_byte_limit", False):
            lines.append(
                "- Advisory: documented selected-chain upper bound exceeds the byte limit; runtime loading is unverified."
            )
        for instruction_file in audit["instruction_files"]:
            lines.append(
                f"- {markdown_code_span(instruction_file['path'])} ({markdown_code_span(instruction_file['class'])}, "
                f"{instruction_file['file_bytes']} bytes)"
            )
        for diagnostic in audit["diagnostics"]:
            lines.append(
                f"- Advisory {markdown_code_span(diagnostic['code'])} for "
                f"{markdown_code_span(diagnostic['path'])}"
            )
        lines.append("")

    # Languages
    lines.append("## Detected Stack")
    lines.append("")
    if analysis.languages:
        lines.append(f"**Languages**: {', '.join(sorted(analysis.languages))}")
    if analysis.frameworks:
        lines.append(f"**Frameworks**: {', '.join(sorted(analysis.frameworks))}")
    if analysis.tools:
        lines.append(f"**Tools**: {', '.join(sorted(analysis.tools))}")
    lines.append("")

    if analysis.package_managers:
        lines.append("## Package Managers")
        lines.append("")
        for package_manager in analysis.package_managers:
            lines.append(
                f"- {markdown_code_span(package_manager['manager'])} "
                f"({markdown_code_span(package_manager['evidence'])})"
            )
        lines.append("")

    # Config files
    if analysis.config_files:
        lines.append("## Configuration Files Found")
        lines.append("")
        for cf in analysis.config_files:
            lines.append(f"- {markdown_code_span(cf)}")
        lines.append("")

    if analysis.generated_candidates:
        lines.append("## Generated Or Boundary Candidates")
        lines.append("")
        for path in analysis.generated_candidates:
            lines.append(f"- {markdown_code_span(path)}")
        lines.append("")

    if analysis.command_inventory:
        lines.append("## Command Inventory")
        lines.append("")
        for command in analysis.command_inventory:
            lines.append(
                f"- {markdown_code_span(command['path'])} script {markdown_code_span(command['script'])}: "
                f"{markdown_code_span(command['command'])}"
            )
        lines.append("")

    if analysis.python_version_hints:
        lines.append("## Python Version Hints")
        lines.append("")
        for hint in analysis.python_version_hints:
            lines.append(
                f"- {markdown_code_span(hint['path'])} {markdown_code_span(hint['source'])}: "
                f"{markdown_code_span(hint['value'])}"
            )
        lines.append("")

    # Linters and formatters
    if analysis.linters or analysis.formatters:
        lines.append("## Code Quality Tools")
        lines.append("")
        if analysis.linters:
            lines.append("**Linters**:")
            for linter in analysis.linters:
                lines.append(
                    f"- {markdown_code_span(linter['tool'])} ({markdown_code_span(linter['config'])})"
                )
        if analysis.formatters:
            lines.append("**Formatters**:")
            for fmt in analysis.formatters:
                lines.append(
                    f"- {markdown_code_span(fmt['tool'])} ({markdown_code_span(fmt['config'])})"
                )
        lines.append("")

    # Testing
    if analysis.test_frameworks:
        lines.append("## Testing")
        lines.append("")
        for tf in analysis.test_frameworks:
            lines.append(
                f"- {markdown_code_span(tf['framework'])} ({markdown_code_span(tf['config'])})"
            )
        lines.append("")

    # Naming conventions
    if analysis.naming_conventions:
        lines.append("## Naming Conventions Detected")
        lines.append("")
        for element, convention in analysis.naming_conventions.items():
            lines.append(
                f"- **{markdown_code_span(element)}**: {markdown_code_span(convention)}"
            )
        lines.append("")

    # Patterns
    if analysis.detected_patterns:
        lines.append("## Code Patterns Detected")
        lines.append("")
        for pattern in analysis.detected_patterns:
            lines.append(f"- {markdown_code_span(pattern)}")
        lines.append("")

    if analysis.detected_facts:
        lines.append("## Detected Facts")
        lines.append("")
        for fact in analysis.detected_facts:
            evidence = ", ".join(
                markdown_code_span(value) for value in fact.get("evidence", [])
            )
            lines.append(
                f"- **{markdown_code_span(fact.get('kind', 'fact'))}** {markdown_code_span(fact.get('name', 'unknown'))} "
                f"({markdown_code_span(fact.get('confidence', 'unknown'))} confidence): {evidence}"
            )
        lines.append("")

    if analysis.scan_summary:
        summary = analysis.scan_summary
        limits = summary["limits"]
        observed = summary["observed"]
        lines.append("## Scan Bounds")
        lines.append("")
        lines.append(
            f"- Truncated: {markdown_code_span(str(summary['truncated']).lower())}"
        )
        lines.append(
            "- Inventory bounds: "
            f"{limits['max_directories_per_pass']} directories and "
            f"{limits['max_entries_per_pass']} entries per pass"
        )
        lines.append(
            "- Content bounds: "
            f"{limits['max_file_bytes']} bytes per file and "
            f"{limits['max_total_bytes']} bytes total"
        )
        lines.append(
            "- Observed: "
            f"{observed['max_directories_in_one_pass']} directories, "
            f"{observed['max_entries_in_one_pass']} entries, "
            f"{observed['files_read']} files, and {observed['bytes_read']} bytes"
        )
        lines.append("")

    # Uncertainty
    if analysis.uncertainty_items:
        lines.append("## Uncertainty Items")
        lines.append("")
        for item in analysis.uncertainty_items:
            suffix = ""
            if item.get("evidence"):
                suffix = " Evidence: " + ", ".join(
                    markdown_code_span(value) for value in item["evidence"]
                )
            lines.append(
                f"- **{markdown_code_span(item['kind'])}**: {markdown_code_span(item['message'])}{suffix}"
            )
        lines.append("")

    return "\n".join(lines)


def format_json(analysis: ProjectAnalysis) -> str:
    """Format analysis as JSON."""
    return json.dumps(asdict(analysis), indent=2)


# =============================================================================
# Main
# =============================================================================


def analyze_project(
    project_path: Path,
    *,
    cwd: Path | None = None,
    project_doc_fallback_filenames: list[str] | None = None,
    project_doc_byte_limit: int = DEFAULT_PROJECT_DOC_BYTE_LIMIT,
    max_depth: int | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    max_directories: int = DEFAULT_MAX_DIRECTORIES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> ProjectAnalysis:
    """Analyze a project directory."""
    project_path = resolve_repo_root(project_path)
    if max_depth is not None and max_depth < 0:
        raise ValueError("Analyzer depth limit must be non-negative")
    limits = ScanLimits(
        max_directories=max_directories,
        max_entries=max_entries,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    if (
        min(
            limits.max_directories,
            limits.max_entries,
            limits.max_file_bytes,
            limits.max_total_bytes,
        )
        < 1
    ):
        raise ValueError("Analyzer scan limits must all be >= 1")
    tracker = ScanTracker(limits)
    analysis = ProjectAnalysis(project_path=str(project_path))
    resolved_cwd: Path | None = None
    if cwd is not None:
        try:
            resolved_cwd = cwd.resolve(strict=True)
        except OSError as exc:
            raise InputFileError(
                "cwd-unavailable",
                "cwd",
                "Working directory must be an accessible directory inside the repository",
            ) from exc
        if not resolved_cwd.is_dir() or not is_within(resolved_cwd, project_path):
            raise InputFileError(
                "cwd-escape",
                "cwd",
                "Working directory must resolve inside the repository",
            )
    analysis.agents_files = find_agents_files(
        project_path,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )
    analysis.codex_project_instruction_audit = audit_codex_project_instruction_sources(
        project_path,
        cwd=resolved_cwd,
        fallback_filenames=project_doc_fallback_filenames or [],
        byte_limit=project_doc_byte_limit,
    )
    analysis.generated_candidates = find_generated_candidates(
        project_path,
        max_depth=max_depth,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )

    # Find config files
    config_files = find_config_files(
        project_path,
        max_files=max_files,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )

    # Store found config files
    all_configs = (
        config_files["language_configs"]
        + config_files["linter_configs"]
        + config_files["test_configs"]
        + config_files["lockfiles"]
    )
    analysis.config_files = [
        p.relative_to(project_path).as_posix() for p in dedupe_paths(all_configs)
    ]

    # Detect languages and frameworks
    languages, frameworks, tools = detect_languages_and_frameworks(
        config_files["language_configs"],
        project_path,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )
    analysis.package_managers = detect_package_managers(
        config_files["lockfiles"], project_path
    )
    tools.update(
        package_manager["manager"] for package_manager in analysis.package_managers
    )

    pyproject_files = [
        path
        for path in config_files["language_configs"]
        if path.name == "pyproject.toml"
    ]
    pyproject_tests, python_version_hints, pyproject_tools = parse_pyproject_details(
        pyproject_files,
        project_path,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )
    tools.update(pyproject_tools)

    analysis.languages = sorted(languages)
    analysis.frameworks = sorted(frameworks)
    analysis.tools = sorted(tools)
    analysis.python_version_hints = python_version_hints

    package_json_files = [
        path for path in config_files["language_configs"] if path.name == "package.json"
    ]
    analysis.command_inventory = parse_package_scripts(
        package_json_files,
        project_path,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )

    # Detect linters and formatters
    linters, formatters = detect_linters_and_formatters(config_files["linter_configs"])
    analysis.linters = linters
    analysis.formatters = formatters

    # Detect test frameworks
    test_frameworks = (
        detect_test_frameworks(config_files["test_configs"]) + pyproject_tests
    )
    analysis.test_frameworks = sorted(
        {json.dumps(item, sort_keys=True): item for item in test_frameworks}.values(),
        key=lambda item: (item["framework"], item["config"]),
    )

    # Analyze naming conventions
    analysis.naming_conventions = analyze_naming_conventions(
        project_path,
        max_depth=max_depth,
        max_files=max_files,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )

    # Sample source files
    count, patterns = sample_source_files(
        project_path,
        max_depth=max_depth,
        max_files=max_files,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        follow_symlinks=follow_symlinks,
        tracker=tracker,
        uncertainty_items=analysis.uncertainty_items,
    )
    analysis.source_files_sampled = count
    analysis.detected_patterns = patterns
    analysis.detected_facts = build_detected_facts(analysis)

    # Keep suggestions for schema 1.0 compatibility and report evidence gaps separately.
    analysis.suggestions = generate_suggestions(analysis)
    for item in generate_uncertainty_items(analysis):
        add_uncertainty(analysis.uncertainty_items, item)
    analysis.scan_summary = {
        "limits": {
            "max_directories_per_pass": limits.max_directories,
            "max_entries_per_pass": limits.max_entries,
            "max_file_bytes": limits.max_file_bytes,
            "max_total_bytes": limits.max_total_bytes,
        },
        "observed": {
            "max_directories_in_one_pass": tracker.max_directories_seen,
            "max_entries_in_one_pass": tracker.max_entries_seen,
            "files_read": tracker.files_read,
            "bytes_read": tracker.bytes_read,
        },
        "truncated": tracker.truncated,
        "certainty": "limited"
        if tracker.truncated or analysis.uncertainty_items
        else "complete",
    }
    analysis.analysis_certainty = analysis.scan_summary["certainty"]

    return analysis


def main() -> int:
    """Main entry point."""
    parser = ValueFreeArgumentParser(
        description="Analyze a project to bootstrap AGENTS.md creation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python analyze_project.py .
    python analyze_project.py /path/to/project --output json
    python analyze_project.py ~/code/my-app --output markdown
        """,
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        type=Path,
        help="Path to the project directory to analyze",
    )
    parser.add_argument(
        "--repo-root", type=Path, help="Path to the project directory to analyze"
    )
    parser.add_argument(
        "--output",
        "--format",
        "-o",
        dest="output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        help="Maximum directory depth to recursively sample for source and test files",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="Maximum number of source files to sample per detector",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Glob pattern to include in source sampling",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob pattern to exclude from source sampling",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinked directories during sampling",
    )
    parser.add_argument(
        "--max-directories",
        type=int,
        default=DEFAULT_MAX_DIRECTORIES,
        help=f"Maximum directories per inventory pass (default: {DEFAULT_MAX_DIRECTORIES})",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        help=f"Maximum directory entries per inventory pass (default: {DEFAULT_MAX_ENTRIES})",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help=f"Maximum bytes read from one candidate (default: {DEFAULT_MAX_FILE_BYTES})",
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
        help=f"Maximum cumulative content bytes read (default: {DEFAULT_MAX_TOTAL_BYTES})",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        help="Working directory inside the repo for Codex instruction-source metadata",
    )
    parser.add_argument(
        "--project-doc-fallback-filename",
        action="append",
        default=[],
        help="Additional project instruction basename to audit. Repeat for multiple names.",
    )
    parser.add_argument(
        "--project-doc-byte-limit",
        type=int,
        default=DEFAULT_PROJECT_DOC_BYTE_LIMIT,
        help=f"Advisory cumulative instruction-byte limit (default: {DEFAULT_PROJECT_DOC_BYTE_LIMIT})",
    )

    raw_arguments = sys.argv[1:]
    json_requested = any(
        argument in {"--format=json", "--output=json"}
        or argument in {"--format", "--output", "-o"}
        and index + 1 < len(raw_arguments)
        and raw_arguments[index + 1] == "json"
        for index, argument in enumerate(raw_arguments)
    )
    try:
        args = parser.parse_args()
    except ArgumentParseError:
        error = command_line_error()
        if json_requested:
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2

    if args.project_path is not None and args.repo_root is not None:
        error = command_line_error()
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
    project_arg = args.repo_root if args.repo_root is not None else args.project_path
    if project_arg is None:
        error = command_line_error()
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
    if args.max_depth is not None and args.max_depth < 0:
        error = command_line_error()
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
    if args.max_files < 1:
        error = command_line_error()
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
    if (
        min(
            args.max_directories,
            args.max_entries,
            args.max_file_bytes,
            args.max_total_bytes,
        )
        < 1
    ):
        error = command_line_error()
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2

    try:
        analysis = analyze_project(
            project_arg,
            cwd=args.cwd,
            project_doc_fallback_filenames=args.project_doc_fallback_filename,
            project_doc_byte_limit=args.project_doc_byte_limit,
            max_depth=args.max_depth,
            max_files=args.max_files,
            include_patterns=args.include,
            exclude_patterns=args.exclude,
            follow_symlinks=args.follow_symlinks,
            max_directories=args.max_directories,
            max_entries=args.max_entries,
            max_file_bytes=args.max_file_bytes,
            max_total_bytes=args.max_total_bytes,
        )
    except (InputFileError, ValueError) as exc:
        error = (
            exc
            if isinstance(exc, InputFileError)
            else InputFileError(
                "invalid-configuration", "configuration", "Invalid configuration"
            )
        )
        if args.output == "json":
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2

    if args.output == "json":
        print(format_json(analysis))
    else:
        print(format_markdown(analysis))

    return 0


if __name__ == "__main__":
    sys.exit(main())
