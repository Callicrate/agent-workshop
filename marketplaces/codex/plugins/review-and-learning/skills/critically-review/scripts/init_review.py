#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Scaffold a review workspace through the shared safe output primitive."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from review_io import (
    SafeArgumentParser,
    ToolError,
    add_error_format,
    json_payload,
    safe_identifier,
    safe_main,
    secure_directory,
    write_output,
)

TEMPLATE_FILES = {
    "review-plan.md": "assets/review-plan-template.md",
    "todo.md": "assets/todo-template.md",
    "claim-ledger.csv": "assets/claim-ledger-template.csv",
    "evidence-log.csv": "assets/evidence-log-template.csv",
    "findings.json": "assets/findings-template.json",
    "report.md": "assets/review-report-template.md",
}
REVIEW_MODES = [
    "triage",
    "standard",
    "exhaustive",
    "technical-deep-dive",
    "first-look",
    "delta-review",
    "implementation-readiness",
    "public-surface-review",
    "evidence-slice-review",
]


def _template_text(root: Path, source: str, *, title: str, review_mode: str) -> str:
    text = (root / source).read_text(encoding="utf-8")
    if source != "assets/findings-template.json":
        return text
    findings = json.loads(text)
    findings["document"]["title"] = title
    findings["document"]["review_mode"] = review_mode
    return json_payload(findings)


def main() -> int:
    parser = SafeArgumentParser(
        description="Scaffold a safely bounded critically-review workspace."
    )
    parser.add_argument(
        "--output", required=True, help="Directory to create or populate."
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional document title to prefill in findings.json metadata.",
    )
    parser.add_argument(
        "--review-mode",
        default="standard",
        choices=REVIEW_MODES,
        help="Initial review mode.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Use supported safe replacement for existing output files.",
    )
    add_error_format(parser)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    lease = secure_directory(args.output)
    created: list[str] = []
    skipped: list[str] = []
    findings_skipped = False
    try:
        for target_name, source_rel in TEMPLATE_FILES.items():
            content = _template_text(
                root, source_rel, title=args.title, review_mode=args.review_mode
            )
            try:
                write_output(
                    content,
                    str(lease.path / target_name),
                    [],
                    force=args.force,
                    parent_lease=lease,
                )
            except ToolError as exc:
                if exc.code != "output-exists":
                    raise
                skipped.append(target_name)
                findings_skipped = findings_skipped or target_name == "findings.json"
            else:
                created.append(target_name)

        metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "skill": "critically-review",
            "review_mode": args.review_mode,
            "files_created": sorted(created),
        }
        try:
            write_output(
                json_payload(metadata),
                str(lease.path / "workspace-metadata.json"),
                [],
                force=args.force,
                parent_lease=lease,
            )
        except ToolError as exc:
            if exc.code != "output-exists":
                raise
            skipped.append("workspace-metadata.json")
        else:
            created.append("workspace-metadata.json")

        if findings_skipped and (args.title or args.review_mode != "standard"):
            print(
                "WARNING: title and review mode were not applied because findings.json exists.",
                file=sys.stderr,
            )
        print(
            json.dumps(
                {
                    "workspace": safe_identifier(lease.path),
                    "created": sorted(created),
                    "skipped": sorted(skipped),
                },
                sort_keys=True,
            )
        )
    finally:
        lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(safe_main(main))
