#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Audit prompt files for the required structural contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SectionRequirement:
    """One required section in a prompt contract."""

    name: str
    aliases: tuple[str, ...]
    guidance: str


@dataclass(frozen=True)
class Issue:
    """One audit result."""

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class KindResult:
    """Detected prompt kind with confidence signal."""

    kind: str
    confidence: str  # "high", "medium", or "low"


CONTRACTS: dict[str, tuple[SectionRequirement, ...]] = {
    "system": (
        SectionRequirement("identity", ("identity", "who you are"), "State the agent name, job, or audience."),
        SectionRequirement(
            "environment",
            ("environment", "tools", "channels", "runtime"),
            "State tools, IO surfaces, channels, or workspace assumptions.",
        ),
        SectionRequirement(
            "security",
            ("security", "guardrails", "constraints", "must not", "never"),
            "State explicit prohibitions and confirmation gates.",
        ),
        SectionRequirement(
            "role",
            ("role", "scope", "boundaries", "authority"),
            "State what the agent owns and what it does not own.",
        ),
        SectionRequirement(
            "instructions",
            ("instructions", "workflow", "process", "steps"),
            "State the ordered working method, retries, or validation flow.",
        ),
        SectionRequirement(
            "memory",
            ("memory", "context", "load"),
            "State which context or memory sources to load and when.",
        ),
    ),
    "role": (
        SectionRequirement("identity", ("identity", "name", "audience"), "State the role name, domain, or audience."),
        SectionRequirement(
            "capabilities",
            ("capabilities", "may do", "allowed actions"),
            "State what the role may do autonomously.",
        ),
        SectionRequirement(
            "boundaries",
            ("boundaries", "out of scope", "must not", "requires confirmation"),
            "State hard limits and confirmation requirements.",
        ),
        SectionRequirement(
            "inputs and outputs",
            ("inputs and outputs", "inputs", "outputs", "deliverables"),
            "State accepted inputs, required outputs, and the done condition.",
        ),
        SectionRequirement(
            "escalation",
            ("escalation", "blockers", "ask when"),
            "State when the role must stop and escalate.",
        ),
    ),
    "multi-agent": (
        SectionRequirement(
            "participants",
            ("participants", "agents", "roles"),
            "List the participating agents and their responsibilities.",
        ),
        SectionRequirement(
            "shared artifacts",
            ("shared artifacts", "artifacts", "source of truth"),
            "State which artifacts are shared and which copy is authoritative.",
        ),
        SectionRequirement(
            "handoffs",
            ("handoffs", "handoff", "acceptance criteria"),
            "State how work transfers between agents and how completion is accepted.",
        ),
        SectionRequirement(
            "conflict resolution",
            ("conflict resolution", "tie-breaker", "escalate after"),
            "State how disagreements are resolved.",
        ),
        SectionRequirement(
            "completion",
            ("completion", "final owner", "final deliverable"),
            "State the final owner, deliverable, and validation step.",
        ),
    ),
}

VAGUE_PHRASES: dict[str, str] = {
    "helpful assistant": "Replace with a job-specific identity.",
    "do your best": "Replace with a concrete output or workflow rule.",
    "be careful": "Replace with an explicit prohibition or confirmation rule.",
    "professional and friendly": "Keep only if tone is a real product requirement.",
    "think carefully": "Replace with a validation or retry instruction.",
    "use good judgment": "Name the decision rule or escalation condition.",
    "respond appropriately": "State the expected response shape for each condition.",
    "consider the context": "Name the exact context source to inspect.",
    "handle edge cases": "List the edge cases or add a test requirement.",
    "maintain quality": "Define the acceptance check that proves quality.",
    "ensure quality": "Define the acceptance check that proves quality.",
    "when appropriate": "Replace with a concrete trigger condition.",
    "as needed": "Replace with a concrete trigger condition.",
    "follow best practices": "Name the specific practice or local standard.",
    "be robust": "Name the retry, fallback, or validation behavior.",
}

