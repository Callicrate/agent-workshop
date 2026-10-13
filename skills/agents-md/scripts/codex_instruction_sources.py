#!/usr/bin/env python3
"""Audit candidate project instruction files without reading their content.

The audit maps instruction-file candidates between a repository root and a
working directory.  It is deliberately metadata-only: it does not infer
Codex's runtime selection behavior or inspect ambient Codex configuration.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from bounded_io import (
    ArgumentParseError,
    InputFileError,
    ValueFreeArgumentParser,
    command_line_error,
    error_envelope,
)


DEFAULT_PROJECT_DOC_BYTE_LIMIT = 32_768
PRIMARY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("AGENTS.override.md", "override"),
    ("AGENTS.md", "agents"),
)


def same_native_file(left: Path, right: Path) -> bool:
    """Use native filesystem identity to detect aliases without Unicode casefolding."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path remains inside the resolved root."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def normalize_project_doc_fallback_filenames(
    filenames: Iterable[str],
) -> tuple[str, ...]:
    """Validate and stably deduplicate explicit instruction-file basenames."""
    normalized: list[str] = []
    seen: set[str] = set()

    for filename in filenames:
        if not isinstance(filename, str):
            raise ValueError("Project-document fallback filenames must be strings")
        if not filename or filename in {".", ".."}:
            raise ValueError(
                f"Invalid project-document fallback filename: {filename!r}"
            )
        if any(not character.isprintable() for character in filename):
            raise ValueError(
                f"Project-document fallback cannot contain control characters: {filename!r}"
            )
        if ":" in filename:
            raise ValueError(
                f"Project-document fallback cannot contain a colon or alternate data stream: {filename!r}"
            )
        if "/" in filename or "\\" in filename:
            raise ValueError(
                f"Project-document fallback must be a basename, not a path: {filename!r}"
            )
        if any(character in filename for character in '?*<>"|'):
            raise ValueError(
                f"Project-document fallback cannot contain Windows-reserved characters: {filename!r}"
            )
        if filename.endswith((".", " ")):
            raise ValueError(
                f"Project-document fallback cannot end with a dot or space: {filename!r}"
            )

        windows_path = PureWindowsPath(filename)
        posix_path = PurePosixPath(filename)
        if (
            windows_path.is_absolute()
            or posix_path.is_absolute()
            or windows_path.drive
            or windows_path.root
            or posix_path.root
            or windows_path.name != filename
            or posix_path.name != filename
        ):
            raise ValueError(
                f"Project-document fallback must be a basename: {filename!r}"
            )

        device_basename = filename.split(".", 1)[0].rstrip(". ").casefold()
        reserved_device_names = {"con", "prn", "aux", "nul", "clock$"}
        reserved_device_names.update(f"com{index}" for index in range(1, 10))
        reserved_device_names.update(f"lpt{index}" for index in range(1, 10))
        if device_basename in reserved_device_names:
            raise ValueError(
                f"Project-document fallback cannot use a Windows device name: {filename!r}"
            )

        if filename in seen:
            continue
        seen.add(filename)
        normalized.append(filename)

    return tuple(normalized)


def instruction_candidate_names(
    fallback_filenames: Iterable[str] = (),
) -> tuple[tuple[str, str], ...]:
    """Return ordered, de-duplicated candidate names and their metadata classes."""
    candidates: list[tuple[str, str]] = list(PRIMARY_CANDIDATES)
    candidates.extend(
        (filename, "fallback")
        for filename in normalize_project_doc_fallback_filenames(fallback_filenames)
    )

    deduplicated: list[tuple[str, str]] = []
    seen: set[str] = set()
    for filename, candidate_class in candidates:
        if filename in seen:
            continue
        seen.add(filename)
        deduplicated.append((filename, candidate_class))
    return tuple(deduplicated)


def directories_from_root_to_cwd(repo_root: Path, cwd: Path) -> list[Path]:
    """Return the inclusive resolved directory chain from root to working directory."""
    relative_cwd = cwd.relative_to(repo_root)
    directories = [repo_root]
    current = repo_root
    for part in relative_cwd.parts:
        current = current / part
        directories.append(current)
    return directories


def markdown_code_span(value: object) -> str:
    """Render untrusted metadata as a single safe Markdown inline-code span."""
    escaped: list[str] = []
    for character in str(value):
        if not character.isprintable():
            escaped.append(f"\\u{ord(character):04x}")
        else:
            escaped.append(character)
    escaped_value = "".join(escaped)
    longest_backtick_run = 0
    current_backtick_run = 0
    for character in escaped_value:
        if character == "`":
            current_backtick_run += 1
            longest_backtick_run = max(longest_backtick_run, current_backtick_run)
        else:
            current_backtick_run = 0
    delimiter = "`" * (longest_backtick_run + 1)
    return f"{delimiter}{escaped_value}{delimiter}"


