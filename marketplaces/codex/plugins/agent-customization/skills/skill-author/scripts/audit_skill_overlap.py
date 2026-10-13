"""Audit skill descriptions for overlap and missing trigger coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from inventory_skills import (
    discovery_contract_issues,
    inventory,
    positive_description,
    resolve_skills_root,
    SKILLS_ROOT_UNAVAILABLE_ERROR,
)

STOP_WORDS = {
    "and",
    "are",
    "asked",
    "for",
    "from",
    "into",
    "keyword",
    "keywords",
    "the",
    "this",
    "use",
    "when",
    "with",
    "you",
}


def tokens(text: str) -> set[str]:
    """Return normalized descriptive tokens."""
    return {token for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", text.casefold()) if token not in STOP_WORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    """Return token overlap score."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def audit(data: dict[str, Any], threshold: float) -> dict[str, Any]:
    """Return overlap and coverage findings for an inventory."""
    skills = data.get("skills", [])
    token_map = {
        skill["name"]: tokens(positive_description(description)) if isinstance(description := skill.get("description"), str) else set()
        for skill in skills
    }
    overlaps = []
    for index, left in enumerate(skills):
        for right in skills[index + 1 :]:
            left_tokens = token_map[left["name"]]
            right_tokens = token_map[right["name"]]
            score = jaccard(left_tokens, right_tokens)
            shared = sorted(left_tokens & right_tokens)
            if score >= threshold or len(shared) >= 8:
                overlaps.append(
                    {
                        "left": left["name"],
                        "right": right["name"],
                        "score": round(score, 3),
                        "shared_terms": shared[:20],
                    }
                )

    coverage = []
    for skill in skills:
        discovery_fields = {
            "description": skill.get("description"),
            "metadata.short-description": skill.get("short_description"),
        }
        coverage.extend(
            {"skill": skill["name"], "issue": issue}
            for issue in discovery_contract_issues(discovery_fields)
        )
        diagnostics = skill.get("frontmatter_diagnostics", [])
        if isinstance(diagnostics, list):
            coverage.extend(
                {"skill": skill["name"], "issue": f"invalid frontmatter: {diagnostic}"}
                for diagnostic in diagnostics
                if isinstance(diagnostic, str)
            )
        if skill.get("line_count", 0) > 150 and any(skill.get("resource_counts", {}).values()):
            coverage.append({"skill": skill["name"], "issue": "SKILL.md may be heavy for a skill with resources"})

    discovery_budget = data.get("discovery_budget")
    advisory_findings = []
    if isinstance(discovery_budget, dict):
        exact_totals = discovery_budget.get("exact_totals")
        threshold_comparison = discovery_budget.get("threshold_comparison")
        if isinstance(exact_totals, dict) and isinstance(threshold_comparison, dict):
            relative_total = exact_totals.get("relative_path_model_total")
            absolute_total = exact_totals.get("absolute_path_model_total")
            relative_comparison = threshold_comparison.get("relative_path_model")
            absolute_comparison = threshold_comparison.get("absolute_path_model")
            if (
                isinstance(relative_total, int)
                and isinstance(absolute_total, int)
                and isinstance(relative_comparison, dict)
                and isinstance(absolute_comparison, dict)
            ):
                relative_result = relative_comparison.get("result")
                absolute_result = absolute_comparison.get("result")
                if isinstance(relative_result, str) and isinstance(absolute_result, str):
                    advisory_findings.append(
                        {
                            "type": "discovery_budget",
                            "status": "advisory",
                            "message": (
                                "Modeled initial discovery-list field values: "
                                f"{relative_total} characters with relative paths ({relative_result}); "
                                f"{absolute_total} characters with absolute paths ({absolute_result}). "
                                "This nonblocking advisory is not proof that this host omitted a skill."
                            ),
                        }
                    )

    return {
        "skills_root": data.get("skills_root"),
        "skill_count": data.get("skill_count", 0),
        "overlap_threshold": threshold,
        "overlaps": overlaps,
        "coverage_findings": coverage,
        "discovery_budget": discovery_budget,
        "advisory_findings": advisory_findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit shared skills for description overlap and trigger coverage.")
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Directory containing skill folders (defaults to the packaged canonical root).",
    )
    parser.add_argument("--inventory", help="Optional existing inventory JSON from inventory_skills.py.")
    parser.add_argument("--threshold", type=float, default=0.22, help="Jaccard threshold for overlap findings.")
    parser.add_argument("--output", help="Optional JSON output path. Prints to stdout when omitted.")
    args = parser.parse_args()

    if args.inventory:
        data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    else:
        try:
            data = inventory(resolve_skills_root(args.skills_root))
        except (OSError, ValueError):
            print(f"error: {SKILLS_ROOT_UNAVAILABLE_ERROR}", file=sys.stderr)
            return 2
    result = audit(data, args.threshold)
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
