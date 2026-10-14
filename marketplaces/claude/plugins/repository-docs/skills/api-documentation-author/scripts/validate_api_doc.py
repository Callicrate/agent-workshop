"""Validate REST API Markdown without traversing or reporting unsafe input."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_DEPTH = 12
MAX_FILES = 2_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 16_777_216
MAX_DIRECTORY_ENTRIES = 5_000
MAX_REDACTION_INPUT_CHARS = 8_192
MAX_PERCENT_DECODE_PASSES = 3
REDACTION_FAILURE = "<redacted-unsafe-input>"
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
ATX_HEADING_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marks>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*$",
    re.MULTILINE,
)
ENDPOINT_TEXT_RE = re.compile(
    r"(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[ \t]+(?P<path>/\S*)"
)
UNFILLED_PLACEHOLDER_RE = re.compile(r"\.\.\.")
STARTER_TEMPLATE_MARKERS = (
    (re.compile(r"<\s*api(?:[\s_-]+)name\s*>", re.IGNORECASE), "API name"),
    (
        re.compile(r"<\s*package(?:[\s_-]+)name\s*>", re.IGNORECASE),
        "package name",
    ),
)
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

REQUIRED_ENDPOINT_SECTIONS = {
    "example request": ("example request",),
    "example response": ("example response", "example success response"),
    "success responses": ("success responses", "success response"),
    "error responses": ("error responses", "error response"),
}
EMPTY_SECTION_PHRASES = (
    "no special headers required",
    "this endpoint does not accept",
    "there are no specific",
    "none.",
    "not applicable",
)


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


def _escape_human(value: str) -> str:
    """Escape Markdown-significant text after redaction."""
    safe = _redact_text(value)
    for character in "\\`*_{}[]<>()#!|":
        safe = safe.replace(character, f"\\{character}")
    return safe


@dataclass(frozen=True)
class ScanIssue:
    """A stable scanner outcome that does not expose OS exceptions."""

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


@dataclass(frozen=True)
class Heading:
    """One non-fenced CommonMark ATX heading."""

    level: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class EndpointBlock:
    """One endpoint heading and its structurally bounded content."""

    ordinal: int
    level: int
    start: int
    end: int


@dataclass
class ValidationResult:
    """Structured validation findings for one relative Markdown file."""

    filepath: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(_redact_text(message))

    def warn(self, message: str) -> None:
        self.warnings.append(_redact_text(message))

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_reparse_or_symlink(path: Path) -> bool:
    """Reject symlinks, Windows junctions, and other reparse points."""
    try:
        info = os.lstat(path)
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _relative_path(root: Path, path: Path) -> str:
    """Return a slash-separated, root-relative path after containment is proven."""
    return path.relative_to(root).as_posix()


def _bounded_markdown_files(root: Path) -> tuple[list[FileCandidate], list[ScanIssue]]:
    """Walk Markdown files with deterministic ordering and pre-read resource caps."""
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
                or candidate.suffix.casefold() != ".md"
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


def mask_code(text: str) -> str:
    """Mask fenced and inline code while preserving line and character positions."""
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
            lines.append(" " * len(body) + newline)
            continue
        if marker in {"`", "~"} and run >= 3:
            fence_marker = marker
            fence_length = run
            lines.append(" " * len(body) + newline)
            continue

        lines.append(_mask_inline_code(body, " ") + newline)
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


def _heading_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.strip()).casefold()


def _headings(masked_text: str) -> list[Heading]:
    """Parse non-fenced ATX headings and valid optional closing sequences."""
    headings: list[Heading] = []
    for match in ATX_HEADING_RE.finditer(masked_text):
        text = match.group("text") or ""
        text = re.sub(r"[ \t]+#+[ \t]*$", "", text).strip()
        headings.append(
            Heading(len(match.group("marks")), text, match.start(), match.end())
        )
    return headings


def find_unlabeled_code_blocks(text: str) -> list[str]:
    """Return warnings for opening fences that do not declare a language."""
    warnings: list[str] = []
    active: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip(" \t")
        marker = stripped[:1]
        run = (
            len(stripped) - len(stripped.lstrip(marker)) if marker in {"`", "~"} else 0
        )
        if active is not None:
            if (
                marker == active[0]
                and run >= active[1]
                and stripped[run:].strip() == ""
            ):
                active = None
            continue
        if marker in {"`", "~"} and run >= 3:
            active = (marker, run)
            if not stripped[run:].strip():
                warnings.append(f"Line {number}: code block without language tag")
    return warnings


def find_starter_template_residue(masked_text: str) -> list[str]:
    """Return line-specific warnings for unfilled starter markers in prose."""
    matches: set[tuple[int, str]] = set()
    for pattern, label in STARTER_TEMPLATE_MARKERS:
        for match in pattern.finditer(masked_text):
            line = masked_text[: match.start()].count("\n") + 1
            matches.add((line, label))
    return [
        f"Line {line}: starter template residue '{label}'"
        for line, label in sorted(matches)
    ]


def _split_gfm_row(row: str) -> list[str] | None:
    """Tokenize one GFM table row, respecting escapes and optional outer pipes."""
    stripped = row.strip()
    if "|" not in stripped:
        return None
    start = 1 if stripped.startswith("|") else 0
    trailing_pipe = len(stripped) - 1
    preceding_backslashes = 0
    cursor = trailing_pipe - 1
    while cursor >= 0 and stripped[cursor] == "\\":
        preceding_backslashes += 1
        cursor -= 1
    end = (
        trailing_pipe
        if stripped.endswith("|") and preceding_backslashes % 2 == 0
        else len(stripped)
    )
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[start:end]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    cells.append("".join(current).strip())
    return cells


def _is_delimiter_cell(cell: str) -> bool:
    return bool(re.fullmatch(r":?-{3,}:?", cell.strip()))


def find_table_issues(text: str) -> list[str]:
    """Validate GFM tables outside code, including escaped-pipe column parity."""
    issues: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = _split_gfm_row(lines[index])
        if header is None or index + 1 >= len(lines):
            index += 1
            continue
        delimiter = _split_gfm_row(lines[index + 1])
        if delimiter is None:
            index += 1
            continue
        if not delimiter or not all(_is_delimiter_cell(cell) for cell in delimiter):
            if any("-" in cell for cell in delimiter):
                issues.append(f"Line {index + 2}: invalid table delimiter row")
            index += 1
            continue
        if len(header) != len(delimiter):
            issues.append(
                f"Line {index + 2}: table delimiter column count does not match header"
            )
        row_index = index + 2
        while row_index < len(lines):
            row = _split_gfm_row(lines[row_index])
            if row is None:
                break
            if len(row) != len(header):
                issues.append(
                    f"Line {row_index + 1}: table row column count does not match header"
                )
            row_index += 1
        index = row_index
    return issues


def _endpoint_blocks(masked_text: str) -> list[EndpointBlock]:
    """Return endpoint blocks with a precomputed boundary for every endpoint."""
    headings = _headings(masked_text)
    return _endpoint_layout(headings, len(masked_text))[0]


def _endpoint_layout(
    headings: list[Heading], text_length: int
) -> tuple[list[EndpointBlock], dict[int, set[str]]]:
    """Bound and collect endpoint sections in linear time without tail slicing."""
    blocks: list[EndpointBlock] = []
    boundaries: dict[int, int] = {}
    next_endpoint: int | None = None
    next_at_or_shallower: list[int | None] = [None] * 7
    endpoint_indexes: set[int] = set()
    for index in range(len(headings) - 1, -1, -1):
        heading = headings[index]
        is_endpoint = ENDPOINT_TEXT_RE.fullmatch(heading.text) is not None
        if is_endpoint:
            endpoint_indexes.add(index)
            candidates = [
                candidate
                for candidate in (next_endpoint, next_at_or_shallower[heading.level])
                if candidate is not None
            ]
            boundaries[index] = min(candidates) if candidates else len(headings)
        for level in range(heading.level, 7):
            next_at_or_shallower[level] = index
        if is_endpoint:
            next_endpoint = index
    ordinal = 0
    for index, heading in enumerate(headings):
        if index not in endpoint_indexes:
            continue
        ordinal += 1
        boundary_index = boundaries[index]
        end = (
            headings[boundary_index].start
            if boundary_index < len(headings)
            else text_length
        )
        blocks.append(EndpointBlock(ordinal, heading.level, heading.end, end))
    sections = {block.ordinal: set() for block in blocks}
    block_index = 0
    for heading in headings:
        while block_index < len(blocks) and heading.start >= blocks[block_index].end:
            block_index += 1
        if block_index >= len(blocks):
            break
        block = blocks[block_index]
        if block.start <= heading.start < block.end and heading.level > block.level:
            sections[block.ordinal].add(_heading_text(heading.text))
    return blocks, sections


def _section_headings(
    masked_text: str, start: int, end: int, endpoint_level: int
) -> set[str]:
    """Return actual subsection headings within one endpoint block."""
    return {
        _heading_text(heading.text)
        for heading in _headings(masked_text)
        if start <= heading.start < end and heading.level > endpoint_level
    }


def validate_file(
    filepath: Path,
    root: Path,
    candidate: FileCandidate | None = None,
    hook: Callable[[str], None] | None = None,
    source_identity: str = "markdown-1",
) -> ValidationResult:
    """Validate one already-contained, regular Markdown file."""
    relative = (
        candidate.relative if candidate is not None else _relative_path(root, filepath)
    )
    result = ValidationResult(source_identity)
    if candidate is None:
        try:
            info = os.lstat(filepath)
        except OSError:
            result.error(f"{source_identity}: read_error")
            return result
        candidate = FileCandidate(
            relative, info.st_dev, info.st_ino, info.st_size, info.st_nlink
        )
    text, read_error = _read_utf8(root, candidate, hook)
    if read_error:
        result.error(f"{source_identity}: {read_error}")
        return result
    assert text is not None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = mask_code(text)
    masked_lower = masked.casefold()

    for warning in find_unlabeled_code_blocks(text):
        result.warn(warning)
    if not re.search(r"^#[ \t]+\S+", masked, re.MULTILINE):
        result.warn("missing top-level title heading")

    endpoint_blocks, endpoint_sections = _endpoint_layout(
        _headings(masked), len(masked)
    )
    if not endpoint_blocks and "endpoint" in relative.casefold():
        result.warn("no endpoint headings found (expected ## METHOD /path format)")
    for block in endpoint_blocks:
        headings = endpoint_sections[block.ordinal]
        for label, aliases in REQUIRED_ENDPOINT_SECTIONS.items():
            if not any(alias in headings for alias in aliases):
                result.error(f"endpoint {block.ordinal}: missing '{label}' heading")

    for phrase in EMPTY_SECTION_PHRASES:
        location = masked_lower.find(phrase)
        if location >= 0:
            line = masked[:location].count("\n") + 1
            result.warn(f"Line {line}: empty-section filler phrase '{phrase}'")
    for match in UNFILLED_PLACEHOLDER_RE.finditer(masked):
        line = masked[: match.start()].count("\n") + 1
        result.warn(f"Line {line}: placeholder '...'")
    for warning in find_starter_template_residue(masked):
        result.warn(warning)
    for issue in find_table_issues(masked):
        result.warn(issue)
    return result


def _direct_target(
    target: Path,
) -> tuple[FileCandidate | None, Path | None, str | None]:
    """Validate a direct target without emitting its raw or absolute path."""
    if _is_reparse_or_symlink(target):
        return None, None, "target: unsafe_target"
    try:
        resolved = target.resolve(strict=True)
        info = os.lstat(resolved)
    except OSError:
        return None, None, "target: unreadable_target"
    if not stat.S_ISREG(info.st_mode) or resolved.suffix.casefold() != ".md":
        return None, None, "target: expected a regular .md file"
    return (
        FileCandidate(
            resolved.name, info.st_dev, info.st_ino, info.st_size, info.st_nlink
        ),
        resolved.parent,
        None,
    )


class CliUsageError(Exception):
    """A usage failure whose raw argparse message must not be emitted."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Suppress raw argv values in usage errors."""

    def error(self, message: str) -> None:
        raise CliUsageError from None


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded validator command line."""
    parser = SafeArgumentParser(description="Validate REST API Markdown docs")
    parser.add_argument(
        "target", help="A regular .md file or a documentation directory"
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Return a non-zero exit code when warnings are found",
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON")
    return parser


def _emit_json(*, ok: bool, result: object | None, error_code: str | None) -> None:
    """Emit the single JSON envelope used by every outcome."""
    error = None if error_code is None else {"code": error_code}
    print(json.dumps({"error": error, "ok": ok, "result": result}, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    """Run validation with stable human-readable findings and exit status."""
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
    target = Path(args.target).expanduser()
    if not target.exists():
        if args.json:
            _emit_json(ok=False, result=None, error_code="missing_target")
        else:
            print("[FAIL] target: missing_target")
        return 1
    directory_target = target.is_dir()
    if directory_target:
        if _is_reparse_or_symlink(target):
            if args.json:
                _emit_json(ok=False, result=None, error_code="unsafe_target")
            else:
                print("[FAIL] target: unsafe_target")
            return 1
        try:
            root = target.resolve(strict=True)
        except OSError:
            if args.json:
                _emit_json(ok=False, result=None, error_code="unreadable_target")
            else:
                print("[FAIL] target: unreadable_target")
            return 1
        files, scan_issues = _bounded_markdown_files(root)
    else:
        candidate, root, direct_error = _direct_target(target)
        if direct_error or candidate is None or root is None:
            code = (direct_error or "invalid_target").split(": ")[-1].replace(" ", "_")
            if args.json:
                _emit_json(ok=False, result=None, error_code=code)
            else:
                print(f"[FAIL] {direct_error}")
            return 1
        files, scan_issues = [candidate], []
    if not files and not scan_issues:
        if args.json:
            _emit_json(ok=False, result=None, error_code="no_markdown_files")
        else:
            print("[FAIL] target: no_markdown_files")
        return 1

    all_ok = not scan_issues
    warnings_found = False
    results: list[ValidationResult] = []
    for ordinal, candidate in enumerate(files, start=1):
        result = validate_file(
            root / candidate.relative,
            root,
            candidate,
            source_identity=(
                candidate.relative if directory_target else f"markdown-{ordinal}"
            ),
        )
        results.append(result)
        if not args.json:
            file_failed = not result.ok or (
                args.fail_on_warnings and bool(result.warnings)
            )
            print(
                f"[{'FAIL' if file_failed else 'PASS'}] {_escape_human(result.filepath)}"
            )
        for error in result.errors:
            if not args.json:
                print(f"  ERROR: {_escape_human(error)}")
            all_ok = False
        for warning in result.warnings:
            if not args.json:
                print(f"  WARN:  {_escape_human(warning)}")
            warnings_found = True
    if not args.json:
        for issue in scan_issues:
            print(
                f"[FAIL] {_escape_human(issue.path)}\n  ERROR: {_escape_human(issue.code)}"
            )
    failed = not all_ok or (args.fail_on_warnings and warnings_found)
    if args.json:
        payload = {
            "files": [
                {
                    "errors": result.errors,
                    "ok": result.ok,
                    "path": _redact_text(result.filepath),
                    "warnings": result.warnings,
                }
                for result in results
            ],
            "scan_errors": [
                {"code": issue.code, "path": _redact_text(issue.path)}
                for issue in scan_issues
            ],
        }
        _emit_json(
            ok=not failed,
            result=payload,
            error_code="validation_failed" if failed else None,
        )
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
