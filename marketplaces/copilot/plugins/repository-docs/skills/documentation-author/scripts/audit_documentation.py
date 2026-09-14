"""Produce a bounded, privacy-safe inventory of project documentation."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

MAX_DEPTH = 12
MAX_FILES = 2_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 16_777_216
MAX_DIRECTORY_ENTRIES = 5_000
MAX_REDACTION_INPUT_CHARS = 8_192
MAX_PERCENT_DECODE_PASSES = 3
REDACTION_FAILURE = "<redacted-unsafe-input>"
MAX_NOTEBOOK_BYTES = MAX_FILE_BYTES
MAX_NOTEBOOK_NESTING = 64
MAX_NOTEBOOK_NODES = 50_000
MAX_NOTEBOOK_INTEGER_DIGITS = 1_024
MAX_NOTEBOOK_STRING_CHARS = 262_144
MAX_NOTEBOOK_LIST_ITEMS = 10_000
MAX_NOTEBOOK_MAP_ITEMS = 1_000
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
SUPPORTED_SUFFIXES = frozenset({".md", ".ipynb"})
CORE_DOCS = {
    "README.md": "Project entry point and setup.",
    "CHANGELOG.md": "Release history.",
    "docs/architecture.md": "System structure and major components.",
}
OPTIONAL_DOCS = {
    "docs/dataflow-diagram.md": "Optional. Only if the project already maintains diagram docs or the user explicitly asked.",
    "docs/functional-diagram.md": "Optional. Prefer prose or tables unless a diagram is explicitly useful.",
}
META_LABEL_RE = re.compile(
    r"\b(review packet|AI commentary|AI-generated summary|generated summary|analysis artifact)\b",
    re.IGNORECASE,
)
DO_NOT_EDIT_RE = re.compile(r"\b(do not edit|do-not-edit)\b", re.IGNORECASE)
AUTH_RE = re.compile(
    r"(?i)\b(?:proxy-authorization|authorization)\s*[:=]\s*(?:bearer|basic|dpop)\s+[^\s`'\"<>]+"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
GITHUB_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b", re.IGNORECASE)
OPENAI_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
DAPI_RE = re.compile(r"\bdapi[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>()\[\]{}]+", re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:access[_. -]*token|refresh[_. -]*token|client[_. -]*secret|"
    r"api[_. -]*key|auth(?:orization)?|cookie|password|passwd|pwd|secret|"
    r"signature|session(?:[_. -]*(?:id|key|token))?|oauth[_. -]*code|"
    r"auth(?:orization)?[_. -]*code|code|token)\s*[:=]"
)
SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:access|refresh|client|api|oauth|authorization|auth|session)?"
    r"[_. -]*(?:token|secret|key|password|passwd|pwd|signature|sig|sessionid|code)"
    r"[ \t]*[:=]"
)
MAX_LINKS_PER_FILE = 100_000


@dataclass(frozen=True)
class ScanIssue:
    """A stable scanner result that omits platform exception details."""

    path: str
    code: str


@dataclass(frozen=True)
class FileCandidate:
    """Metadata captured by the walker and verified against the opened handle."""

    relative: str
    device: int
    inode: int
    size: int
    links: int


def _is_reparse_or_symlink(path: Path) -> bool:
    """Reject symlinks, Windows junctions, and any other reparse point."""
    try:
        info = os.lstat(path)
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _relative_path(root: Path, path: Path) -> str:
    """Return only a slash-separated path already proven to be under root."""
    return path.relative_to(root).as_posix()


def _source_identities(files: list[FileCandidate]) -> dict[str, str]:
    """Assign stable document identities without emitting filename segments."""
    counters = {"markdown": 0, "notebook": 0}
    identities: dict[str, str] = {}
    for candidate in sorted(
        files, key=lambda item: (item.relative.casefold(), item.relative)
    ):
        kind = (
            "notebook"
            if candidate.relative.casefold().endswith(".ipynb")
            else "markdown"
        )
        counters[kind] += 1
        identities[candidate.relative] = f"{kind}-{counters[kind]}"
    return identities


def _report_path(relative: str, fallback: str) -> str:
    """Return a useful contained path unless redaction makes it unsafe to show."""
    return _safe_report_path(relative) or fallback


def _safe_report_path(relative: str) -> str | None:
    """Return a contained path only when output redaction leaves it unchanged."""
    return relative if _redact_text(relative) == relative else None


def _summarize_scan_errors(
    issues: list[ScanIssue], source_ids: dict[str, str]
) -> list[dict[str, object]]:
    """Bound noisy scanner limits while retaining each code and its full count."""
    grouped: dict[str, list[ScanIssue]] = {}
    for issue in sorted(issues, key=lambda item: (item.code, item.path)):
        grouped.setdefault(issue.code, []).append(issue)
    return [
        {
            "path": next(
                (
                    safe_path
                    for issue in group
                    if (safe_path := _safe_report_path(issue.path)) is not None
                ),
                _report_path(group[0].path, source_ids.get(group[0].path, "scan")),
            ),
            "code": code,
            "count": len(group),
        }
        for code, group in sorted(grouped.items())
    ]


def _bounded_files(root: Path) -> tuple[list[FileCandidate], list[ScanIssue]]:
    """Walk supported docs deterministically and enforce all limits before reads."""
    files: list[FileCandidate] = []
    issues: list[ScanIssue] = []
    total_bytes = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= MAX_DIRECTORY_ENTRIES:
                        issues.append(
                            ScanIssue(
                                _relative_path(root, directory), "directory_entry_limit"
                            )
                        )
                        break
                    entries.append(entry)
            entries.sort(key=lambda entry: entry.name.casefold())
        except OSError:
            issues.append(
                ScanIssue(_relative_path(root, directory), "directory_read_error")
            )
            continue
        child_directories: list[tuple[Path, int]] = []
        for entry in entries:
            candidate = Path(entry.path)
            if entry.name.casefold() in EXCLUDED_DIR_NAMES or _is_reparse_or_symlink(
                candidate
            ):
                continue
            try:
                entry_stat = os.lstat(candidate)
            except OSError:
                issues.append(
                    ScanIssue(_relative_path(root, candidate), "metadata_read_error")
                )
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if depth >= MAX_DEPTH:
                    issues.append(
                        ScanIssue(_relative_path(root, candidate), "depth_limit")
                    )
                else:
                    child_directories.append((candidate, depth + 1))
                continue
            if (
                not stat.S_ISREG(entry_stat.st_mode)
                or candidate.suffix.casefold() not in SUPPORTED_SUFFIXES
            ):
                continue
            relative = _relative_path(root, candidate)
            if len(files) >= MAX_FILES:
                issues.append(ScanIssue(".", "file_limit"))
                stack.clear()
                child_directories.clear()
                break
            if entry_stat.st_size > MAX_FILE_BYTES:
                issues.append(ScanIssue(relative, "file_too_large"))
                continue
            if total_bytes + entry_stat.st_size > MAX_TOTAL_BYTES:
                issues.append(ScanIssue(relative, "total_byte_limit"))
                continue
            files.append(
                FileCandidate(
                    relative=relative,
                    device=entry_stat.st_dev,
                    inode=entry_stat.st_ino,
                    size=entry_stat.st_size,
                    links=entry_stat.st_nlink,
                )
            )
            total_bytes += entry_stat.st_size
        stack.extend(reversed(child_directories))
    return files, issues


def _windows_final_path(file_descriptor: int) -> str | None:
    """Return the canonical path bound to a Windows file handle."""
    if os.name != "nt":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_path.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(file_descriptor)
    length = get_path(handle, None, 0, 0)
    if not length:
        return None
    buffer = ctypes.create_unicode_buffer(length + 1)
    if not get_path(handle, buffer, len(buffer), 0):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _open_no_follow(root: Path, relative: str) -> int:
    """Open one contained file without following a final or intermediate link."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        raise OSError("unsafe relative path")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
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
            str(root.joinpath(*parts)),
            0x80000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError("no-follow open failed")
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            raise
    required = ("O_NOFOLLOW", "O_DIRECTORY")
    if (
        any(not hasattr(os, name) for name in required)
        or os.open not in os.supports_dir_fd
    ):
        raise OSError("no-follow open unsupported")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    directory_fd = os.open(root, directory_flags)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(parts[-1], file_flags, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def _read_utf8(
    root: Path,
    candidate: FileCandidate,
    hook: Callable[[str], None] | None = None,
) -> tuple[str | None, str | None]:
    """Read once through a no-follow handle with pre/post identity checks."""
    file_descriptor: int | None = None
    try:
        file_descriptor = _open_no_follow(root, candidate.relative)
        before = os.fstat(file_descriptor)
    except OSError:
        return None, "read_error"
    try:
        attributes = getattr(before, "st_file_attributes", 0)
        if not stat.S_ISREG(before.st_mode) or attributes & 0x400:
            return None, "unsafe_file"
        if before.st_nlink != 1:
            return None, "hardlink_rejected"
        if before.st_size > MAX_FILE_BYTES:
            return None, "file_too_large"
        if (before.st_dev, before.st_ino, before.st_size, before.st_nlink) != (
            candidate.device,
            candidate.inode,
            candidate.size,
            candidate.links,
        ):
            return None, "file_changed_before_read"
        if os.name == "nt":
            final_path = _windows_final_path(file_descriptor)
            if final_path is None:
                return None, "containment_unavailable"
            try:
                common = os.path.commonpath(
                    (os.path.normcase(str(root)), os.path.normcase(final_path))
                )
            except ValueError:
                return None, "outside_root"
            if common != os.path.normcase(str(root)):
                return None, "outside_root"
        if hook is not None:
            hook(candidate.relative)
        chunks: list[bytes] = []
        remaining = MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(file_descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_nlink) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_nlink,
        ):
            return None, "file_changed_during_read"
        if len(data) > MAX_FILE_BYTES:
            return None, "file_too_large"
    except OSError:
        return None, "read_error"
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
    if b"\x00" in data or any(
        byte < 9 or 14 <= byte < 32 or byte == 127 for byte in data
    ):
        return None, "binary_or_nul"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"