def audit_codex_project_instruction_sources(
    repo_root: Path,
    *,
    cwd: Path | None = None,
    fallback_filenames: Iterable[str] = (),
    byte_limit: int = DEFAULT_PROJECT_DOC_BYTE_LIMIT,
) -> dict[str, object]:
    """Return metadata for candidate Codex project instruction files.

    This function never reads a candidate document.  File size, resolved
    containment, and candidate ordering are the only information collected.
    """
    if byte_limit < 1:
        raise ValueError("Project-document byte limit must be >= 1")

    resolved_root = repo_root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ValueError(f"Repository root is not a directory: {resolved_root}")

    requested_cwd = cwd if cwd is not None else resolved_root
    resolved_cwd = requested_cwd.resolve()
    if not resolved_cwd.exists() or not resolved_cwd.is_dir():
        raise ValueError(f"Working directory is not a directory: {resolved_cwd}")
    if not is_within(resolved_cwd, resolved_root):
        raise ValueError(
            f"Working directory must be inside repository root: {resolved_cwd}"
        )

    normalized_fallbacks = normalize_project_doc_fallback_filenames(fallback_filenames)
    candidates = instruction_candidate_names(normalized_fallbacks)
    instruction_files: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    candidate_metadata_total_bytes = 0
    documented_selected_chain_upper_bound_bytes = 0

    for depth, directory in enumerate(
        directories_from_root_to_cwd(resolved_root, resolved_cwd)
    ):
        seen_native_targets: list[Path] = []
        directory_candidates: list[dict[str, object]] = []
        for filename, candidate_class in candidates:
            candidate = directory / filename
            try:
                if not candidate.exists():
                    continue
                resolved_candidate = candidate.resolve()
            except OSError as exc:
                diagnostics.append(
                    {
                        "code": "candidate-resolution-failed",
                        "path": candidate.relative_to(resolved_root).as_posix(),
                        "filename": filename,
                        "class": candidate_class,
                        "depth": depth,
                        "message": f"Candidate metadata could not be resolved: {exc.__class__.__name__}",
                    }
                )
                continue

            if not is_within(resolved_candidate, resolved_root):
                diagnostics.append(
                    {
                        "code": "outside-root-symlink",
                        "path": candidate.relative_to(resolved_root).as_posix(),
                        "filename": filename,
                        "class": candidate_class,
                        "depth": depth,
                        "message": "Candidate resolves outside the repository root and was excluded",
                    }
                )
                continue
            if any(
                same_native_file(resolved_candidate, seen_target)
                for seen_target in seen_native_targets
            ):
                continue
            if not resolved_candidate.is_file():
                continue

            try:
                file_bytes = resolved_candidate.stat().st_size
            except OSError as exc:
                diagnostics.append(
                    {
                        "code": "candidate-stat-failed",
                        "path": candidate.relative_to(resolved_root).as_posix(),
                        "filename": filename,
                        "class": candidate_class,
                        "depth": depth,
                        "message": f"Candidate metadata could not be read: {exc.__class__.__name__}",
                    }
                )
                continue

            seen_native_targets.append(resolved_candidate)
            candidate_metadata_total_bytes += file_bytes
            metadata = {
                "path": candidate.relative_to(resolved_root).as_posix(),
                "filename": filename,
                "class": candidate_class,
                "depth": depth,
                "file_bytes": file_bytes,
                "cumulative_candidate_metadata_bytes": candidate_metadata_total_bytes,
                "byte_limit": byte_limit,
                "exceeds_byte_limit_if_selected": file_bytes > byte_limit,
                "selected_by_documented_precedence": False,
            }
            instruction_files.append(metadata)
            directory_candidates.append(metadata)
        if directory_candidates:
            selected_candidate = next(
                (
                    candidate
                    for candidate in directory_candidates
                    if int(candidate["file_bytes"]) > 0
                ),
                None,
            )
            if selected_candidate is not None:
                selected_candidate["selected_by_documented_precedence"] = True
                documented_selected_chain_upper_bound_bytes += int(
                    selected_candidate["file_bytes"]
                )

    return {
        "schema_version": "1.0",
        "repo_root": str(resolved_root),
        "cwd": str(resolved_cwd),
        "cwd_project_relative": resolved_cwd.relative_to(resolved_root).as_posix()
        or ".",
        "fallback_filenames": list(normalized_fallbacks),
        "byte_limit": byte_limit,
        "runtime_attestation": "not-verified",
        "global_codex_home_docs": "excluded-from-project-total",
        "instruction_files": instruction_files,
        "candidate_count": len(instruction_files),
        "candidate_metadata_total_bytes": candidate_metadata_total_bytes,
        "selected_chain_bounds": {
            "status": "not-verified",
            "documented_precedence": [
                "AGENTS.override.md",
                "AGENTS.md",
                "configured-fallbacks",
            ],
            "uncertainty": "Readability, active fallback configuration, and fresh-run runtime loading are not verified.",
            "lower_bound_bytes": 0,
            "upper_bound_bytes": documented_selected_chain_upper_bound_bytes,
            "byte_limit": byte_limit,
            "could_exceed_byte_limit": documented_selected_chain_upper_bound_bytes
            > byte_limit,
        },
        "diagnostics": diagnostics,
    }


def main() -> int:
    """Run the metadata-only project instruction audit."""
    parser = ValueFreeArgumentParser(
        description="Audit candidate Codex project instruction files without reading document content."
    )
    parser.add_argument(
        "--repo-root", required=True, type=Path, help="Repository root to audit"
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        help="Working directory inside the repository (defaults to repo root)",
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
    try:
        args = parser.parse_args()
    except ArgumentParseError:
        print(json.dumps(error_envelope(command_line_error()), indent=2))
        return 2

    try:
        audit = audit_codex_project_instruction_sources(
            args.repo_root,
            cwd=args.cwd,
            fallback_filenames=args.project_doc_fallback_filename,
            byte_limit=args.project_doc_byte_limit,
        )
    except ValueError as exc:
        del exc
        print(
            json.dumps(
                error_envelope(
                    InputFileError(
                        "invalid-configuration",
                        "configuration",
                        "Invalid configuration",
                    )
                ),
                indent=2,
            )
        )
        return 2

    print(json.dumps(audit, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
