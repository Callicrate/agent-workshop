"""Canonical JSON-value semantics for serving signature logical types."""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any


MAX_TEMPORAL_CHARS = 64
MAX_BINARY_DECODED_BYTES = 24_000
MAX_BINARY_ENCODED_CHARS = 4 * ((MAX_BINARY_DECODED_BYTES + 2) // 3)
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TIMESTAMP_PATTERN = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})T"
    r"(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9]):(?P<second>[0-5][0-9])"
    r"(?P<fraction>\.[0-9]{3}|\.[0-9]{6})?"
    r"(?P<zone>Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)


def is_canonical_date(value: Any) -> bool:
    """Accept only a real date that round-trips as ``YYYY-MM-DD``."""

    if (
        not isinstance(value, str)
        or len(value) != 10
        or not DATE_PATTERN.fullmatch(value)
    ):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def is_canonical_timestamp(value: Any) -> bool:
    """Accept bounded RFC3339 seconds with no, 3, or 6 fractional digits."""

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_TEMPORAL_CHARS
        or (match := TIMESTAMP_PATTERN.fullmatch(value)) is None
        or not is_canonical_date(match.group("date"))
    ):
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    fraction = match.group("fraction")
    timespec = (
        "seconds"
        if fraction is None
        else "milliseconds"
        if len(fraction) == 4
        else "microseconds"
    )
    canonical = parsed.isoformat(timespec=timespec)
    if value.endswith("Z") and canonical.endswith("+00:00"):
        canonical = canonical[:-6] + "Z"
    return canonical == value


def is_canonical_base64(value: Any) -> bool:
    """Accept nonempty standard Base64 with strict padding and a decoded cap."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_BINARY_ENCODED_CHARS
        or len(value) % 4 != 0
    ):
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return (
        len(decoded) <= MAX_BINARY_DECODED_BYTES
        and base64.b64encode(decoded).decode("ascii") == value
    )


def matches_logical_type(value: Any, logical_type: str) -> bool:
    """Validate one non-null JSON value against the canonical serving type."""

    if logical_type == "boolean":
        return isinstance(value, bool)
    if logical_type in {"integer", "long"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if logical_type in {"float", "double", "decimal"}:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if logical_type == "string":
        return isinstance(value, str)
    if logical_type == "date":
        return is_canonical_date(value)
    if logical_type == "timestamp":
        return is_canonical_timestamp(value)
    if logical_type == "binary":
        return is_canonical_base64(value)
    if logical_type == "array":
        return isinstance(value, list)
    if logical_type in {"map", "struct"}:
        return isinstance(value, Mapping)
    return False
