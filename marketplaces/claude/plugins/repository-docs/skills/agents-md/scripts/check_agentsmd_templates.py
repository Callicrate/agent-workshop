#!/usr/bin/env python3
"""Validate agents-md Markdown templates and guidance."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SMART_PUNCTUATION = {
    "\u2014": "em dash",
    "\u2018": "left single quote",
    "\u2019": "right single quote",
    "\u201c": "left double quote",
    "\u201d": "right double quote",
}

GENERIC_PHRASES = (
    "write clean code",
    "follow best practices",
    "use type hints",
    "use f-strings",
    "handle errors properly",
)

TOO_CONCRETE_IN_TEMPLATES = (
    "acme",
    "example.com",
    "prod.",
    "dev.",
    "order_id",
    "user_id",
)

REQUIRED_FILES = (
    "SKILL.md",
    "assets/agentsmd-minimal.md",
    "assets/agentsmd-contract-bearing.md",
    "assets/agentsmd-operational.md",
    "assets/agentsmd-full.md",
    "references/manual-audit.md",
    "references/tool-contracts.md",
    "scripts/analyze_project.py",
    "scripts/validate_agentsmd.py",
    "scripts/semantic_check_agentsmd.py",
    "scripts/check_agentsmd_templates.py",
    "scripts/run_agentsmd_fixture_checks.py",
    "tests/fixtures/minimal-python/AGENTS.md",
    "tests/fixtures/contract-bearing/AGENTS.md",
    "tests/fixtures/operational/AGENTS.md",
    "tests/fixtures/bad-placeholders/AGENTS.md",
    "tests/fixtures/bad-stale-path/AGENTS.md",
    "tests/fixtures/bad-unsafe-command/AGENTS.md",
    "tests/fixtures/good-maintainer-only-command/AGENTS.md",
    "tests/fixtures/bad-broken-link/AGENTS.md",
    "tests/fixtures/dynamic-paths/AGENTS.md",
    "tests/fixtures/bad-nested-conflict/AGENTS.md",
)

LEGACY_HEADING_TEXT = (
    "## Tool And Workflow Contracts",
    "## Coordination And Evidence",
    "## Local Workflow",
    "## Project Contracts",
)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str


def iter_markdown_files(skill_dir: Path) -> list[Path]:
    return sorted(skill_dir.rglob("*.md"))


def check_smart_punctuation(path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        for character, label in SMART_PUNCTUATION.items():
            if character in line:
                violations.append(
                    Violation(
                        path, line_number, f"Replace {label} with ASCII punctuation"
                    )
                )

    return violations


def check_fences(path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    stack: list[tuple[str, int, int]] = []
    fence_pattern = re.compile(r"^\s*(`{3,}|~{3,})")

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = fence_pattern.match(line)
        if match is None:
            continue

        fence = match.group(1)
        marker = fence[0]
        length = len(fence)
        if stack and stack[-1][0] == marker and length >= stack[-1][1]:
            stack.pop()
        else:
            stack.append((marker, length, line_number))

    for marker, length, line_number in stack:
        violations.append(
            Violation(
                path,
                line_number,
                f"Unclosed Markdown fence starting with {marker * length}",
            )
        )

    return violations


def check_template_text(path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    if not path.name.startswith("agentsmd-"):
        return violations

    lowercase_text = text.lower()
    for phrase in GENERIC_PHRASES:
        if phrase in lowercase_text:
            line_number = lowercase_text[: lowercase_text.index(phrase)].count("\n") + 1
            violations.append(
                Violation(path, line_number, f"Remove generic phrase: {phrase!r}")
            )

    for phrase in TOO_CONCRETE_IN_TEMPLATES:
        if phrase in lowercase_text:
            line_number = lowercase_text[: lowercase_text.index(phrase)].count("\n") + 1
            violations.append(
                Violation(
                    path,
                    line_number,
                    f"Use a placeholder instead of concrete example: {phrase!r}",
                )
            )

    return violations


def check_required_files(skill_dir: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relative_path in REQUIRED_FILES:
        path = skill_dir / relative_path
        if not path.exists():
            violations.append(
                Violation(path, 1, "Required agents-md skill file is missing")
            )
    return violations


def check_legacy_headings(path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for heading in LEGACY_HEADING_TEXT:
        if heading in text:
            line_number = text[: text.index(heading)].count("\n") + 1
            violations.append(
                Violation(path, line_number, f"Replace legacy heading: {heading}")
            )
    return violations


def validate(skill_dir: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_required_files(skill_dir))

    for path in iter_markdown_files(skill_dir):
        text = path.read_text(encoding="utf-8")
        violations.extend(check_smart_punctuation(path, text))
        violations.extend(check_fences(path, text))
        violations.extend(check_template_text(path, text))
        violations.extend(check_legacy_headings(path, text))

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate agents-md template hygiene.")
    parser.add_argument(
        "skill_dir",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Path to the agents-md skill directory",
    )
    args = parser.parse_args()

    skill_dir = args.skill_dir.resolve()
    violations = validate(skill_dir)
    if violations:
        for violation in violations:
            relative_path = violation.path.relative_to(skill_dir)
            print(f"{relative_path}:{violation.line}: {violation.message}")
        return 1

    print("AGENTS.md template hygiene check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
