"""Bound and redact diagnostic values before they leave runtime probes.

This module deliberately treats diagnostics as untrusted: environment values,
paths, subprocess output, and exception messages can all contain credentials.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping
from typing import Any

MAX_TEXT_LENGTH = 512
MAX_COLLECTION_ITEMS = 64
REDACTED = "[REDACTED]"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic|dpop)\s+[^\s,;]+")
_DAPI_TOKEN = re.compile(r"(?i)\bdapi[a-z0-9_-]{8,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b([a-z][a-z0-9_.-]*(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|credential)[a-z0-9_.-]*)"
    r"\s*([:=])\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(?i)\b(https?://)[^\s/@:]+(?::[^\s/@]*)?@")
_URL_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|apikey|auth(?:orization)?|credential|password|passwd|secret|token)=[^&#\s]*)"
)


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject invalid CLI input without reflecting an untrusted argument value."""

    def error(self, message: str) -> None:
        del message
        self.exit(2, "error: invalid_cli_argument\n")


def redact_text(value: object, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Return one bounded, single-line, credential-safe diagnostic string."""
    text = str(value)
    text = _CONTROL_CHARS.sub(" ", text)
    text = _URL_USERINFO.sub(r"\1" + REDACTED + "@", text)
    text = _URL_SECRET_QUERY.sub(lambda match: match.group(1).split("=", 1)[0] + "=" + REDACTED, text)
    text = _AUTH_SCHEME.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    text = _DAPI_TOKEN.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = " ".join(text.split())
    if limit < 1:
        return ""
    if len(text) > limit:
        return f"{text[: max(0, limit - 1)]}…"
    return text


def redact_structure(value: Any, *, depth: int = 0) -> Any:
    """Recursively bound and redact JSON-compatible diagnostic data.

    A depth and item cap prevent a diagnostic failure from becoming a memory or
    logging failure. Unknown objects are represented only by their redacted
    string representation.
    """
    if depth >= 8:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return "[BINARY REDACTED]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                redacted["truncated_items"] = True
                break
            redacted[redact_text(key, limit=96)] = redact_structure(child, depth=depth + 1)
        return redacted
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        result = [redact_structure(item, depth=depth + 1) for item in items[:MAX_COLLECTION_ITEMS]]
        if len(items) > MAX_COLLECTION_ITEMS:
            result.append("[TRUNCATED]")
        return result
    return redact_text(value)


def safe_error(operation: str, exc: BaseException) -> dict[str, str]:
    """Describe an expected probe failure without exposing exception text."""
    error_type = redact_text(type(exc).__name__, limit=80)
    return {
        "error_type": error_type,
        "reason": redact_text(f"{operation} failed ({error_type})", limit=160),
    }