def _mask_code(text: str) -> str:
    """Mask backtick and tilde fences plus inline code using position-preserving sentinels."""
    lines: list[str] = []
    fence_marker: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        leading = len(body) - len(body.lstrip(" \t"))
        fence = body[leading:] if leading <= 3 else ""
        marker = fence[:1]
        run = len(fence) - len(fence.lstrip(marker)) if marker in {"`", "~"} else 0
        if fence_marker is not None:
            if (
                marker == fence_marker
                and run >= fence_length
                and fence[run:].strip() == ""
            ):
                fence_marker = None
            lines.append("\0" * len(body) + newline)
            continue
        if marker in {"`", "~"} and run >= 3:
            fence_marker = marker
            fence_length = run
            lines.append("\0" * len(body) + newline)
            continue
        lines.append(_mask_inline_code(body, "\0") + newline)
    return "".join(lines)


def _mask_inline_code(body: str, mask_character: str) -> str:
    """Mask backtick spans in linear time without a backtracking expression."""
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(body):
        if body[index] != "`":
            index += 1
            continue
        end = index + 1
        while end < len(body) and body[end] == "`":
            end += 1
        runs.append((index, end - index))
        index = end
    next_matching: dict[int, int] = {}
    latest: dict[int, int] = {}
    for start, length in reversed(runs):
        if length in latest:
            next_matching[start] = latest[length]
        latest[length] = start
    run_lengths = dict(runs)
    masked = list(body)
    index = 0
    while index < len(body):
        length = run_lengths.get(index)
        if length is None:
            index += 1
            continue
        closing = next_matching.get(index)
        if closing is not None:
            for position in range(index, closing + length):
                masked[position] = mask_character
            index = closing + length
        else:
            index += length
    return "".join(masked)


