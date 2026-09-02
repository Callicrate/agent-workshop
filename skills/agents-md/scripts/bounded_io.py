#!/usr/bin/env python3
"""Bounded, repository-contained text input helpers for agents-md tools."""

from __future__ import annotations

import argparse
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_TEXT_FILE_BYTE_LIMIT = 1_048_576


@dataclass(frozen=True)
class InputFileError(Exception):
    """A stable input failure that is safe to expose without source values."""

    code: str
    label: str
    message: str

    def __str__(self) -> str:
        return self.message


class ArgumentParseError(Exception):
    """A value-free command-line parsing failure safe for JSON callers."""


class ValueFreeArgumentParser(argparse.ArgumentParser):
    """Raise a stable error instead of writing argument values to stderr."""

    def error(self, message: str) -> None:
        del message
        raise ArgumentParseError()


def command_line_error() -> InputFileError:
    """Return the stable error envelope used for invalid CLI arguments."""
    return InputFileError("invalid-arguments", "arguments", "Invalid command arguments")


def is_within(path: Path, root: Path) -> bool:
    """Return whether resolved ``path`` remains below resolved ``root``."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def has_unsafe_windows_path_syntax(value: str) -> bool:
    """Reject path spellings that could select Windows drives, devices, or SMB."""
    if not value:
        return False
    if value.startswith("\\") or value.startswith("//"):
        return True
    return len(value) >= 2 and value[0].isalpha() and value[1] == ":"


def resolve_repo_root(repo_root: Path) -> Path:
    """Resolve a repository root or raise a stable non-leaking error."""
    try:
        resolved = repo_root.resolve(strict=True)
        if not resolved.is_dir():
            raise OSError
    except (OSError, RuntimeError) as exc:
        raise InputFileError(
            "invalid-repo-root",
            "repo-root",
            "Repository root must be an accessible directory",
        ) from exc
    return resolved


def resolve_repo_file(repo_root: Path, path: Path, *, label: str) -> Path:
    """Preflight one regular repo-owned file without reading it."""
    root = resolve_repo_root(repo_root)
    raw_path = str(path)
    # A native absolute local path is needed by the CLI contract on Windows.
    # UNC/device spellings remain forbidden even when ``Path`` marks them
    # absolute; non-absolute Windows spellings are never joined to the root.
    is_network_or_device = raw_path.replace("/", "\\").startswith("\\\\")
    is_mixed_drive_path = (
        len(raw_path) >= 2
        and raw_path[0].isalpha()
        and raw_path[1] == ":"
        and "/" in raw_path
    )
    if (
        is_network_or_device
        or is_mixed_drive_path
        or (not path.is_absolute() and has_unsafe_windows_path_syntax(raw_path))
    ):
        raise InputFileError(
            f"{label}-unsafe-path", label, f"{label} uses an unsafe path syntax"
        )
    requested = path if path.is_absolute() else root / path
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InputFileError(
            f"{label}-unavailable",
            label,
            f"{label} must be an accessible regular file inside the repository",
        ) from exc
    if not is_within(resolved, root):
        raise InputFileError(
            f"{label}-escape", label, f"{label} must resolve inside the repository"
        )
    try:
        file_stat = resolved.stat()
    except OSError as exc:
        raise InputFileError(
            f"{label}-unavailable",
            label,
            f"{label} must be an accessible regular file inside the repository",
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise InputFileError(
            f"{label}-not-file",
            label,
            f"{label} must be a regular file inside the repository",
        )
    return resolved


def preflight_repo_files(
    repo_root: Path, paths: Iterable[tuple[str, Path]]
) -> dict[str, Path]:
    """Validate every requested file before the caller reads any file content."""
    root = resolve_repo_root(repo_root)
    prepared: dict[str, Path] = {}
    for label, path in paths:
        if label in prepared:
            raise InputFileError(
                "invalid-configuration", "configuration", "Input labels must be unique"
            )
        prepared[label] = resolve_repo_file(root, path, label=label)
    return prepared


def _contains_binary_controls(data: bytes) -> bool:
    allowed = {9, 10, 12, 13}
    return any(byte < 32 and byte not in allowed for byte in data)


def _stable_metadata(
    file_stat: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _open_no_follow(path: Path) -> int:
    """Open a final resolved path without silently dropping no-follow protection."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | no_follow
        return os.open(path, flags)
    if os.name != "nt":
        raise OSError("no-follow open is unavailable")

    # Windows does not expose O_NOFOLLOW here.  Open the reparse point itself
    # and reject it before converting the handle to a descriptor.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise OSError("Windows no-reparse open failed")
    try:

        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

        info = FileAttributeTagInfo()
        ok = ctypes.windll.kernel32.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok or info.attributes & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError("Windows no-reparse check failed")
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise


def read_repo_text(
    repo_root: Path,
    path: Path,
    *,
    label: str,
    byte_limit: int = DEFAULT_TEXT_FILE_BYTE_LIMIT,
    reject_hardlinks: bool = True,
) -> tuple[Path, str, int]:
    """Read one contained UTF-8 file through a stable bounded descriptor."""
    if byte_limit < 1:
        raise ValueError("byte_limit must be >= 1")
    resolved = resolve_repo_file(repo_root, path, label=label)
    try:
        before = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise InputFileError(
                f"{label}-not-file",
                label,
                f"{label} must be a regular file inside the repository",
            )
        descriptor = _open_no_follow(resolved)
        try:
            opened = os.fstat(descriptor)
            if reject_hardlinks and opened.st_nlink != 1:
                raise InputFileError(
                    f"{label}-hardlink",
                    label,
                    f"{label} must not be a multiply-linked file alias",
                )
            if (
                not stat.S_ISREG(opened.st_mode)
                or not os.path.samestat(before, opened)
                or opened.st_size != before.st_size
            ):
                raise InputFileError(
                    f"{label}-changed",
                    label,
                    f"{label} changed while it was being opened",
                )
            if opened.st_size > byte_limit:
                raise InputFileError(
                    f"{label}-too-large", label, "Input exceeds configured byte limit"
                )
            chunks: list[bytes] = []
            remaining = byte_limit + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
            path_after = os.stat(resolved, follow_symlinks=False)
            if _stable_metadata(opened) != _stable_metadata(
                after
            ) or not os.path.samestat(after, path_after):
                raise InputFileError(
                    f"{label}-changed",
                    label,
                    f"{label} changed while it was being read",
                )
            if reject_hardlinks and after.st_nlink != 1:
                raise InputFileError(
                    f"{label}-hardlink",
                    label,
                    f"{label} must not be a multiply-linked file alias",
                )
        finally:
            os.close(descriptor)
    except InputFileError:
        raise
    except OSError as exc:
        raise InputFileError(
            f"{label}-unavailable", label, f"{label} could not be read safely"
        ) from exc
    if len(data) > byte_limit:
        raise InputFileError(
            f"{label}-too-large", label, "Input exceeds configured byte limit"
        )
    if _contains_binary_controls(data):
        raise InputFileError(
            f"{label}-binary",
            label,
            f"{label} must contain UTF-8 text, not binary data",
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise InputFileError(
            f"{label}-invalid-utf8", label, f"{label} must contain valid UTF-8 text"
        ) from exc
    return resolved, text, len(data)


def error_envelope(error: InputFileError) -> dict[str, object]:
    """Return the documented bounded JSON error envelope."""
    return {
        "status": "error",
        "findings": [
            {
                "severity": "error",
                "code": error.code,
                "path": error.label,
                "line": 1,
                "message": error.message,
            }
        ],
    }
