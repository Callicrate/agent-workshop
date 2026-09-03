"""Shared project-specific skill marker contract helpers."""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from pathlib import Path

PROJECT_MARKER_NAME = "project-specific-skill"
PROJECT_MARKER_LINK = "[project-specific-skill](project-specific-skill)"
REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


class MarkerMissingError(FileNotFoundError):
    """Raised when a project-specific marker has no directory entry."""


class MarkerReadError(ValueError):
    """Raised when a marker cannot be read without following a changed entry."""


def project_leaf_slugify(value: str) -> str:
    """Return an ASCII lowercase hyphen slug for a project directory leaf."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def is_unsafe_windows_path(value: str) -> bool:
    """Return whether a path uses a UNC or Windows device namespace."""
    normalized = value.replace("/", "\\")
    return normalized.startswith("\\\\") or normalized.startswith("\\??\\")


def is_symlink_or_reparse(entry: os.stat_result) -> bool:
    """Return whether an lstat result identifies a symlink or reparse point."""
    attributes = getattr(entry, "st_file_attributes", 0)
    return stat.S_ISLNK(entry.st_mode) or bool(attributes & REPARSE_POINT_ATTRIBUTE)


def same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Return whether stat results identify the same file, or fail closed."""
    if not left.st_ino or not right.st_ino:
        return False
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def read_marker_bytes(marker_path: Path) -> bytes:
    """Read a regular marker from one verified descriptor without following swaps."""
    try:
        before_open = marker_path.lstat()
    except FileNotFoundError as exc:
        raise MarkerMissingError(marker_path) from exc
    except OSError as exc:
        raise MarkerReadError(f"unable to inspect project-specific-skill: {exc}") from exc
    if is_symlink_or_reparse(before_open):
        raise MarkerReadError("project-specific-skill must not be a symlink or reparse point")
    if not stat.S_ISREG(before_open.st_mode):
        raise MarkerReadError("project-specific-skill must be a regular file")

    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(marker_path, open_flags)
    except OSError as exc:
        raise MarkerReadError(f"unable to open project-specific-skill: {exc}") from exc

    try:
        descriptor_entry = os.fstat(descriptor)
        try:
            after_open = marker_path.lstat()
        except FileNotFoundError as exc:
            raise MarkerReadError("project-specific-skill changed while opening") from exc
        except OSError as exc:
            raise MarkerReadError(f"unable to recheck project-specific-skill: {exc}") from exc
        if (
            is_symlink_or_reparse(after_open)
            or not stat.S_ISREG(after_open.st_mode)
            or not stat.S_ISREG(descriptor_entry.st_mode)
            or not same_file_identity(before_open, descriptor_entry)
            or not same_file_identity(before_open, after_open)
        ):
            raise MarkerReadError("project-specific-skill changed while opening")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise MarkerReadError(f"unable to read project-specific-skill: {exc}") from exc
    finally:
        os.close(descriptor)


def marker_path_text(marker_text: str) -> tuple[str | None, str | None]:
    """Return a marker path without its permitted final newline, or an error."""
    if marker_text.endswith("\r\n"):
        path_text = marker_text[:-2]
    elif marker_text.endswith("\n"):
        path_text = marker_text[:-1]
    else:
        path_text = marker_text

    if "\r" in path_text or "\n" in path_text:
        return None, "project-specific-skill must contain exactly one path"
    return path_text, None


def validate_marker_text(marker_text: str) -> tuple[str | None, str | None]:
    """Validate marker text without accessing the named project path."""
    if marker_text.startswith("\ufeff"):
        return None, "project-specific-skill must not contain a BOM"

    project_path_text, line_error = marker_path_text(marker_text)
    if line_error:
        return None, line_error
    if project_path_text is None:
        return None, "project-specific-skill must contain exactly one path"
    if not project_path_text:
        return None, "project-specific-skill must not be blank"
    if project_path_text != project_path_text.strip():
        return None, "project-specific-skill must not have leading or trailing whitespace"
    if "\x00" in project_path_text:
        return None, "project-specific-skill must not contain NUL bytes"
    if "'" in project_path_text or '"' in project_path_text:
        return None, "project-specific-skill must not contain quotes"
    if is_unsafe_windows_path(project_path_text):
        return None, "project-specific-skill must not use a UNC or Windows device path"

    project_path = Path(project_path_text)
    if not project_path.is_absolute():
        return None, "project-specific-skill must contain an absolute project directory path"
    return project_path_text, None


def parse_marker_bytes(marker_bytes: bytes) -> tuple[str | None, str | None]:
    """Decode and validate marker bytes without accessing the named project path."""
    if marker_bytes.startswith(b"\xef\xbb\xbf"):
        return None, "project-specific-skill must not contain a UTF-8 BOM"
    try:
        marker_text = marker_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, f"project-specific-skill must be UTF-8: {exc.reason}"
    return validate_marker_text(marker_text)


def canonical_path_text_matches(project_path_text: str, resolved_path: Path) -> bool:
    """Compare a marker path to its resolved identity without accepting aliases."""
    resolved_text = str(resolved_path)
    if os.name == "nt":
        return project_path_text.replace("/", "\\").casefold() == resolved_text.replace("/", "\\").casefold()
    return project_path_text == resolved_text


def same_project_identity(left: Path, right: Path) -> bool:
    """Compare resolved project identities with Windows case semantics when needed."""
    return canonical_path_text_matches(str(left), right)


def reject_linked_path_components(project_path: Path) -> str | None:
    """Reject symlink or reparse components before probing any descendant."""
    anchor = Path(project_path.anchor)
    try:
        anchor_entry = anchor.lstat()
    except OSError as exc:
        return f"unable to inspect project-specific-skill path anchor: {exc}"
    if is_symlink_or_reparse(anchor_entry):
        return "project-specific-skill project path must not use a symlink or reparse point"

    current = anchor
    for component in project_path.parts[1:]:
        current = current / component
        try:
            entry = current.lstat()
        except FileNotFoundError:
            return "project-specific-skill project path does not exist"
        except OSError as exc:
            return f"unable to inspect project-specific-skill path: {exc}"
        if is_symlink_or_reparse(entry):
            return "project-specific-skill project path must not use a symlink or reparse point"
    return None


def resolve_marker_project_path(project_path_text: str) -> tuple[Path | None, str | None, str | None]:
    """Resolve a validated marker path and return its canonical project identity and slug."""
    validated_path_text, text_error = validate_marker_text(project_path_text)
    if text_error:
        return None, None, text_error
    if validated_path_text is None:
        return None, None, "project-specific-skill must contain exactly one path"

    project_path = Path(validated_path_text)
    component_error = reject_linked_path_components(project_path)
    if component_error:
        return None, None, component_error

    try:
        if not project_path.is_dir():
            return None, None, "project-specific-skill project path must be a directory"
        resolved_project_path = project_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, None, f"unable to resolve project-specific-skill path: {exc}"

    if not canonical_path_text_matches(validated_path_text, resolved_project_path):
        return None, None, "project-specific-skill must contain the canonical resolved project path"
    if resolved_project_path.parent == resolved_project_path:
        return None, None, "project-specific-skill must not identify a filesystem root"

    project_slug = project_leaf_slugify(resolved_project_path.name)
    if not project_slug:
        return None, None, "project-specific-skill project directory name has no valid ASCII slug"
    return resolved_project_path, project_slug, None
