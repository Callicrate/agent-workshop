"""Derive one run-level publication decision from label-gate result rows."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Mapping
from typing import Any


ALLOW_DECISION = "ALLOW_PUBLISH"
BLOCK_DECISION = "BLOCK_PUBLISH"
ALLOW_STATUSES = frozenset({"ok", "ok_zero_variance_unchanged"})
MAX_GATE_RESULTS_JSON_CHARS = 1_000_000
CANONICAL_IDENTITY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_GATE_ROW_FIELDS = frozenset(
    {"gate_scoring_run_id", "gate_run_contract_digest", "publish_gate"}
)
LABEL_GATE_QUERY_ROW_FIELDS = frozenset(
    {
        "gate_scoring_run_id",
        "gate_run_contract_digest",
        "label_name",
        "is_contract_gate_row",
        "bucket_rows",
        "label_rows",
        "label_share",
        "mean_share",
        "std_share",
        "expected_label_count",
        "expected_row_count",
        "staged_row_count",
        "scoreable_row_count",
        "unscorable_row_count",
        "unknown_label_count",
        "unknown_label_set_digest",
        "unexpected_null_count",
        "share_z_score",
        "publish_gate",
    }
)
QUERY_COUNT_FIELDS = frozenset(
    {
        "bucket_rows",
        "label_rows",
        "expected_label_count",
        "staged_row_count",
        "scoreable_row_count",
        "unscorable_row_count",
        "unknown_label_count",
        "unexpected_null_count",
    }
)
QUERY_OPTIONAL_COUNT_FIELDS = frozenset({"expected_row_count"})
QUERY_REQUIRED_NUMBER_FIELDS = frozenset({"label_share"})
QUERY_OPTIONAL_NUMBER_FIELDS = frozenset({"mean_share", "std_share", "share_z_score"})
MAX_LABEL_GATE_QUERY_ROWS = 100_000
MAX_LABEL_NAME_CHARS = 512
MAX_GATE_STATUS_CHARS = 128
MAX_COUNT_VALUE = (1 << 63) - 1
MAX_ABS_DIAGNOSTIC_INTEGER = 10**308


def is_canonical_identity(value: Any) -> bool:
    """Return whether value is a canonical lowercase SHA-256 identity string."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and CANONICAL_IDENTITY_PATTERN.fullmatch(value) is not None
    )


def parse_canonical_identity(value: str) -> str:
    """Argparse type for a nonblank canonical identity without value reflection."""
    if not is_canonical_identity(value):
        raise argparse.ArgumentTypeError(
            "must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _valid_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT_VALUE


def _valid_finite_number(value: Any) -> bool:
    if type(value) is int:
        return -MAX_ABS_DIAGNOSTIC_INTEGER <= value <= MAX_ABS_DIAGNOSTIC_INTEGER
    if type(value) is float:
        return math.isfinite(value)
    return False


def project_label_gate_query_rows(rows: Any) -> list[dict[str, str]]:
    """Validate the full SQL row schema and project exact core gate fields."""
    if not isinstance(rows, list) or len(rows) > MAX_LABEL_GATE_QUERY_ROWS:
        raise ValueError("invalid label-gate query result")
    projected: list[dict[str, str]] = []
    run_unknown_label_count: int | None = None
    run_unknown_label_set_digest: str | None = None
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or set(row.keys()) != LABEL_GATE_QUERY_ROW_FIELDS
        ):
            raise ValueError("invalid label-gate query row")
        if not is_canonical_identity(row["gate_scoring_run_id"]):
            raise ValueError("invalid label-gate query row")
        if not is_canonical_identity(row["gate_run_contract_digest"]):
            raise ValueError("invalid label-gate query row")
        if row["gate_scoring_run_id"] != row["gate_run_contract_digest"]:
            raise ValueError("invalid label-gate query row")
        label_name = row["label_name"]
        if not (
            label_name is None
            or (
                isinstance(label_name, str)
                and 0 < len(label_name) <= MAX_LABEL_NAME_CHARS
            )
        ):
            raise ValueError("invalid label-gate query row")
        if type(row["is_contract_gate_row"]) is not bool:
            raise ValueError("invalid label-gate query row")
        if any(not _valid_count(row[field]) for field in QUERY_COUNT_FIELDS):
            raise ValueError("invalid label-gate query row")
        unknown_label_count = row["unknown_label_count"]
        if run_unknown_label_count is None:
            run_unknown_label_count = unknown_label_count
        elif unknown_label_count != run_unknown_label_count:
            raise ValueError("invalid label-gate query row")
        unknown_label_set_digest = row["unknown_label_set_digest"]
        if not is_canonical_identity(unknown_label_set_digest):
            raise ValueError("invalid label-gate query row")
        if run_unknown_label_set_digest is None:
            run_unknown_label_set_digest = unknown_label_set_digest
        elif unknown_label_set_digest != run_unknown_label_set_digest:
            raise ValueError("invalid label-gate query row")
        if any(
            row[field] is not None and not _valid_count(row[field])
            for field in QUERY_OPTIONAL_COUNT_FIELDS
        ):
            raise ValueError("invalid label-gate query row")
        if any(
            not _valid_finite_number(row[field])
            for field in QUERY_REQUIRED_NUMBER_FIELDS
        ):
            raise ValueError("invalid label-gate query row")
        if any(
            row[field] is not None and not _valid_finite_number(row[field])
            for field in QUERY_OPTIONAL_NUMBER_FIELDS
        ):
            raise ValueError("invalid label-gate query row")
        publish_gate = row["publish_gate"]
        if not (
            isinstance(publish_gate, str)
            and 0 < len(publish_gate) <= MAX_GATE_STATUS_CHARS
            and publish_gate == publish_gate.strip()
        ):
            raise ValueError("invalid label-gate query row")
        projected.append(
            {
                "gate_scoring_run_id": row["gate_scoring_run_id"],
                "gate_run_contract_digest": row["gate_run_contract_digest"],
                "publish_gate": publish_gate,
            }
        )
    return projected


