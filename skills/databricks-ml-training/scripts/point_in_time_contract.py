"""Pure validators for point-in-time training examples and split membership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import combinations
import re
from typing import Any

RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def parse_rfc3339(value: str) -> datetime:
    """Parse an offset-aware RFC 3339 timestamp without accepting a naive value."""

    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        raise ValueError("timestamp must be an RFC 3339 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be valid RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def validate_pairwise_disjoint_split_ids(splits: Mapping[str, Sequence[str]]) -> None:
    """Reject any example ID shared by train, validation, or test.

    The caller supplies stable IDs after all temporal and entity-level split
    rules have been applied. Empty splits are rejected because they cannot
    support an evaluation claim.
    """

    expected = {"train", "validation", "test"}
    if set(splits) != expected:
        raise ValueError("splits must contain only train, validation, and test")
    normalized: dict[str, set[str]] = {}
    for name, ids in splits.items():
        if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence):
            raise ValueError(f"{name} split IDs must be a sequence of strings")
        if not ids:
            raise ValueError(f"{name} split must not be empty")
        if any(not isinstance(value, str) or not value for value in ids):
            raise ValueError(f"{name} split IDs must be non-empty strings")
        values = set(ids)
        if len(values) != len(ids):
            raise ValueError(f"{name} split contains duplicate example IDs")
        normalized[name] = values
    for first, second in combinations(sorted(normalized), 2):
        if normalized[first].intersection(normalized[second]):
            raise ValueError(
                "train, validation, and test example IDs must be pairwise disjoint"
            )


def select_feature_as_of(
    *,
    example_at: str,
    feature_rows: Sequence[Mapping[str, Any]],
    valid_from_key: str = "valid_from",
    valid_to_key: str = "valid_to",
    available_at_key: str = "available_at",
) -> Mapping[str, Any]:
    """Return the sole feature row valid and available at one prediction time.

    `feature_rows` must already come from the recorded immutable Delta version.
    This function handles the separate per-example validity rule:
    ``valid_from <= example_at < valid_to``. A row arriving after `example_at`
    is excluded even if its business-validity range starts earlier.
    """

    prediction_time = parse_rfc3339(example_at)
    matches: list[Mapping[str, Any]] = []
    for row in feature_rows:
        valid_from = parse_rfc3339(str(row[valid_from_key]))
        raw_valid_to = row.get(valid_to_key)
        valid_to = (
            parse_rfc3339(str(raw_valid_to)) if raw_valid_to is not None else None
        )
        available_at = parse_rfc3339(str(row[available_at_key]))
        is_current_at_example = valid_from <= prediction_time and (
            valid_to is None or prediction_time < valid_to
        )
        if is_current_at_example and available_at <= prediction_time:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one feature row valid and available at example_at"
        )
    return matches[0]
