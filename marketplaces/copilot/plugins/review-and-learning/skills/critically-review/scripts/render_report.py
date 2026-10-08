#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.18", "referencing>=0.30"]
# ///

"""Render a schema-valid critical-review report as readable, escaped Markdown."""

from __future__ import annotations

import html
import json
import re
import unicodedata
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


def text(value: str, *, inline: bool = False) -> str:
    """Escape report text without turning prose into serialized data or markup.

    Only this renderer supplies Markdown structure. Input markup is displayed
    literally; it cannot add HTML, images, links, headings, lists, or fences.
    """
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        f"\\u{ord(char):04x}"
        if unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in "\n\t"
        else char
        for char in value
    )
    if inline:
        value = " ".join(value.split())
    lines = []
    for line in value.split("\n"):
        line = html.escape(line.strip(), quote=False)
        line = re.sub(r"([\\`*_\[\]#!|~])", r"\\\1", line)
        # Strip indentation and escape remaining block openers, including
        # numbered lists and setext headings. Normal prose punctuation stays.
        line = re.sub(r"^([-+=])", r"\\\1", line)
        line = re.sub(r"^(\d{1,9})([.)])", r"\1\\\2", line)
        lines.append(line)
    return "\n".join(lines)


def bullet(value: str) -> str:
    return "- " + text(value).replace("\n", "\n  ")


def assessment_lines(assessment: dict[str, str]) -> list[str]:
    verdict, rationale = assessment["summary"].split(":", 1)
    return [
        f"**{text(verdict)}:** {text(rationale.strip())}",
        "",
        f"**Confidence:** {text(assessment['confidence'])}",
        "",
    ]


def action_lines(assessment: dict[str, str]) -> list[str]:
    parts = []
    for line in assessment["decision_impact"].splitlines():
        label, value = line.split(":", 1)
        parts.extend([f"**{text(label)}:** {text(value.strip())}", ""])
    return parts


def document_lines(document: dict[str, str]) -> list[str]:
    return [
        f"**Artifact:** {text(document['artifact_type'], inline=True)}  ",
        f"**Domain:** {text(document['domain'], inline=True)}  ",
        f"**Review mode:** {text(document['review_mode'], inline=True)}",
        "",
    ]


def render(report: dict[str, Any]) -> str:
    """Map validated report fields to reader-facing Markdown, not JSON fences."""
    document = report["document"]
    parts = [f"# {text(document['title'], inline=True) or 'Critical Review Report'}", ""]
    parts.extend(["## Overall Assessment", ""])
    parts.extend(assessment_lines(report["overall_assessment"]))
    parts.extend(["## Scope and Materials Reviewed", ""])
    parts.extend(document_lines(document))
    researched = "Yes" if report["scope"]["research_performed"] else "No"
    parts.extend([f"**External research performed:** {researched}", ""])
    parts.extend(bullet(item) for item in report["scope"]["materials_reviewed"])
    parts.append("")
    if report["scope"]["limitations"]:
        parts.extend(["### Coverage Limits", ""])
        parts.extend(bullet(item) for item in report["scope"]["limitations"])
        parts.append("")
    if report["strengths"]:
        parts.extend(["## What Holds Up", ""])
        parts.extend(bullet(item) for item in report["strengths"])
        parts.append("")
    parts.extend(["## Detailed Findings", ""])
    if not report["findings"]:
        parts.extend(["No findings recorded within the reviewed scope.", ""])
    for finding in report["findings"]:
        parts.extend([
            f"### {text(finding['id'])}: {text(finding['title'], inline=True)}",
            "",
            f"**Severity:** {text(finding['severity'])} | "
            f"**Confidence:** {text(finding['confidence'])} | "
            f"**Category:** {text(finding['category'])} / {text(finding['issue_type'])}",
            "",
        ])
        if "evidence_quality" in finding:
            parts.extend([f"**Evidence quality:** {text(finding['evidence_quality'])}", ""])
        parts.extend([
            f"**Claim location:** {text(finding['location'])}", "",
            "\n".join("> " + line for line in text(finding["quote"]).split("\n")), "",
            text(finding["analysis"]), "",
            f"**Why it matters:** {text(finding['why_it_matters'])}", "",
            f"**Observed evidence:** {text(finding['observed_evidence'])}", "",
            "**Sources:**", "",
        ])
        for evidence in finding["evidence"]:
            parts.append(bullet(
                f"{evidence['reference']} ({evidence['source_type']}) — {evidence['notes']}"
            ))
        parts.append("")
        for field, label in (
            ("decision_impact", "Decision impact"),
            ("recommended_fix", "Recommended fix"),
            ("repair_shape", "Smallest repair"),
            ("verification_needed", "Verification needed"),
        ):
            parts.extend([f"**{label}:** {text(finding[field])}", ""])
        if finding.get("origins"):
            parts.extend(["**Finding provenance:**", ""])
            for origin in finding["origins"]:
                parts.append(bullet(
                    f"{origin['source']} — {origin['finding_id']} ({origin['source_id']})"
                ))
            parts.append("")
    for field, heading in (
        ("omissions", "Material Omissions"), ("open_questions", "Open Questions")
    ):
        if report[field]:
            parts.extend([f"## {heading}", ""])
            parts.extend(bullet(item) for item in report[field])
            parts.append("")
    parts.extend(["## Decision and Next Action", ""])
    parts.extend(action_lines(report["overall_assessment"]))
    if report.get("source_reports"):
        parts.extend(["## Source Reports", ""])
        for source in report["source_reports"]:
            parts.extend([
                f"### {text(source['document']['title'], inline=True) or 'Untitled source report'}",
                "",
                f"**Source:** {text(source['source'], inline=True)} "
                f"({text(source['source_id'])})", "",
            ])
            parts.extend(document_lines(source["document"]))
            parts.extend(assessment_lines(source["overall_assessment"]))
            parts.extend(action_lines(source["overall_assessment"]))
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