OBJECTIVE_FIELD_NAMES = (
    "primary objective",
    "primary job",
    "primary responsibility",
    "objective",
    "purpose",
    "job",
    "sole responsibility",
)
OBJECTIVE_LABEL_RE = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*(?:\*\*|__)?"
    r"(?P<label>primary[ \t]+(?:objective|job|responsibility)|objective|purpose|job|"
    r"sole[ \t]+responsibility)"
    r"(?:\*\*|__)?[ \t]*:[ \t]*(?P<value>.*)$",
    flags=re.IGNORECASE,
)
OBJECTIVE_STATEMENT_RE = re.compile(
    r"\b(?:primary[ \t]+(?:objective|job|responsibility)|sole[ \t]+responsibility)\b[ \t]*"
    r"(?:is\b|shall[ \t]+be\b|will[ \t]+be\b)[ \t]+\S",
    flags=re.IGNORECASE,
)
SOLE_RESPONSIBILITY_STATEMENT_RE = re.compile(
    r"\bsolely[ \t]+responsible\b[ \t]+(?:for|to)[ \t]+\S",
    flags=re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.*?)[ \t]*#*[ \t]*$")

PROMPT_LINE_WARNING_THRESHOLD = 220

RUNTIME_TRIGGER_TERMS = (
    "helper agent",
    "mcp",
    "queue",
    "worker",
    "workers",
    "async",
    "poll",
    "polling",
    "workflow",
)

RUNTIME_FIELDS: dict[str, tuple[str, ...]] = {
    "caller": ("caller", "who calls", "invoked by", "dispatcher"),
    "trigger": ("trigger", "when to call", "entry condition", "starts when"),
    "required_inputs": ("required input", "required inputs", "must include", "input starts"),
    "optional_inputs": ("optional input", "optional inputs", "optional artifact", "fallback input"),
    "durable_state_location": ("durable state", "state location", "output lands", "artifact", "manifest"),
    "sync_async_behavior": ("sync", "async", "wait", "poll", "resume later"),
    "result_retrieval": ("result retrieval", "retrieve", "output file", "result artifact", "where output"),
    "failure_modes": ("failure mode", "timeout", "missing tool", "partial output", "stale state"),
    "retry_policy": ("retry", "repair", "escalate", "stop condition"),
    "caller_next_action": ("next action", "after success", "after failure", "caller does"),
}

INPUT_CLAIM_RE = re.compile(
    r"(?:must include|required inputs?|the user message must include)\s*:?(?P<tail>[^\n.]+)",
    flags=re.IGNORECASE,
)

PATHISH_RE = re.compile(r"(?<![\w/@.-])(?P<path>(?:[\w.-]+/)+[\w.@{}$()\[\]-]+(?:#[\w.-]+)?)")


