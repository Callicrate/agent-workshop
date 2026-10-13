#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.18", "referencing>=0.30"]
# ///

"""Validate bounded reports or standalone findings lists for critically-review."""

from __future__ import annotations

import json
import sys

from review_io import (
    SafeArgumentParser,
    add_error_format,
    close_snapshots,
    read_json,
    safe_identifier,
    safe_main,
    snapshot_input,
    validate_payload,
)


def main() -> int:
    parser = SafeArgumentParser(
        description="Validate a bounded critically-review report or findings list."
    )
    parser.add_argument(
        "--report", required=True, help="Report JSON or standalone findings-list path."
    )
    add_error_format(parser)
    args = parser.parse_args()

    snapshot = snapshot_input(args.report)
    try:
        value = read_json(snapshot)
        kind, errors = validate_payload(value, allow_findings_list=True)
        if errors:
            code = errors[0]
            if args.json_errors:
                print(
                    json.dumps({"ok": False, "error": {"code": code}}, sort_keys=True)
                )
            else:
                print(f"ERROR: {code}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "kind": kind,
                    "report": safe_identifier(snapshot.path),
                    "valid": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        close_snapshots([snapshot])


if __name__ == "__main__":
    raise SystemExit(safe_main(main))