def _active(masked: str, index: int) -> bool:
    return 0 <= index < len(masked) and masked[index] != "\0"


def _unescape_markdown(value: str) -> str:
    """Unescape one-character Markdown escapes without evaluating markup."""
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            output.append(value[index + 1])
            index += 2
        else:
            output.append(value[index])
            index += 1
    return "".join(output)


def _escaped_positions(source: str, masked: str) -> list[bool]:
    """Cache backslash parity for every active character in one pass."""
    escaped = [False] * len(source)
    backslashes = 0
    for index, character in enumerate(source):
        if not _active(masked, index):
            backslashes = 0
            continue
        escaped[index] = backslashes % 2 == 1
        backslashes = backslashes + 1 if character == "\\" else 0
    return escaped


def _delimiter_pairs(
    source: str, masked: str, opening: str, closing: str, escaped: list[bool]
) -> dict[int, int]:
    """Cache balanced active delimiters so malformed input cannot rescan suffixes."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, character in enumerate(source):
        if not _active(masked, index) or escaped[index]:
            continue
        if character == opening:
            stack.append(index)
        elif character == closing and stack:
            pairs[stack.pop()] = index
    return pairs


def _skip_space(source: str, masked: str, index: int, end: int) -> int:
    while index < end and _active(masked, index) and source[index] in " \t":
        index += 1
    return index


def _parse_definition(
    line: str,
    masked: str,
    brackets: dict[int, int],
    parentheses: dict[int, int],
    escaped: list[bool],
) -> tuple[str, str] | None:
    """Parse one CommonMark reference definition using cached delimiters."""
    index = 0
    while index < len(line) and line[index] == " " and _active(masked, index):
        index += 1
    if index > 3:
        return None
    label_end = brackets.get(index)
    if label_end is None:
        return None
    label_text = line[index + 1 : label_end]
    index = label_end + 1
    if index >= len(line) or line[index] != ":" or not _active(masked, index):
        return None
    index = _skip_space(line, masked, index + 1, len(line))
    if index >= len(line):
        return None
    if line[index] == "<" and _active(masked, index):
        end = index + 1
        while end < len(line):
            if not _active(masked, end):
                return None
            if line[end] == ">" and not escaped[end]:
                destination = line[index + 1 : end]
                if "<" in destination or any(
                    character in " \t" for character in destination
                ):
                    return None
                if (
                    _optional_title_end(
                        line, masked, end + 1, len(line), parentheses, escaped
                    )
                    is None
                ):
                    return None
                return label_text, destination
            end += 1
        return None
    end = index
    while end < len(line):
        if not _active(masked, end) or line[end] in " \t":
            break
        end += 1
    if (
        end == index
        or _optional_title_end(line, masked, end, len(line), parentheses, escaped)
        is None
    ):
        return None
    return label_text, line[index:end]


def _optional_title_end(
    source: str,
    masked: str,
    index: int,
    end: int,
    parentheses: dict[int, int],
    escaped: list[bool],
) -> int | None:
    """Accept only an exact optional CommonMark-style title and trailing space."""
    if index == end:
        return end
    title_start = _skip_space(source, masked, index, end)
    if title_start == index:
        return None
    if title_start == end:
        return end
    delimiter = source[title_start]
    if delimiter in {"'", '"'} and _active(masked, title_start):
        cursor = title_start + 1
        while cursor < end:
            if not _active(masked, cursor):
                return None
            if source[cursor] == delimiter and not escaped[cursor]:
                trailing = _skip_space(source, masked, cursor + 1, end)
                return end if trailing == end else None
            cursor += 1
        return None
    if delimiter == "(" and _active(masked, title_start):
        title_end = parentheses.get(title_start)
        if title_end is None or title_end >= end:
            return None
        trailing = _skip_space(source, masked, title_end + 1, end)
        return end if trailing == end else None
    return None


def _parse_inline_destination(
    source: str,
    masked: str,
    start: int,
    parentheses: dict[int, int],
    escaped: list[bool],
) -> tuple[str, int] | None:
    """Extract one balanced inline destination without exposing its title."""
    close = parentheses.get(start)
    if close is None:
        return None
    index = _skip_space(source, masked, start + 1, close)
    if index < close and source[index] == "<" and not escaped[index]:
        destination_start = index + 1
        index += 1
        while index < close:
            if source[index] == ">" and not escaped[index]:
                destination = source[destination_start:index]
                if "<" in destination or any(
                    character in " \t" for character in destination
                ):
                    return None
                if (
                    _optional_title_end(
                        source, masked, index + 1, close, parentheses, escaped
                    )
                    is None
                ):
                    return None
                return destination, close + 1
            index += 1
        return None
    destination_start = index
    while index < close:
        if not _active(masked, index):
            return None
        if source[index] in " \t" and not escaped[index]:
            break
        nested_close = parentheses.get(index)
        if source[index] == "(" and nested_close is not None and nested_close < close:
            index = nested_close + 1
            continue
        index += 1
    if _optional_title_end(source, masked, index, close, parentheses, escaped) is None:
        return None
    return source[destination_start:index], close + 1


def _normalize_reference(label: str) -> str:
    return " ".join(_unescape_markdown(label).split()).casefold()


def _is_nested_plain_link_label(label_start: int, latest_label_end: int) -> bool:
    """Check the sole active plain-link label interval while scanning left to right."""
    return label_start < latest_label_end


def _markdown_destinations(text: str) -> list[str]:
    """Extract destinations with cached delimiters and bounded linear work."""
    masked = _mask_code(text)
    source_lines = text.splitlines()
    masked_lines = masked.splitlines()
    definitions: dict[str, str] = {}
    definition_lines: set[int] = set()
    contexts: list[tuple[list[bool], dict[int, int], dict[int, int]]] = []
    for line, masked_line in zip(source_lines, masked_lines, strict=True):
        escaped = _escaped_positions(line, masked_line)
        contexts.append(
            (
                escaped,
                _delimiter_pairs(line, masked_line, "[", "]", escaped),
                _delimiter_pairs(line, masked_line, "(", ")", escaped),
            )
        )
    for number, (line, masked_line) in enumerate(
        zip(source_lines, masked_lines, strict=True)
    ):
        escaped, brackets, parentheses = contexts[number]
        definition = _parse_definition(
            line, masked_line, brackets, parentheses, escaped
        )
        if definition is not None:
            definitions.setdefault(_normalize_reference(definition[0]), definition[1])
            definition_lines.add(number)

    destinations: list[str] = []
    for number, (line, masked_line) in enumerate(
        zip(source_lines, masked_lines, strict=True)
    ):
        if number in definition_lines:
            continue
        escaped, brackets, parentheses = contexts[number]
        latest_plain_link_label_end = -1
        consumed_label_starts: set[int] = set()
        index = 0
        while index < len(line):
            image = (
                line[index : index + 2] == "!["
                and _active(masked_line, index)
                and _active(masked_line, index + 1)
                and not escaped[index]
            )
            if image:
                bracket_start = index + 1
            elif (
                line[index] == "["
                and _active(masked_line, index)
                and not escaped[index]
            ):
                bracket_start = index
                if index in consumed_label_starts or (
                    index > 0
                    and line[index - 1] == "!"
                    and _active(masked_line, index - 1)
                    and not escaped[index - 1]
                ):
                    index += 1
                    continue
            else:
                index += 1
                continue
            if not image and _is_nested_plain_link_label(
                bracket_start, latest_plain_link_label_end
            ):
                index += 1
                continue
            label_end = brackets.get(bracket_start)
            if label_end is None:
                index += 1
                continue
            label_text = line[bracket_start + 1 : label_end]
            after_label = label_end + 1
            destination: str | None = None
            if (
                after_label < len(line)
                and line[after_label] == "("
                and _active(masked_line, after_label)
            ):
                inline = _parse_inline_destination(
                    line, masked_line, after_label, parentheses, escaped
                )
                if inline is not None:
                    destination, next_index = inline
            elif (
                after_label < len(line)
                and line[after_label] == "["
                and _active(masked_line, after_label)
            ):
                reference_end = brackets.get(after_label)
                if reference_end is not None:
                    reference_label = line[after_label + 1 : reference_end]
                    consumed_label_starts.add(after_label)
                    destination = definitions.get(
                        _normalize_reference(reference_label or label_text)
                    )
            else:
                destination = definitions.get(_normalize_reference(label_text))
            if destination is not None:
                if not image:
                    latest_plain_link_label_end = label_end
                destinations.append(_unescape_markdown(destination))
                if len(destinations) >= MAX_LINKS_PER_FILE:
                    return destinations
            index += 1
    return destinations


def _has_scheme(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value))


def _is_absolute_like(value: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})", value))


def _exact_case_exists(path: Path, root: Path) -> bool:
    """Check every component without relying on case-insensitive host behavior."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        try:
            if part not in {child.name for child in current.iterdir()}:
                return False
        except OSError:
            return False
        current = current / part
    return True


