#!/usr/bin/env python3
"""Validate an AGENTS.md file produced for a target repository."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from bounded_io import (
    ArgumentParseError,
    InputFileError,
    ValueFreeArgumentParser,
    command_line_error,
    error_envelope,
    preflight_repo_files,
    read_repo_text,
    resolve_repo_root,
)

SMART_PUNCTUATION = {
    "\u2014": "em dash",
    "\u2018": "left single quote",
    "\u2019": "right single quote",
    "\u201c": "left double quote",
    "\u201d": "right double quote",
}

GENERIC_PHRASES = (
    "add tests when appropriate",
    "write clean code",
    "follow best practices",
    "use type hints",
    "use f-strings",
    "handle errors properly",
    "use meaningful names",
)

PLACEHOLDER_PATTERNS = (
    "[Project Name]",
    "[entire repository or scoped subdirectory]",
    "[one sentence]",
    "[one-sentence project purpose]",
    "[language/runtime/framework versions observed in config]",
    "[language/runtime/framework versions from config]",
    "[commands, services, notebooks, packages, or apps agents should start from]",
    "[path]",
    "[install command]",
    "[install or sync command]",
    "[test command]",
    "[lint or format command]",
    "[run or build command]",
    "[verified convention or contract that is specific to this repository.]",
    "[small project-specific good example]",
    "[matching bad example that agents have actually been tempted to write]",
    "[good project-specific pattern]",
    "[matching bad pattern or common ai mistake]",
    "[doc name]",
)

PLACEHOLDER_WORDS = (
    "todo",
    "tbd",
    "fixme",
    "replace me",
    "replace-me",
    "placeholder",
    "project name",
    "project-name",
    "repo root",
    "repo-root",
    "target repo",
    "target-repo",
    "agents file",
    "agents-file",
    "path",
    "command",
    "framework",
    "language",
    "tool",
    "package manager",
    "package-manager",
    "description",
    "example",
)

PLACEHOLDER_TOKENS = (
    "project name",
    "entire repository",
    "path",
    "framework",
    "command",
    "tool/package manager",
    "representative",
    "one-sentence",
    "source files",
    "config",
    "fixture",
    "risk area",
    "repo convention",
    "small project-specific",
    "matching bad",
    "doc name",
)

TEMPLATE_COMMENT_PATTERN = re.compile(
    r"<!--.*?(?:template|placeholder|todo|tbd|fixme|replace).*?-->", re.IGNORECASE
)
ANGLE_PLACEHOLDER_PATTERN = re.compile(r"<([a-z][a-z0-9 _./:-]{1,80})>", re.IGNORECASE)
BRACE_PLACEHOLDER_PATTERN = re.compile(
    r"(\{\{[^{}\n]{2,80}\}\}|\{[A-Z][A-Z0-9_ -]{2,80}\}|__[A-Z][A-Z0-9_ -]{2,80}__)"
)

CONTRACT_HEADINGS = {
    "context",
    "workspace contract",
    "project architecture",
    "project structure",
    "repository map",
    "workspace structure",
    "local workflow",
    "local commands",
    "project rules",
    "project contracts",
    "tool and workflow contracts",
    "coordination and evidence",
    "key file contracts",
    "execution patterns",
    "do / don't",
    "do/don't",
}

CANONICAL_HEADING_ORDER = {
    "scope": 1,
    "context": 2,
    "repository map": 3,
    "project structure": 3,
    "workspace structure": 3,
    "local commands": 4,
    "local workflow": 4,
    "project rules": 5,
    "testing": 6,
    "tool and workflow contracts": 7,
    "project contracts": 7,
    "coordination and evidence": 8,
    "style conventions": 9,
    "domain terms": 10,
    "do / don't": 11,
    "do/don't": 11,
    "related docs": 12,
}

RULE_HEADINGS = {
    "project rules",
    "project contracts",
}

FORBIDDEN_EVIDENCE_HEADINGS = {
    "evidence log",
    "verification evidence",
    "source evidence",
}

SAMPLE_MARKERS = (
    "sampled path",
    "sampled source",
    "source sample",
    "representative sample",
)

REQUIRED_EVIDENCE_HEADINGS = {
    "analyzer report use",
    "sampled paths",
    "rule sources",
    "live contract inventory",
    "directory topology evidence",
    "handoff ownership map",
    "stale reference search",
    "invalidated or superseded guidance",
    "validation commands and results",
}

TRACEABLE_REFERENCE_PATTERN = re.compile(r"`[^`]+`|\[[^\]]+\]\([^)]+\)")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
COMMAND_LANGUAGES = {"bash", "console", "powershell", "ps1", "sh", "shell", "zsh"}

UNSAFE_COMMAND_PATTERNS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brm\s+-fr\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[a-z]*[fxd][a-z]*\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+compose\s+down\b.*\s-v\b", re.IGNORECASE),
    re.compile(r"\baws\s+s3\s+rm\b.*\s--recursive\b", re.IGNORECASE),
    re.compile(r"\bterraform\s+(apply|destroy)\b", re.IGNORECASE),
    re.compile(r"\bkubectl\s+delete\b", re.IGNORECASE),
    re.compile(r"\b(drop|truncate)\s+(database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\bdatabricks\s+bundle\s+deploy\b", re.IGNORECASE),
    re.compile(r"\bcredential\b", re.IGNORECASE),
    re.compile(r"\bdeploy\b", re.IGNORECASE),
    re.compile(r"\bdestroy\b", re.IGNORECASE),
    re.compile(r"\blive[-\s]?target\b", re.IGNORECASE),
    re.compile(r"\bmigrate\b", re.IGNORECASE),
    re.compile(r"\bprod(?:uction)?\b", re.IGNORECASE),
    re.compile(r"\breplay\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
)

SAFE_COMMAND_CONTEXT = (
    "maintainer-only",
    "inspected-only",
    "do not run",
    "do not execute",
    "dry-run",
    "requires explicit user",
    "unless the user explicitly asks",
    "explicitly asks to run",
    "explicitly safe local fixture",
    "safe local fixture",
)

CREATE_REQUIRED_HEADINGS = {
    "scope": "Scope",
    "context": "Context",
    "project rules": "Project Rules",
}

LENGTH_BUDGETS = {
    ("quick", "create"): 250,
    ("standard", "create"): 350,
    ("exhaustive", "create"): 500,
    ("quick", "update"): 400,
    ("standard", "update"): 600,
    ("exhaustive", "update"): 800,
    ("quick", "review"): 300,
    ("standard", "review"): 450,
    ("exhaustive", "review"): 650,
    ("quick", "split"): 300,
    ("standard", "split"): 450,
    ("exhaustive", "split"): 650,
    ("quick", "move"): 300,
    ("standard", "move"): 450,
    ("exhaustive", "move"): 650,
}


@dataclass(frozen=True)
class Violation:
    line: int
    message: str
    code: str = "validation-error"
    severity: str = "error"


@dataclass(frozen=True)
class CodeBlock:
    language: str
    start_line: int
    code: str


def check_smart_punctuation(text: str) -> list[Violation]:
    violations: list[Violation] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        for character, label in SMART_PUNCTUATION.items():
            if character in line:
                violations.append(
                    Violation(line_number, f"Replace {label} with ASCII punctuation")
                )

    return violations


def check_fences(text: str) -> list[Violation]:
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
                line_number, f"Unclosed Markdown fence starting with {marker * length}"
            )
        )

    return violations


def check_headings(text: str, *, mode: str, intent: str) -> list[Violation]:
    violations: list[Violation] = []
    h1_count = 0
    previous_level = 0
    contract_heading_seen = False
    last_known_order = 0
    headings_seen: set[str] = set()

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is None:
            continue

        level = len(match.group(1))
        heading = match.group(2).strip().lower()
        headings_seen.add(heading)
        if level == 1:
            h1_count += 1
        if previous_level and level > previous_level + 1:
            violations.append(
                Violation(line_number, "Do not skip Markdown heading levels")
            )
        previous_level = level
        if heading in CONTRACT_HEADINGS:
            contract_heading_seen = True
        if heading in CANONICAL_HEADING_ORDER:
            heading_order = CANONICAL_HEADING_ORDER[heading]
            if heading_order < last_known_order:
                violations.append(
                    Violation(
                        line_number,
                        "Known AGENTS.md sections should preserve the canonical relative order",
                        code="heading-order",
                    )
                )
            else:
                last_known_order = heading_order
        if heading in FORBIDDEN_EVIDENCE_HEADINGS:
            violations.append(
                Violation(
                    line_number,
                    "Move evidence logs to working notes or a repo-owned evidence file",
                    code="repo-evidence-section",
                )
            )

    if h1_count != 1:
        violations.append(
            Violation(1, "AGENTS.md must contain exactly one H1 title", code="h1-count")
        )
    if intent == "create" and mode == "standard":
        missing_headings = [
            label
            for key, label in CREATE_REQUIRED_HEADINGS.items()
            if key not in headings_seen
        ]
        for heading in missing_headings:
            violations.append(
                Violation(
                    1,
                    f"Create/standard validation requires a {heading} section",
                    code="missing-required-section",
                )
            )
    elif mode != "quick" and not contract_heading_seen:
        violations.append(
            Violation(
                1,
                "Add at least one core guidance section such as Context or Project Rules",
                code="missing-core-section",
            )
        )

    return violations


def iter_code_blocks(text: str) -> list[CodeBlock]:
    blocks: list[CodeBlock] = []
    in_fence = False
    fence_marker = ""
    language = ""
    start_line = 0
    lines: list[str] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = FENCE_PATTERN.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
                language = fence_match.group(2).lower()
                start_line = line_number + 1
                lines = []
            elif marker == fence_marker:
                blocks.append(CodeBlock(language, start_line, "\n".join(lines)))
                in_fence = False
                fence_marker = ""
                language = ""
                start_line = 0
                lines = []
            continue
        if in_fence:
            lines.append(line)

    return blocks


def collect_section_bullets(text: str, target_headings: set[str]) -> list[str]:
    bullets: list[str] = []
    lines = text.splitlines()
    active_level = 0

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is not None:
            heading = match.group(2).strip().lower()
            level = len(match.group(1))
            active_level = level if heading in target_headings else 0
            continue

        if active_level and line.strip().startswith("- "):
            bullets.append(line)

    return bullets


def check_sidecar_evidence(
    evidence_text_raw: str, agentsmd_text: str
) -> list[Violation]:
    evidence_text = evidence_text_raw.lower()
    violations: list[Violation] = []

    headings = {
        match.group(2).strip().lower()
        for match in re.finditer(
            r"^(#{2,6})\s+(.+?)\s*$", evidence_text_raw, flags=re.MULTILINE
        )
    }
    missing_headings = sorted(REQUIRED_EVIDENCE_HEADINGS - headings)
    for heading in missing_headings:
        violations.append(
            Violation(1, f"Evidence file is missing required section: {heading}")
        )

    evidence_items = [
        line for line in evidence_text_raw.splitlines() if line.strip().startswith("- ")
    ]
    traceable_items = [
        line for line in evidence_items if TRACEABLE_REFERENCE_PATTERN.search(line)
    ]
    if not traceable_items:
        violations.append(
            Violation(
                1,
                "Evidence file must include at least one bullet with a file path, command, or linked source",
            )
        )

    rule_items = collect_section_bullets(agentsmd_text, RULE_HEADINGS)
    rule_source_items = [
        line
        for line in traceable_items
        if "source" in line.lower() or "supports" in line.lower()
    ]
    if len(rule_source_items) < len(rule_items):
        violations.append(
            Violation(
                1,
                "Evidence file must include at least one traceable source item per Project Rules or Project Contracts bullet",
            )
        )

    if not any(marker in evidence_text for marker in SAMPLE_MARKERS):
        violations.append(
            Violation(
                1,
                "Evidence file must document sampled paths or representative source selection",
            )
        )

    return violations


def check_placeholder_and_generic_text(text: str) -> list[Violation]:
    violations: list[Violation] = []
    lowercase_text = text.lower()

    for phrase in GENERIC_PHRASES:
        if phrase in lowercase_text:
            line_number = lowercase_text[: lowercase_text.index(phrase)].count("\n") + 1
            violations.append(
                Violation(
                    line_number,
                    f"Remove generic phrase: {phrase!r}",
                    code="generic-filler",
                )
            )

    for placeholder in PLACEHOLDER_PATTERNS:
        placeholder_lower = placeholder.lower()
        if placeholder_lower in lowercase_text:
            line_number = (
                lowercase_text[: lowercase_text.index(placeholder_lower)].count("\n")
                + 1
            )
            violations.append(
                Violation(
                    line_number,
                    f"Replace unresolved placeholder: {placeholder}",
                    code="placeholder",
                )
            )

    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = FENCE_PATTERN.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue

        line_lower = line.lower()
        if TEMPLATE_COMMENT_PATTERN.search(line):
            violations.append(
                Violation(
                    line_number,
                    "Remove template or placeholder comment",
                    code="template-comment",
                )
            )

        for match in re.finditer(r"\b(TODO|TBD|FIXME)\b", line, flags=re.IGNORECASE):
            violations.append(
                Violation(
                    line_number,
                    f"Resolve unfinished marker: {match.group(1)}",
                    code="placeholder",
                )
            )

        for match in re.finditer(r"\[([^\]\n]+)\]", line):
            end_index = match.end()
            if end_index < len(line) and line[end_index] == "(":
                continue
            label = match.group(1).lower()
            if any(token in label for token in PLACEHOLDER_TOKENS) or any(
                word in label for word in PLACEHOLDER_WORDS
            ):
                violations.append(
                    Violation(
                        line_number,
                        f"Replace unresolved bracket placeholder: [{match.group(1)}]",
                        code="placeholder",
                    )
                )

        for match in ANGLE_PLACEHOLDER_PATTERN.finditer(line):
            label = match.group(1).strip().lower()
            if any(word in label for word in PLACEHOLDER_WORDS):
                violations.append(
                    Violation(
                        line_number,
                        f"Replace unresolved angle-bracket placeholder: <{match.group(1)}>",
                        code="placeholder",
                    )
                )

        for match in BRACE_PLACEHOLDER_PATTERN.finditer(line):
            label = match.group(1).strip("{}_ ").lower().replace("_", " ")
            if any(word in label for word in PLACEHOLDER_WORDS):
                violations.append(
                    Violation(
                        line_number,
                        f"Replace unresolved placeholder variant: {match.group(1)}",
                        code="placeholder",
                    )
                )

        if "template" in line_lower and "comment" in line_lower:
            violations.append(
                Violation(
                    line_number,
                    "Remove template comment text",
                    code="template-comment",
                )
            )

    return violations


def check_length_budget(text: str, *, mode: str, intent: str) -> list[Violation]:
    """Warn when an AGENTS.md is likely too long for the requested workflow."""
    budget = LENGTH_BUDGETS.get((mode, intent), LENGTH_BUDGETS[(mode, "update")])
    line_count = len(text.splitlines())
    if line_count <= budget:
        return []
    return [
        Violation(
            1,
            f"AGENTS.md has {line_count} lines; this is probably too long for {intent}/{mode} work (budget {budget})",
            code="length-budget",
            severity="warning",
        )
    ]


def check_related_docs(text: str) -> list[Violation]:
    violations: list[Violation] = []
    active = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is not None:
            active = match.group(2).strip().lower() == "related docs"
            continue
        if not active or not line.strip().startswith("- "):
            continue

        has_path = bool(TRACEABLE_REFERENCE_PATTERN.search(line))
        has_reason = " - " in line and bool(line.split(" - ", 1)[1].strip())
        if not has_path or not has_reason:
            violations.append(
                Violation(
                    line_number,
                    "Related Docs entries must include a relative path plus when or why agents should read it",
                    code="related-docs-entry",
                )
            )

    return violations


def check_unsafe_commands(text: str) -> list[Violation]:
    violations: list[Violation] = []
    all_lines = text.splitlines()

    for block in iter_code_blocks(text):
        if block.language not in COMMAND_LANGUAGES:
            continue
        for offset, line in enumerate(block.code.splitlines()):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            command_line = block.start_line + offset
            context_start = max(0, command_line - 4)
            context_end = min(len(all_lines), command_line + 2)
            context = "\n".join(all_lines[context_start:context_end]).lower()
            if any(marker in context for marker in SAFE_COMMAND_CONTEXT):
                continue
            for pattern in UNSAFE_COMMAND_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        Violation(
                            command_line,
                            "Mark unsafe, destructive, production, migration, or deploy commands as maintainer-only or inspected-only",
                            code="unsafe-command",
                        )
                    )
                    break

    return violations


def validate_text(text: str, *, mode: str, intent: str = "update") -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_smart_punctuation(text))
    violations.extend(check_fences(text))
    violations.extend(check_headings(text, mode=mode, intent=intent))
    violations.extend(check_placeholder_and_generic_text(text))
    violations.extend(check_length_budget(text, mode=mode, intent=intent))
    if mode in {"standard", "exhaustive"}:
        violations.extend(check_related_docs(text))
        violations.extend(check_unsafe_commands(text))
    return violations


def validate(path: Path, *, mode: str, intent: str = "update") -> list[Violation]:
    """Validate an already-trusted local file for Python callers."""
    return validate_text(
        path.read_text(encoding="utf-8", errors="strict"), mode=mode, intent=intent
    )


def validate_evidence_text(text: str, agentsmd_text: str) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_smart_punctuation(text))
    violations.extend(check_fences(text))
    violations.extend(check_sidecar_evidence(text, agentsmd_text))
    return violations


def validate_evidence(path: Path, agentsmd_text: str) -> list[Violation]:
    """Validate an already-trusted local evidence file for Python callers."""
    return validate_evidence_text(
        path.read_text(encoding="utf-8", errors="strict"), agentsmd_text
    )


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    parser = ValueFreeArgumentParser(
        description="Validate a target repository AGENTS.md file."
    )
    parser.add_argument(
        "agentsmd", nargs="?", type=Path, help="Path to the AGENTS.md file to validate"
    )
    parser.add_argument(
        "--agents-file", type=Path, help="Path to the AGENTS.md file to validate"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Target repository root. Accepted for a stable tool contract; structural validation does not require it.",
    )
    parser.add_argument(
        "--mode",
        choices=("quick", "standard", "exhaustive"),
        default="standard",
        help="Validation depth.",
    )
    parser.add_argument(
        "--intent",
        choices=("create", "update", "review", "split", "move"),
        default="update",
        help="Validation intent. Default preserves backward-compatible update-style structure checks.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Optional repo-owned evidence file to validate alongside AGENTS.md when one is explicitly required.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON findings."
    )
    json_requested = "--json" in sys.argv[1:]
    try:
        args = parser.parse_args()
    except ArgumentParseError:
        error = command_line_error()
        if json_requested:
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2

    if args.agentsmd is not None and args.agents_file is not None:
        error = command_line_error()
        if args.json:
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
    agentsmd_arg = args.agents_file if args.agents_file is not None else args.agentsmd
    if agentsmd_arg is None:
        error = command_line_error()
        if args.json:
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2

    try:
        repo_root = resolve_repo_root(
            args.repo_root
            if args.repo_root is not None
            else agentsmd_arg.absolute().parent
        )
        requested = [("agents-file", agentsmd_arg)]
        if args.evidence is not None:
            requested.append(("evidence", args.evidence))
        prepared = preflight_repo_files(repo_root, requested)
        agentsmd_path, agentsmd_text, _ = read_repo_text(
            repo_root, prepared["agents-file"], label="agents-file"
        )
        evidence_path = prepared.get("evidence")
        evidence_text: str | None = None
        if evidence_path is not None:
            evidence_path, evidence_text, _ = read_repo_text(
                repo_root, evidence_path, label="evidence"
            )
    except InputFileError as exc:
        if args.json:
            print(json.dumps(error_envelope(exc), indent=2))
        else:
            print(f"Error: {exc.message}", file=sys.stderr)
        return 2

    violations = [
        (agentsmd_path, violation)
        for violation in validate_text(
            agentsmd_text, mode=args.mode, intent=args.intent
        )
    ]

    if evidence_path is not None and evidence_text is not None:
        violations.extend(
            (evidence_path, violation)
            for violation in validate_evidence_text(evidence_text, agentsmd_text)
        )

    if violations:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "fail",
                        "findings": [
                            {
                                "severity": violation.severity,
                                "code": violation.code,
                                "path": display_path(path, repo_root),
                                "line": violation.line,
                                "message": violation.message,
                            }
                            for path, violation in violations
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for path, violation in violations:
                print(
                    f"{display_path(path, repo_root)}:{violation.line}: {violation.message}"
                )
        return 1

    if args.json:
        print(json.dumps({"status": "pass", "findings": []}, indent=2))
    else:
        print(f"AGENTS.md validation passed: {display_path(agentsmd_path, repo_root)}")
        if evidence_path is not None:
            print(
                f"Evidence validation passed: {display_path(evidence_path, repo_root)}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