def normalize(text: str) -> str:
    """Normalize text for loose matching."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def collect_headings(text: str) -> list[str]:
    """Return markdown headings from the file."""
    return [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]


def _headings_have_alias(headings: list[str], aliases: tuple[str, ...]) -> bool:
    """Return whether any heading contains one of the contract aliases."""
    normalized_headings = [normalize(heading) for heading in headings]
    return any(normalize(alias) in heading for alias in aliases for heading in normalized_headings)


def _role_requirement(name: str) -> SectionRequirement:
    """Return one role-contract section requirement by canonical name."""
    return next(requirement for requirement in CONTRACTS["role"] if requirement.name == name)


def detect_kind(text: str, headings: list[str]) -> KindResult:
    """Best-effort detection of the prompt artifact kind.

    Returns a KindResult with confidence:
    - high: heading-level schema match
    - medium: multiple body-text keyword signals
    - low: weak or no positive signals (defaults to system)

    Prefers heading-level signals over body-text keywords to reduce
    misclassification of role prompts that merely mention handoff behavior.
    """
    heading_haystack = normalize("\n".join(headings))

    # Heading-level schema takes precedence - high confidence
    if "participants" in heading_haystack and "handoff" in heading_haystack:
        return KindResult("multi-agent", "high")
    if _headings_have_alias(headings, _role_requirement("capabilities").aliases) and _headings_have_alias(
        headings, _role_requirement("boundaries").aliases
    ):
        return KindResult("role", "high")

    # Fall back to body-text keywords - medium confidence
    body_haystack = normalize(text)
    multi_agent_signals = sum(
        1 for kw in ("handoff", "shared artifacts", "tie-breaker", "participants") if kw in body_haystack
    )
    if multi_agent_signals >= 2:
        return KindResult("multi-agent", "medium")
    if "capabilities" in body_haystack and ("boundaries" in body_haystack or "out of scope" in body_haystack):
        return KindResult("role", "medium")

    return KindResult("system", "low")


def has_alias(text: str, headings: list[str], aliases: tuple[str, ...]) -> str:
    """Check section presence via heading match or body-text hint.

    Returns:
        "heading" - alias found in a heading (strong, satisfies requirement)
        "body"    - alias found in body text only (weak hint, does not satisfy)
        "missing" - alias not found anywhere
    """
    normalized_headings = [normalize(heading) for heading in headings]

    for alias in aliases:
        alias_norm = normalize(alias)
        if any(alias_norm in heading for heading in normalized_headings):
            return "heading"

    # Body-text fallback - hint only
    body_haystack = normalize(text)
    for alias in aliases:
        alias_norm = normalize(alias)
        if alias_norm in body_haystack:
            return "body"

    return "missing"


def _strip_example_context(text: str) -> str:
    """Remove CommonMark fenced code blocks and blockquotes from text.

    This prevents vague-phrase warnings from firing inside quoted examples
    where the phrases are intentionally shown as bad patterns. Plain prose
    under an example heading is still audited because prompts sometimes mix
    operational rules into example sections.
    """
    kept: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines():
        if fence_char is None:
            opening = re.match(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})", line)
            if opening is not None:
                marker = opening.group("fence")
                fence_char = marker[0]
                fence_length = len(marker)
                kept.append("")
                continue
        elif re.match(rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$", line):
            fence_char = None
            fence_length = 0
            kept.append("")
            continue

        if fence_char is not None or re.match(r"^[ \t]*>", line):
            kept.append("")
        else:
            kept.append(line)
    return "\n".join(kept)


def _is_example_heading(title: str) -> bool:
    """Return whether a heading marks example-only content."""
    return re.search(r"\bexamples?\b", normalize(title)) is not None


def _is_identity_heading(title: str) -> bool:
    """Return whether a heading names a role Identity section."""
    return re.fullmatch(r"(?:.+[ \t]+)?identity", normalize(title)) is not None


def _identity_sections(text: str) -> list[str]:
    """Return non-example Identity section bodies without crossing roles."""
    lines = _strip_example_context(text).splitlines()
    headings: list[tuple[int, int, str]] = []
    ancestors: list[tuple[int, str]] = []

    for index, line in enumerate(lines):
        match = MARKDOWN_HEADING_RE.match(line)
        if match is None:
            continue
        level = len(match.group("marks"))
        title = match.group("title").strip()
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        inside_example = any(_is_example_heading(ancestor_title) for _, ancestor_title in ancestors)
        inside_identity = any(_is_identity_heading(ancestor_title) for _, ancestor_title in ancestors)
        if _is_identity_heading(title) and not inside_example and not inside_identity:
            headings.append((index, level, title))
        ancestors.append((level, title))

    sections: list[str] = []
    for position, (start, level, _) in enumerate(headings):
        end = len(lines)
        next_identity = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        for index in range(start + 1, next_identity):
            match = MARKDOWN_HEADING_RE.match(lines[index])
            if match is not None and len(match.group("marks")) <= level:
                end = index
                break
        sections.append("\n".join(lines[start + 1 : min(end, next_identity)]))
    return sections


def _has_nonempty_value(lines: list[str], start: int, heading_level: int | None = None) -> bool:
    """Return whether a field or subheading has a non-empty following value."""
    for line in lines[start:]:
        if not line.strip():
            continue
        heading = MARKDOWN_HEADING_RE.match(line)
        if heading is not None:
            if heading_level is None or len(heading.group("marks")) <= heading_level:
                return False
            continue
        if OBJECTIVE_LABEL_RE.match(line) or re.match(
            r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*(?:\*\*|__)?[A-Za-z][A-Za-z \t]{0,40}"
            r"(?:\*\*|__)?[ \t]*:",
            line,
        ):
            return False
        return bool(re.sub(r"[`*_~]", "", line).strip())
    return False


def _is_nonobjective_identity_subsection(title: str) -> bool:
    """Return whether a nested heading leaves objective-bearing Identity content."""
    if _is_example_heading(title) or re.search(r"\bidentity\b", normalize(title)):
        return True
    return any(
        normalize(alias) in normalize(title)
        for requirement in CONTRACTS["role"]
        if requirement.name != "identity"
        for alias in requirement.aliases
    )


def _strip_nonobjective_identity_subsections(text: str) -> str:
    """Remove examples and nested non-Identity role-contract sections."""
    kept: list[str] = []
    skipped_level: int | None = None
    for line in text.splitlines():
        heading = MARKDOWN_HEADING_RE.match(line)
        if heading is not None:
            level = len(heading.group("marks"))
            if skipped_level is not None and level <= skipped_level:
                skipped_level = None
            if skipped_level is None and _is_nonobjective_identity_subsection(heading.group("title")):
                skipped_level = level
                continue
        if skipped_level is None:
            kept.append(line)
    return "\n".join(kept)


def _identity_has_objective(section: str) -> bool:
    """Return whether one Identity section contains a filled objective field."""
    lines = _strip_nonobjective_identity_subsections(_strip_example_context(section)).splitlines()
    for index, line in enumerate(lines):
        plain_line = re.sub(r"[`*_~]", "", line)
        if OBJECTIVE_STATEMENT_RE.search(plain_line) or SOLE_RESPONSIBILITY_STATEMENT_RE.search(plain_line):
            return True

        label = OBJECTIVE_LABEL_RE.match(line)
        if label is not None:
            if re.sub(r"[`*_~]", "", label.group("value")).strip():
                return True
            if _has_nonempty_value(lines, index + 1):
                return True

        heading = MARKDOWN_HEADING_RE.match(line)
        if heading is None:
            continue
        title = normalize(heading.group("title"))
        if title in OBJECTIVE_FIELD_NAMES and _has_nonempty_value(
            lines,
            index + 1,
            len(heading.group("marks")),
        ):
            return True
    return False


def _load_tool_names(manifest: Path) -> set[str]:
    """Load a simple tool-name manifest from JSON or newline text."""
    raw = manifest.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")}

    names: set[str] = set()
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("tool") or item.get("id")
                if isinstance(name, str):
                    names.add(name)
    elif isinstance(payload, dict):
        for key in ("tools", "tool_names", "commands"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        names.add(item)
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("tool") or item.get("id")
                        if isinstance(name, str):
                            names.add(name)
        for key, value in payload.items():
            if isinstance(value, dict) and ("description" in value or "inputSchema" in value):
                names.add(str(key))
    return names


def _iter_backticked_identifiers(text: str) -> Iterable[str]:
    """Yield code-span identifiers that look like tool or command names."""
    for match in re.finditer(r"`([^`\n]+)`", text):
        token = match.group(1).strip()
        if any(char in token for char in "/\\ .:$(){}[]<>"):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,}", token) and ("_" in token or "-" in token):
            yield token


def _iter_claimed_inputs(text: str) -> Iterable[str]:
    """Yield required input names from common prompt-spec phrasings."""
    for match in INPUT_CLAIM_RE.finditer(text):
        tail = re.split(r"\b(?:and|or)\b|,", match.group("tail"), flags=re.IGNORECASE)
        for item in tail:
            cleaned = item.strip(" `:-;\t\r\n")
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,}", cleaned):
                yield cleaned


def _path_exists(repo_root: Path, raw_path: str) -> bool:
    """Return whether a repo-relative path reference exists."""
    path_text = raw_path.split("#", 1)[0].strip("`'\".,:;)")
    if not path_text or any(marker in path_text for marker in ("<", ">", "*", "?", "$", "${")):
        return True
    if path_text.startswith(("http://", "https://", "mailto:")):
        return True
    return (repo_root / path_text).exists()


def audit_contract_facts(
    text: str,
    repo_root: Path | None = None,
    tool_manifest: Path | None = None,
    check_runtime_contract: bool = False,
) -> list[Issue]:
    """Run opt-in operational contract checks."""
    issues: list[Issue] = []

    if tool_manifest is not None:
        if not tool_manifest.exists():
            issues.append(Issue("error", "missing_tool_manifest", f"Tool manifest not found: {tool_manifest}"))
        else:
            tool_names = _load_tool_names(tool_manifest)
            if not tool_names:
                issues.append(
                    Issue("warning", "empty_tool_manifest", f"Tool manifest has no tool names: {tool_manifest}")
                )
            for token in sorted(set(_iter_backticked_identifiers(text))):
                if token not in tool_names:
                    issues.append(
                        Issue(
                            "warning",
                            "unresolved_tool_name",
                            f"Backticked identifier `{token}` is not in the provided tool manifest.",
                        )
                    )

    seen_inputs: set[str] = set()
    duplicate_inputs: set[str] = set()
    for input_name in _iter_claimed_inputs(text):
        normalized_name = input_name.casefold()
        if normalized_name in seen_inputs:
            duplicate_inputs.add(input_name)
        seen_inputs.add(normalized_name)
    for input_name in sorted(duplicate_inputs, key=str.casefold):
        issues.append(
            Issue(
                "warning",
                "duplicate_required_input",
                f"Required input `{input_name}` appears more than once. Check whether one input can derive another.",
            )
        )

    if repo_root is not None:
        if not repo_root.exists():
            issues.append(Issue("error", "missing_repo_root", f"Repo root not found: {repo_root}"))
        else:
            for match in PATHISH_RE.finditer(text):
                raw_path = match.group("path")
                if not _path_exists(repo_root, raw_path):
                    issues.append(
                        Issue(
                            "warning",
                            "missing_repo_path",
                            f"Path-like reference `{raw_path}` does not exist under {repo_root}.",
                        )
                    )

    if check_runtime_contract:
        haystack = normalize(text)
        has_runtime_surface = any(term in haystack for term in RUNTIME_TRIGGER_TERMS)
        if has_runtime_surface:
            for field, aliases in RUNTIME_FIELDS.items():
                if not any(alias in haystack for alias in aliases):
                    issues.append(
                        Issue(
                            "warning",
                            "missing_runtime_lifecycle_field",
                            f"Runtime contract may be missing `{field}` coverage.",
                        )
                    )

    return issues


def audit_text(
    text: str,
    kind: str,
    repo_root: Path | None = None,
    tool_manifest: Path | None = None,
    check_runtime_contract: bool = False,
    template_baseline: bool = False,
) -> list[Issue]:
    """Audit text against one contract."""
    issues: list[Issue] = []
    headings = collect_headings(text)
    requirements = CONTRACTS[kind]

    if not headings:
        issues.append(
            Issue(
                "warning",
                "no_headings",
                "No markdown headings found. Keep sections clearly separated even if the prompt is plain text.",
            )
        )

    for requirement in requirements:
        match_level = has_alias(text, headings, requirement.aliases)
        if match_level == "missing":
            issues.append(
                Issue(
                    "error",
                    f"missing_{requirement.name.replace(' ', '_')}",
                    f"Missing {requirement.name} section. {requirement.guidance}",
                )
            )
        elif match_level == "body":
            issues.append(
                Issue(
                    "warning",
                    f"hint_only_{requirement.name.replace(' ', '_')}",
                    f"{requirement.name} found in body text but not as a heading. "
                    f"Add an explicit heading to satisfy the structural contract.",
                )
            )

    if template_baseline:
        issues.append(
            Issue(
                "warning",
                "template_baseline",
                "Template baseline mode checks required headings only. Rerun without --template-baseline after filling content.",
            )
        )
        return issues

    # Ignore examples for advisory content checks.
    prose_text = _strip_example_context(text)
    if kind == "role":
        identity_sections = _identity_sections(text)
        missing_objectives = sum(not _identity_has_objective(section) for section in identity_sections)
        if not identity_sections:
            missing_objectives = 1
        for _ in range(missing_objectives):
            issues.append(
                Issue(
                    "warning",
                    "missing_primary_objective",
                    "No non-empty primary objective found in Identity. "
                    "Add - Primary objective: <single outcome>.",
                )
            )

    lowered = prose_text.casefold()
    for phrase, suggestion in VAGUE_PHRASES.items():
        if phrase in lowered:
            issues.append(
                Issue(
                    "warning",
                    "vague_phrase",
                    f'Found vague phrase "{phrase}". {suggestion}',
                )
            )

    line_count = len(text.splitlines())
    if line_count > PROMPT_LINE_WARNING_THRESHOLD:
        issues.append(
            Issue(
                "warning",
                "long_prompt",
                f"Prompt is long ({line_count} lines). Move examples or detail to references before adding more rules.",
            )
        )

    issues.extend(audit_contract_facts(text, repo_root, tool_manifest, check_runtime_contract))

    return issues


def render_markdown(target: Path, kind_result: KindResult, issues: list[Issue]) -> str:
    """Render the audit as markdown."""
    lines = [
        "# Prompt Structure Audit",
        "",
        f"Target: {target}",
        "",
        f"Kind: {kind_result.kind} (confidence: {kind_result.confidence})",
        "",
    ]

    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]

    if not issues:
        lines.append("PASS")
        return "\n".join(lines)

    if errors:
        lines.append("## Errors")
        for issue in errors:
            lines.append(f"- {issue.message}")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        for issue in warnings:
            lines.append(f"- {issue.message}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main() -> int:
    """Run the prompt structure audit."""
    parser = argparse.ArgumentParser(description="Audit prompt structure and contract coverage")
    parser.add_argument("target", type=Path, help="Prompt or contract file to audit")
    parser.add_argument(
        "--kind",
        choices=["auto", "system", "role", "multi-agent"],
        default="auto",
        help="Which contract to audit against",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Optional repo root for checking path-like examples and required artifact claims",
    )
    parser.add_argument(
        "--tool-manifest",
        type=Path,
        help="Optional JSON or newline manifest of valid tool and command names",
    )
    parser.add_argument(
        "--check-runtime-contract",
        action="store_true",
        help="Warn when helper, MCP, queue, worker, or async contracts omit lifecycle fields",
    )
    parser.add_argument(
        "--template-baseline",
        action="store_true",
        help="Check starter templates for required headings only; rerun normal audit after content is filled",
    )
    args = parser.parse_args()

    target = args.target.resolve()
    if not target.exists():
        print(f"Error: file not found: {target}", file=sys.stderr)
        return 1
    if not target.is_file():
        print(f"Error: target is not a file: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")
    headings = collect_headings(text)

    if args.kind == "auto":
        kind_result = detect_kind(text, headings)
    else:
        kind_result = KindResult(args.kind, "high")  # explicit override is always high

    issues = audit_text(
        text,
        kind_result.kind,
        repo_root=args.repo_root.resolve() if args.repo_root else None,
        tool_manifest=args.tool_manifest.resolve() if args.tool_manifest else None,
        check_runtime_contract=args.check_runtime_contract,
        template_baseline=args.template_baseline,
    )

    if args.kind == "auto" and kind_result.confidence == "low":
        issues.insert(
            0,
            Issue(
                "warning",
                "low_kind_confidence",
                "Auto-detection is uncertain. Pass --kind system, --kind role, or --kind multi-agent if this is not a system prompt.",
            ),
        )

    if args.json:
        payload = {
            "target": str(target),
            "kind": kind_result.kind,
            "kind_confidence": kind_result.confidence,
            "issues": [asdict(issue) for issue in issues],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(target, kind_result, issues))

    return 1 if any(issue.severity == "error" for issue in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