def _link_status(document: Path, root: Path, destination: str) -> str:
    """Classify a destination while retaining none of its untrusted value."""
    raw = destination.strip()
    if not raw or raw.startswith("#"):
        return "fragment"
    if _is_absolute_like(raw):
        return "outside_root"
    if _has_scheme(raw) or raw.startswith("//"):
        return "external"
    path_part = raw.split("#", maxsplit=1)[0]
    if not path_part:
        return "fragment"
    lexical_candidate = document.parent / unquote(path_part)
    normalized_candidate = Path(os.path.abspath(lexical_candidate))
    try:
        normalized_candidate.relative_to(root)
    except ValueError:
        return "outside_root"
    try:
        lexical_info = os.lstat(normalized_candidate)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "unsafe_target"
    attributes = getattr(lexical_info, "st_file_attributes", 0)
    if stat.S_ISLNK(lexical_info.st_mode) or attributes & 0x400:
        return "unsafe_target"
    try:
        candidate = normalized_candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return "unsafe_target"
    try:
        candidate.relative_to(root)
    except ValueError:
        return "outside_root"
    if not _exact_case_exists(candidate, root):
        return "case_mismatch"
    return "ok"


def _redact_text(value: str) -> str:
    """Remove unsafe text before an untrusted value can reach any output."""
    if len(value) > MAX_REDACTION_INPUT_CHARS:
        return REDACTION_FAILURE
    decoded = value
    for _ in range(MAX_PERCENT_DECODE_PASSES):
        decoded_next = re.sub(
            r"%([0-9A-Fa-f]{2})",
            lambda match: chr(int(match.group(1), 16)),
            decoded,
        )
        if decoded_next == decoded:
            break
        decoded = decoded_next
    else:
        if re.search(r"%[0-9A-Fa-f]{2}", decoded):
            return REDACTION_FAILURE
    normalized = unicodedata.normalize("NFKC", decoded)
    if len(normalized) > MAX_REDACTION_INPUT_CHARS or any(
        unicodedata.category(character) in {"Cc", "Cf"}
        or unicodedata.bidirectional(character)
        in {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
        for character in normalized
    ):
        return REDACTION_FAILURE
    collapsed = re.sub(r"[^a-z0-9:=]+", "", normalized.casefold())
    if SENSITIVE_KEY_RE.search(normalized) or re.search(
        r"(?:accesstoken|refreshtoken|clientsecret|apikey|authorization|"
        r"oauthcode|authcode|session(?:id|key|token)?|password|passwd|pwd|"
        r"signature|token|secret|code)[:=]",
        collapsed,
    ):
        return "<redacted-sensitive-value>"
    redacted = AUTH_RE.sub("<redacted-auth>", normalized)
    redacted = BEARER_RE.sub("<redacted-auth>", redacted)
    redacted = GITHUB_RE.sub("<redacted-github-token>", redacted)
    redacted = OPENAI_RE.sub("<redacted-openai-key>", redacted)
    redacted = DAPI_RE.sub("<redacted-dapi-token>", redacted)
    redacted = JWT_RE.sub("<redacted-jwt>", redacted)
    redacted = SECRET_ASSIGNMENT_RE.sub("<redacted-secret>", redacted)
    redacted = URL_RE.sub("<redacted-url>", redacted)
    return "".join(
        "<control>" if ord(character) < 32 or ord(character) == 127 else character
        for character in redacted
    )


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _escape_human(value: str) -> str:
    """Escape Markdown-significant text after recursive output redaction."""
    safe = _redact_text(value)
    for character in "\\`*_{}[]<>()#!|":
        safe = safe.replace(character, f"\\{character}")
    return safe


def _markdown_code_span(value: str) -> str:
    """Render one already-redacted value as a punctuation-preserving code span."""
    safe = _redact_text(value)
    longest_run = max((len(run) for run in re.findall(r"`+", safe)), default=0)
    delimiter = "`" * (longest_run + 1)
    if safe.startswith(("`", " ")) or safe.endswith(("`", " ")):
        safe = f" {safe} "
    return f"{delimiter}{safe}{delimiter}"


class NotebookLimitError(ValueError):
    """A deterministic bounded-notebook parsing outcome."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _notebook_failure(relative: str, code: str) -> tuple[dict[str, object], ScanIssue]:
    return {
        "path": relative,
        "code_cells": 0,
        "markdown_cells": 0,
        "parse_error": code,
    }, ScanIssue(relative, code)


def _notebook_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_NOTEBOOK_INTEGER_DIGITS:
        raise NotebookLimitError("notebook_integer_digit_limit")
    return int(value)


def _notebook_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len(pairs) > MAX_NOTEBOOK_MAP_ITEMS:
        raise NotebookLimitError("notebook_map_limit")
    result: dict[str, object] = {}
    for key, value in pairs:
        if len(key) > MAX_NOTEBOOK_STRING_CHARS:
            raise NotebookLimitError("notebook_string_limit")
        if key in result:
            raise NotebookLimitError("duplicate_notebook_key")
        result[key] = value
    return result


def _reject_notebook_constant(_: str) -> object:
    raise NotebookLimitError("invalid_notebook_json")


def _notebook_shape_code(data: object) -> str | None:
    """Check the fully parsed JSON tree without recursive traversal."""
    stack: list[tuple[object, int]] = [(data, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NOTEBOOK_NODES:
            return "notebook_node_limit"
        if depth > MAX_NOTEBOOK_NESTING:
            return "notebook_nesting_limit"
        if isinstance(value, str):
            if len(value) > MAX_NOTEBOOK_STRING_CHARS:
                return "notebook_string_limit"
        elif isinstance(value, list):
            if len(value) > MAX_NOTEBOOK_LIST_ITEMS:
                return "notebook_list_limit"
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, dict):
            if len(value) > MAX_NOTEBOOK_MAP_ITEMS:
                return "notebook_map_limit"
            for key, item in value.items():
                if len(key) > MAX_NOTEBOOK_STRING_CHARS:
                    return "notebook_string_limit"
                stack.append((item, depth + 1))
    return None


def _summarize_notebook(
    path: Path, relative: str, text: str
) -> tuple[dict[str, object], ScanIssue | None]:
    """Validate a resource-bounded notebook shape before counting cells."""
    if len(text.encode("utf-8")) > MAX_NOTEBOOK_BYTES:
        return _notebook_failure(relative, "notebook_byte_limit")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_notebook_object,
            parse_int=_notebook_integer,
            parse_constant=_reject_notebook_constant,
        )
    except NotebookLimitError as error:
        return _notebook_failure(relative, error.code)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _notebook_failure(relative, "invalid_notebook_json")
    shape_code = _notebook_shape_code(data)
    if shape_code is not None:
        return _notebook_failure(relative, shape_code)
    if not isinstance(data, dict):
        return _notebook_failure(relative, "invalid_notebook_root")
    cells = data.get("cells")
    if not isinstance(cells, list):
        return _notebook_failure(relative, "invalid_notebook_cells")
    code_cells = 0
    markdown_cells = 0
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("cell_type"), str):
            return _notebook_failure(relative, "invalid_notebook_cell")
        if cell["cell_type"] == "code":
            code_cells += 1
        elif cell["cell_type"] == "markdown":
            markdown_cells += 1
    return {
        "path": relative,
        "code_cells": code_cells,
        "markdown_cells": markdown_cells,
        "parse_error": "",
    }, None


def build_report(root: Path) -> dict[str, object]:
    """Build a deterministic audit and retain only safe root-relative identifiers."""
    files, scan_errors = _bounded_files(root)
    source_ids = _source_identities(files)
    report_paths = {
        candidate.relative: _report_path(
            candidate.relative, source_ids[candidate.relative]
        )
        for candidate in files
    }
    markdown_text: dict[str, str] = {}
    notebooks: list[tuple[Path, str, str, str]] = []
    file_errors: list[ScanIssue] = []
    for candidate in files:
        relative = candidate.relative
        source_id = source_ids[relative]
        text, error = _read_utf8(root, candidate)
        if error:
            file_errors.append(ScanIssue(report_paths[relative], error))
            continue
        assert text is not None
        path = root / relative
        if path.suffix.casefold() == ".md":
            markdown_text[relative] = text
        else:
            notebooks.append((path, relative, source_id, text))
    markdown_files = sorted(markdown_text)
    links: list[dict[str, str]] = []
    for relative in markdown_files:
        document = root / Path(relative)
        for ordinal, destination in enumerate(
            _markdown_destinations(markdown_text[relative]), start=1
        ):
            links.append(
                {
                    "ordinal": ordinal,
                    "source": report_paths[relative],
                    "status": _link_status(document, root, destination),
                }
            )
    links.sort(
        key=lambda item: (
            item["source"],
            item["ordinal"],
            item["status"],
        )
    )
    summaries: list[dict[str, object]] = []
    for path, relative, source_id, text in notebooks:
        summary, error = _summarize_notebook(path, report_paths[relative], text)
        summaries.append(summary)
        if error is not None:
            file_errors.append(error)
    existing_set = set(markdown_files)
    core = [
        {"path": path, "present": path in existing_set, "purpose": purpose}
        for path, purpose in CORE_DOCS.items()
    ]
    optional = [
        {"path": path, "present": path in existing_set, "purpose": purpose}
        for path, purpose in OPTIONAL_DOCS.items()
    ]
    missing_core = [item["path"] for item in core if not item["present"]]
    guides: list[str] = []
    if "README.md" in missing_core:
        guides.append("references/guide-readme.md")
    if "docs/architecture.md" in missing_core:
        guides.append("references/guide-architecture.md")
    if "CHANGELOG.md" in missing_core:
        guides.append("references/guide-changelog.md")
    report = {
        "project_root": ".",
        "existing_markdown": [report_paths[path] for path in markdown_files],
        "readme_casing": sorted(
            report_paths[path]
            for path in markdown_files
            if Path(path).name.casefold() == "readme.md"
            and Path(path).name != "README.md"
        ),
        "markdown_links": links,
        "broken_markdown_links": [
            item
            for item in links
            if item["status"] not in {"ok", "external", "fragment"}
        ],
        "notebook_summaries": sorted(summaries, key=lambda item: str(item["path"])),
        "do_not_edit_mentions": [
            report_paths[path]
            for path in markdown_files
            if DO_NOT_EDIT_RE.search(markdown_text[path])
        ],
        "meta_label_mentions": [
            report_paths[path]
            for path in markdown_files
            if META_LABEL_RE.search(markdown_text[path])
        ],
        "core_documents": core,
        "optional_documents": optional,
        "missing_core_documents": missing_core,
        "recommended_guides": guides,
        "diagram_policy": "Treat diagram documents as opt-in. Do not generate them by default.",
        "scan_errors": _summarize_scan_errors(scan_errors, source_ids),
        "file_errors": [issue.__dict__ for issue in file_errors],
    }
    return _redact_value(report)  # type: ignore[return-value]


def print_markdown_report(report: dict[str, object]) -> None:
    """Emit a Markdown-safe human rendering of an already-redacted report."""
    print("# Documentation Audit\n")
    print("Project root: `.`\n")
    print("## Core Documents")
    for item in report["core_documents"]:  # type: ignore[index]
        entry = item  # type: ignore[assignment]
        status = "present" if entry["present"] else "missing"
        print(
            f"- {_markdown_code_span(str(entry['path']))}: {status} - {_escape_human(str(entry['purpose']))}"
        )
    print("\n## Optional Documents")
    for item in report["optional_documents"]:  # type: ignore[index]
        entry = item  # type: ignore[assignment]
        status = "present" if entry["present"] else "not present"
        print(
            f"- {_markdown_code_span(str(entry['path']))}: {status} - {_escape_human(str(entry['purpose']))}"
        )
    print("\n## Recommended Guides")
    guides = report["recommended_guides"]  # type: ignore[index]
    if guides:
        for guide in guides:
            print(f"- {_markdown_code_span(str(guide))}")
    else:
        print("- none")
    print("\n## Drift Checks")
    broken = report["broken_markdown_links"]  # type: ignore[index]
    if broken:
        print("- broken or suspicious Markdown links:")
        for item in broken:
            print(
                f"  - {_markdown_code_span(str(item['source']))} link "
                f"#{item['ordinal']} ({_escape_human(str(item['status']))})"
            )
    else:
        print("- Markdown links: ok")
    for label in ("scan_errors", "file_errors"):
        errors = report[label]  # type: ignore[index]
        if errors:
            print(f"- {label.replace('_', ' ')}:")
            for item in errors:
                count = int(item.get("count", 1))
                suffix = "" if count == 1 else f" (count={count})"
                print(
                    f"  - {_markdown_code_span(str(item['path']))}: {_escape_human(str(item['code']))}{suffix}"
                )
    for label, description in (
        ("readme_casing", "noncanonical README casing"),
        ("do_not_edit_mentions", "do-not-edit sidecar mentions"),
        ("meta_label_mentions", "generated/meta label mentions"),
    ):
        paths = report[label]  # type: ignore[index]
        if paths:
            print(f"- {description}:")
            for path in paths:
                print(f"  - {_markdown_code_span(str(path))}")
    summaries = report["notebook_summaries"]  # type: ignore[index]
    if summaries:
        print("\n## Notebook Summaries")
        for item in summaries:
            print(
                f"- {_markdown_code_span(str(item['path']))}: code_cells={item['code_cells']}, "
                f"markdown_cells={item['markdown_cells']} ({_escape_human(str(item['parse_error'] or 'ok'))})"
            )


class CliUsageError(Exception):
    """A usage failure whose raw argparse message must not be emitted."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Suppress raw argv values in usage errors."""

    def error(self, message: str) -> None:
        raise CliUsageError from None


def build_parser() -> argparse.ArgumentParser:
    """Build the audit CLI."""
    parser = SafeArgumentParser(description="Audit project documentation")
    parser.add_argument("project_root", help="Directory to scan")
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of Markdown"
    )
    return parser


