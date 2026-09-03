#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.18", "referencing>=0.30"]
# ///

"""Render a schema-valid critical-review report as inert Markdown literals."""

from __future__ import annotations

import json
from typing import Any

from review_io import (
    SafeArgumentParser,
    ToolError,
    add_error_format,
    close_snapshots,
    read_json,
    require_valid_payload,
    safe_identifier,
    safe_main,
    snapshot_input,
    write_output,
)


def _longest_run(text: str, character: str) -> int:
    longest = 0
    current = 0
    for value in text:
        if value == character:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def literal(value: Any) -> str:
    """Keep every untrusted scalar inside a fence it cannot close."""
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    fence = "`" * max(3, _longest_run(rendered, "`") + 1)
    return f"{fence}text\n{rendered}\n{fence}"


def render(report: dict[str, Any]) -> str:
    """Use a fixed heading vocabulary; no report data becomes Markdown syntax."""
    parts = [
        "# Critical Review Report",
        "",
        "## Document",
        "",
        literal(report["document"]),
        "",
        "## Scope and Materials Reviewed",
        "",
        literal(report["scope"]),
        "",
        "## Overall Assessment",
        "",
        literal(
            {
                "summary": report["overall_assessment"]["summary"],
                "confidence": report["overall_assessment"]["confidence"],
            }
        ),
        "",
        "## What Holds Up",
        "",
        literal(report["strengths"]),
        "",
        "## Detailed Findings",
        "",
    ]
    if report["findings"]:
        for number, finding in enumerate(report["findings"], start=1):
            parts.extend([f"### Finding {number}", "", literal(finding), ""])
    else:
        parts.append(literal([]))
        parts.append("")
    parts.extend(
        [
            "## Material Omissions",
            "",
            literal(report["omissions"]),
            "",
            "## Open Questions",
            "",
            literal(report["open_questions"]),
            "",
            "## Decision and Next Action",
            "",
            literal(report["overall_assessment"]["decision_impact"]),
        ]
    )
    if "source_reports" in report:
        parts.extend(["", "## Source Metadata", "", literal(report["source_reports"])])
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = SafeArgumentParser(
        description="Render a schema-valid critical-review JSON report as safe Markdown."
    )
    parser.add_argument("--report", required=True, help="Input report JSON path.")
    parser.add_argument(
        "--output", required=True, help="New Markdown path, unless --force is explicit."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Use supported safe replacement for an existing output.",
    )
    add_error_format(parser)
    args = parser.parse_args()

    snapshot = snapshot_input(args.report)
    try:
        report = read_json(snapshot)
        if not isinstance(report, dict):
            raise ToolError("unsupported-json-shape")
        require_valid_payload(report, allow_findings_list=False)
        output = write_output(render(report), args.output, [snapshot], force=args.force)
        print(
            json.dumps(
                {
                    "output": safe_identifier(output),
                    "findings": len(report["findings"]),
                },
                sort_keys=True,
            )
        )
    finally:
        close_snapshots([snapshot])
    return 0


if __name__ == "__main__":
    raise SystemExit(safe_main(main))
