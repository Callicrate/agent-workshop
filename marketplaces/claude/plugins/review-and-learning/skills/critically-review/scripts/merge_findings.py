#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.18", "referencing>=0.30"]
# ///

"""Merge validated critical-review findings without adjudicating dissent."""

from __future__ import annotations

import copy
import json
from typing import Any

from review_io import (
    InputSnapshot,
    MAX_FINDINGS,
    MAX_INPUT_FILES,
    SafeArgumentParser,
    ToolError,
    add_error_format,
    close_snapshots,
    json_payload,
    read_json,
    require_valid_payload,
    safe_identifier,
    safe_main,
    snapshot_input,
    write_output,
)

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "note": 0}
GENERIC_DOCUMENT = {
    "title": "Merged Critical Review Findings",
    "artifact_type": "multiple",
    "domain": "multiple",
    "review_mode": "standard",
}
MIXED_ASSESSMENT = {
    "summary": (
        "insufficient-evidence: Source assessments conflict, so the merge cannot "
        "establish one decision without adjudication."
    ),
    "decision_impact": (
        "Next action: Reconcile the conflicting source assessments without "
        "discarding their evidence.\n"
        "Validation gate: One decision-ready assessment is supported by the "
        "retained source evidence."
    ),
    "confidence": "low",
}
UNKNOWN_DOCUMENT = {
    "title": "Metadata unavailable for standalone findings",
    "artifact_type": "unknown",
    "domain": "unknown",
    "review_mode": "standard",
}
UNKNOWN_ASSESSMENT = {
    "summary": (
        "insufficient-evidence: Standalone findings do not establish a "
        "document-level decision."
    ),
    "decision_impact": (
        "Next action: Review the source artifact and adjudicate the standalone "
        "findings.\n"
        "Validation gate: A decision-ready assessment is supported by the "
        "artifact and retained findings."
    ),
    "confidence": "low",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def semantic_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Remove identity/provenance before exact byte-level semantic comparison."""
    return {
        key: copy.deepcopy(value)
        for key, value in finding.items()
        if key not in {"id", "origins"}
    }


def finding_origins(
    finding: dict[str, Any], source_id: str, source: str
) -> list[dict[str, str]]:
    inherited = finding.get("origins")
    if isinstance(inherited, list) and inherited:
        return [copy.deepcopy(origin) for origin in inherited]
    return [{"source_id": source_id, "source": source, "finding_id": finding["id"]}]


def unique_values(values: list[Any]) -> list[Any]:
    by_key = {canonical_json(value): value for value in values}
    return [by_key[key] for key in sorted(by_key)]


def source_records(
    report: dict[str, Any], source_id: str, source: str
) -> list[dict[str, Any]]:
    inherited = report.get("source_reports")
    if isinstance(inherited, list) and inherited:
        return [copy.deepcopy(record) for record in inherited]
    return [
        {
            "source_id": source_id,
            "source": source,
            "document": copy.deepcopy(report["document"]),
            "overall_assessment": copy.deepcopy(report["overall_assessment"]),
        }
    ]


def standalone_source_record(snapshot: InputSnapshot) -> dict[str, Any]:
    return {
        "source_id": snapshot.source_id,
        "source": snapshot.display_name,
        "document": copy.deepcopy(UNKNOWN_DOCUMENT),
        "overall_assessment": copy.deepcopy(UNKNOWN_ASSESSMENT),
    }


def merged_document(
    records: list[dict[str, Any]], *, unknown_metadata: bool
) -> dict[str, Any]:
    values = unique_values([record["document"] for record in records])
    if unknown_metadata or len(values) != 1:
        return copy.deepcopy(GENERIC_DOCUMENT)
    return copy.deepcopy(values[0])


def merged_assessment(
    records: list[dict[str, Any]], *, unknown_metadata: bool
) -> dict[str, Any]:
    values = unique_values([record["overall_assessment"] for record in records])
    if unknown_metadata and all(
        record["overall_assessment"] == UNKNOWN_ASSESSMENT for record in records
    ):
        return copy.deepcopy(UNKNOWN_ASSESSMENT)
    if unknown_metadata or len(values) != 1:
        return copy.deepcopy(MIXED_ASSESSMENT)
    return copy.deepcopy(values[0])


def merge(inputs: list[tuple[Any, Any]]) -> dict[str, Any]:
    buckets: dict[
        str, list[tuple[dict[str, Any], list[dict[str, str]], tuple[str, str, str]]]
    ] = {}
    materials: list[str] = []
    limitations: list[str] = []
    strengths: list[str] = []
    omissions: list[str] = []
    questions: list[str] = []
    research_performed = False
    reports: list[dict[str, Any]] = []
    unknown_metadata = False

    for value, snapshot in inputs:
        findings = value["findings"] if isinstance(value, dict) else value
        if isinstance(value, dict):
            reports.extend(
                source_records(value, snapshot.source_id, snapshot.display_name)
            )
            scope = value["scope"]
            research_performed = research_performed or scope["research_performed"]
            materials.extend(scope["materials_reviewed"])
            limitations.extend(scope["limitations"])
            strengths.extend(value["strengths"])
            omissions.extend(value["omissions"])
            questions.extend(value["open_questions"])
        else:
            unknown_metadata = True
            reports.append(standalone_source_record(snapshot))
        for finding in findings:
            semantic = semantic_finding(finding)
            key = canonical_json(semantic)
            representative = (snapshot.source_id, snapshot.display_name, finding["id"])
            buckets.setdefault(key, []).append(
                (
                    semantic,
                    finding_origins(finding, snapshot.source_id, snapshot.display_name),
                    representative,
                )
            )

    merged_findings: list[dict[str, Any]] = []
    for key in sorted(buckets):
        candidates = buckets[key]
        # This is the only representative selection: all candidates in the
        # bucket are byte-identical after identity/provenance is removed.
        semantic, _, _ = min(candidates, key=lambda candidate: candidate[2])
        origins = unique_values(
            [
                origin
                for _, candidate_origins, _ in candidates
                for origin in candidate_origins
            ]
        )
        if len(origins) > MAX_INPUT_FILES:
            raise ToolError("finding-origins-limit")
        merged = copy.deepcopy(semantic)
        merged["origins"] = origins
        merged_findings.append(merged)

    if len(merged_findings) > MAX_FINDINGS:
        raise ToolError("merged-findings-limit")
    source_reports = unique_values(reports)
    if len(source_reports) > MAX_INPUT_FILES:
        raise ToolError("source-reports-limit")
    merged_findings.sort(
        key=lambda finding: (
            -SEVERITY_ORDER[finding["severity"]],
            canonical_json(finding),
        )
    )
    for index, finding in enumerate(merged_findings, start=1):
        finding["id"] = f"F-{index:03d}"

    result: dict[str, Any] = {
        "document": merged_document(reports, unknown_metadata=unknown_metadata),
        "scope": {
            "materials_reviewed": unique_values(materials),
            "research_performed": research_performed,
            "limitations": unique_values(limitations),
        },
        "strengths": unique_values(strengths),
        "findings": merged_findings,
        "omissions": unique_values(omissions),
        "open_questions": unique_values(questions),
        "overall_assessment": merged_assessment(
            reports, unknown_metadata=unknown_metadata
        ),
        "source_reports": source_reports,
    }
    return result


def main() -> int:
    parser = SafeArgumentParser(
        description="Merge schema-valid critically-review JSON outputs."
    )
    parser.add_argument(
        "inputs", nargs="+", help="Input JSON reports or findings lists."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New output JSON path, unless --force is explicit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Use supported safe replacement for an existing output.",
    )
    add_error_format(parser)
    args = parser.parse_args()
    if len(args.inputs) > MAX_INPUT_FILES:
        raise ToolError("input-count-limit")

    parsed_inputs: list[tuple[Any, InputSnapshot]] = []
    snapshots: list[InputSnapshot] = []
    try:
        for raw_path in args.inputs:
            snapshot = snapshot_input(raw_path)
            value = read_json(snapshot)
            require_valid_payload(value, allow_findings_list=True)
            snapshots.append(snapshot)
            parsed_inputs.append((value, snapshot))

        result = merge(parsed_inputs)
        require_valid_payload(result, allow_findings_list=False)
        output = write_output(
            json_payload(result), args.output, snapshots, force=args.force
        )
        print(
            json.dumps(
                {
                    "output": safe_identifier(output),
                    "findings": len(result["findings"]),
                },
                sort_keys=True,
            )
        )
    finally:
        close_snapshots(snapshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(safe_main(main))
