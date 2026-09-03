"""Privacy-safe primitives shared by the Python debugging helpers.

The helpers consume failure artifacts, which regularly contain credentials, machine
paths, source excerpts, and terminal control characters. Keep all rendering and
machine-readable error messages behind this small, dependency-free boundary.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_DIAGNOSTIC_TEXT = 8_192
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/]+@", re.IGNORECASE)
_AUTH_HEADER = re.compile(
    r"\b(?:proxy-authorization|authorization)\s*:\s*(?:bearer|basic|dpop)\s+[^\s,;]+",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_DAPI_TOKEN = re.compile(r"\bdapi[a-z0-9_-]{8,}\b", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b", re.IGNORECASE)
_OPENAI_TOKEN = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_COOKIE_HEADER = re.compile(r"\b(?:set-cookie|cookie)\b[\"']?\s*[:=]\s*[\"']?[^\r\n]*", re.IGNORECASE)
_COOKIE_PAIR = re.compile(r"(?P<name>[A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;,\s}\]]+)")
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|auth(?:orization)?|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"secret|password|passwd|token|session(?:[_-]?(?:id|token|key))?|sid)\b(?:['\"])?\s*([:=])\s*(?:['\"])?[^\s,'\";]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedactionMetadata:
    """Facts about safe rendering, without retaining what was removed."""

    redaction_count: int = 0
    controls_removed: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def combine_metadata(*items: RedactionMetadata) -> RedactionMetadata:
    """Combine independently sanitized fields without exposing their contents."""
    return RedactionMetadata(
        redaction_count=sum(item.redaction_count for item in items),
        controls_removed=sum(item.controls_removed for item in items),
        truncated=any(item.truncated for item in items),
    )


def sanitize_text(value: object, *, limit: int = MAX_DIAGNOSTIC_TEXT) -> tuple[str, RedactionMetadata]:
    """Return bounded, single-line diagnostic text and redaction facts.

    This function is deliberately suitable for human output, JSON values, and
    exception messages. It never returns literal newlines or terminal controls.
    """
    text = value if isinstance(value, str) else str(value)
    text = _ANSI_ESCAPE.sub("", text)
    controls_removed = len(_CONTROL.findall(text)) + text.count("\r") + text.count("\n")
    text = text.replace("\r", " ").replace("\n", " ")
    text = _CONTROL.sub("", text)
    text = " ".join(text.split())
    redaction_count = 0

    def replace(_: re.Match[str]) -> str:
        nonlocal redaction_count
        redaction_count += 1
        return "[REDACTED]"

    for pattern in (
        _AUTH_HEADER,
        _BEARER_TOKEN,
        _DAPI_TOKEN,
        _JWT,
        _GITHUB_TOKEN,
        _OPENAI_TOKEN,
    ):
        text = pattern.sub(replace, text)

    def replace_cookie_header(match: re.Match[str]) -> str:
        def replace_cookie_pair(pair: re.Match[str]) -> str:
            nonlocal redaction_count
            redaction_count += 1
            return f"{pair.group('name')}=[REDACTED]"

        return _COOKIE_PAIR.sub(replace_cookie_pair, match.group(0))

    text = _COOKIE_HEADER.sub(replace_cookie_header, text)

    def replace_url(match: re.Match[str]) -> str:
        nonlocal redaction_count
        redaction_count += 1
        return f"{match.group(1)}[REDACTED]@"

    text = _URL_CREDENTIALS.sub(replace_url, text)

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal redaction_count
        redaction_count += 1
        return f"{match.group(0).split(match.group(1), 1)[0]}{match.group(1)}[REDACTED]"

    text = _SECRET_ASSIGNMENT.sub(replace_assignment, text)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip() + "…"
    return text, RedactionMetadata(redaction_count, controls_removed, truncated)


def safe_path(value: str | Path, *, repo_root: Path | None = None) -> str:
    """Return a repo-relative path when safely known, otherwise a basename."""
    try:
        path = Path(os.path.normpath(str(value)))
    except (TypeError, ValueError):
        return "[path]"
    if repo_root is not None:
        try:
            base = Path(os.path.normpath(str(repo_root)))
            relative = path.relative_to(base).as_posix()
            return sanitize_text(relative, limit=512)[0]
        except (OSError, ValueError):
            pass
    name = path.name
    return sanitize_text(name if name else "[path]", limit=256)[0]


def error_envelope(code: str, message: object) -> dict[str, Any]:
    """Build a stable sanitized error payload."""
    safe_message, metadata = sanitize_text(message)
    return {"code": code, "message": safe_message, "redaction": metadata.as_dict()}


def clamp_limit(value: int, *, minimum: int, maximum: int, name: str) -> int:
    """Validate an explicit resource override before using it for allocation."""
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def read_utf8_bounded(path: Path, *, max_bytes: int) -> str:
    """Read at most ``max_bytes`` bytes, rejecting oversized and invalid inputs."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise OSError("could not stat input file") from exc
    if size > max_bytes:
        raise ValueError("input exceeds configured byte limit")
    try:
        with path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise OSError("could not read input file") from exc
    if len(payload) > max_bytes:
        raise ValueError("input exceeds configured byte limit")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(exc.encoding, b"", 0, 0, "input is not valid UTF-8") from exc


def read_stdin_bounded(*, max_bytes: int) -> str:
    """Read bounded UTF-8 stdin without accepting an unbounded text stream."""
    payload = sys.stdin.buffer.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("input exceeds configured byte limit")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(exc.encoding, b"", 0, 0, "input is not valid UTF-8") from exc