def derive_publication_decision(
    rows: Any,
    expected_scoring_run_id: Any,
    expected_run_contract_digest: Any,
) -> dict[str, Any]:
    """Return the exact run-level decision and fixed reason for gate rows."""
    if (
        not is_canonical_identity(expected_scoring_run_id)
        or not is_canonical_identity(expected_run_contract_digest)
        or expected_scoring_run_id != expected_run_contract_digest
    ):
        return {
            "publication_decision": BLOCK_DECISION,
            "publication_reason": "invalid_expected_contract",
            "gate_row_count": len(rows) if isinstance(rows, list) else 0,
        }
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping)
        or set(row.keys()) != EXPECTED_GATE_ROW_FIELDS
        or any(not isinstance(row[field], str) for field in EXPECTED_GATE_ROW_FIELDS)
        for row in rows
    ):
        return {
            "publication_decision": BLOCK_DECISION,
            "publication_reason": "invalid_gate_row",
            "gate_row_count": len(rows) if isinstance(rows, list) else 0,
        }
    if not rows:
        return {
            "publication_decision": BLOCK_DECISION,
            "publication_reason": "empty_gate_result",
            "gate_row_count": 0,
        }

    if any(
        not is_canonical_identity(row["gate_scoring_run_id"])
        or not is_canonical_identity(row["gate_run_contract_digest"])
        or row["gate_scoring_run_id"] != row["gate_run_contract_digest"]
        or row["gate_scoring_run_id"] != expected_scoring_run_id
        or row["gate_run_contract_digest"] != expected_run_contract_digest
        for row in rows
    ):
        return {
            "publication_decision": BLOCK_DECISION,
            "publication_reason": "mixed_gate_contract",
            "gate_row_count": len(rows),
        }

    if any(row["publish_gate"] not in ALLOW_STATUSES for row in rows):
        return {
            "publication_decision": BLOCK_DECISION,
            "publication_reason": "non_allow_status",
            "gate_row_count": len(rows),
        }

    return {
        "publication_decision": ALLOW_DECISION,
        "publication_reason": "all_label_gates_allow",
        "gate_row_count": len(rows),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the offline JSON decision CLI."""
    parser = argparse.ArgumentParser(
        description="Derive a batch-inference run publication decision from gate rows"
    )
    parser.add_argument(
        "--expected-scoring-run-id", required=True, type=parse_canonical_identity
    )
    parser.add_argument(
        "--expected-run-contract-digest", required=True, type=parse_canonical_identity
    )
    gate_input = parser.add_mutually_exclusive_group(required=True)
    gate_input.add_argument(
        "--gate-results-json",
        help="JSON array of exact three-field core gate objects",
    )
    gate_input.add_argument(
        "--label-gate-query-results-json",
        help="JSON array using the closed diagnostic-rich SQL row schema",
    )
    return parser


def main() -> int:
    """Parse bounded JSON, derive the decision, and print canonical JSON."""
    parser = build_parser()
    args = parser.parse_args()
    if args.expected_scoring_run_id != args.expected_run_contract_digest:
        parser.error("expected run ID and contract digest must be identical")
    gate_results_json = (
        args.gate_results_json
        if args.gate_results_json is not None
        else args.label_gate_query_results_json
    )
    if len(gate_results_json) > MAX_GATE_RESULTS_JSON_CHARS:
        parser.error(
            f"gate results JSON exceeds {MAX_GATE_RESULTS_JSON_CHARS} characters"
        )
    try:
        rows = json.loads(gate_results_json)
    except json.JSONDecodeError as error:
        parser.error(f"gate results JSON is invalid: {error.msg}")
    except ValueError:
        parser.error("gate results JSON contains an invalid numeric token")
    unknown_label_count: int | None = None
    unknown_label_set_digest: str | None = None
    if args.label_gate_query_results_json is not None:
        rich_rows = rows
        try:
            rows = project_label_gate_query_rows(rows)
        except ValueError:
            parser.error("invalid label-gate query results")
        if rich_rows:
            unknown_label_count = rich_rows[0]["unknown_label_count"]
            unknown_label_set_digest = rich_rows[0]["unknown_label_set_digest"]
    result = derive_publication_decision(
        rows,
        args.expected_scoring_run_id,
        args.expected_run_contract_digest,
    )
    if unknown_label_count is not None and unknown_label_set_digest is not None:
        result["unknown_label_count"] = unknown_label_count
        result["unknown_label_set_digest"] = unknown_label_set_digest
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