def _emit_json(*, ok: bool, result: object | None, error_code: str | None) -> None:
    """Emit the single JSON envelope used by every outcome."""
    error = None if error_code is None else {"code": error_code}
    print(json.dumps({"error": error, "ok": ok, "result": result}, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    """Run a deterministic bounded audit."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in arguments
    try:
        args = build_parser().parse_args(arguments)
    except CliUsageError:
        if json_requested:
            _emit_json(ok=False, result=None, error_code="usage_error")
        else:
            print("[FAIL] usage_error")
        return 2
    root = Path(args.project_root).expanduser()
    if not root.exists() or not root.is_dir() or _is_reparse_or_symlink(root):
        if args.json:
            _emit_json(ok=False, result=None, error_code="invalid_target")
        else:
            print("[FAIL] target: expected a readable non-reparse directory")
        return 1
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        if args.json:
            _emit_json(ok=False, result=None, error_code="unreadable_target")
        else:
            print("[FAIL] target: unreadable_target")
        return 1
    report = build_report(resolved_root)
    if args.json:
        failed = bool(report["scan_errors"] or report["file_errors"])
        _emit_json(
            ok=not failed,
            result=report,
            error_code="audit_failed" if failed else None,
        )
    else:
        print_markdown_report(report)
    return 1 if report["scan_errors"] or report["file_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
