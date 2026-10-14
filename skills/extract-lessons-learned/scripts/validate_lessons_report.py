"""Validate the terminal routing ledger in a lessons-learned Markdown report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

SCHEMA_VERSION = 1
LESSON_ID_RE = re.compile(r"^L[1-9][0-9]*$")
LESSON_HEADING_RE = re.compile(r"^###\s+\[(L[1-9][0-9]*)\](?:\s+|$)", re.MULTILINE)
SECTION_RE_TEMPLATE = r"^##\s+{heading}\s*$"
NEXT_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
PLACEHOLDER_RE = re.compile(
    r"\{[^}]+\}|<[^>]+>|\b(?:tbd|todo|placeholder|fill\s+in)\b",
    re.IGNORECASE,
)
EXACT_PATH_RE = re.compile(
    r"^(?:[^/{}<>\r\n]+/)*[^/{}<>\r\n]+\.(?:md|py|json|ya?ml|toml|txt)"
    r"(?:#[A-Za-z0-9._-]+)?$",
    re.IGNORECASE,
)
MEMORY_DESTINATION_RE = re.compile(r"^memory:[a-z0-9][a-z0-9._/-]*$")
PRIOR_MATCH_RE = re.compile(r"^.+\.md#L[1-9][0-9]*$", re.IGNORECASE)
ROOT_COUNT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
ROOT_FAMILY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
FILE_REFERENCE_RE = re.compile(
    r"^(?:[^/{}<>|@\r\n]+/)*[^/{}<>|@\r\n]+\."
    r"(?:jsonl|sqlite|md|py|json|sql|ya?ml|toml|txt)"
    r"(?:(?:::|#)[A-Za-z0-9._/-]+)?$",
    re.IGNORECASE,
)
NAMED_REFERENCE_RE = re.compile(
    r"^(?:artifact|check|query|test):[A-Za-z0-9][A-Za-z0-9._/#-]*$"
)
STRUCTURED_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159}$")
STRUCTURED_REFERENCE_SEPARATOR_RE = re.compile(r"[./#:@=-]")
CORRECTNESS_IMPACT_RE = re.compile(
    r"^correctness:(?P<kind>[a-z-]+):(?P<affected>[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159})"
    r"\s*\|\s*evidence:(?P<evidence_kind>[a-z-]+):"
    r"(?P<evidence>[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159})$"
)
DEFER_TRIGGER_RE = re.compile(
    r"^when:(?P<event_kind>[a-z-]+):(?P<event_ref>[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159})"
    r"\s*\|\s*evidence:(?P<evidence_kind>[a-z-]+):"
    r"(?P<evidence>[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159})$"
)
OBSERVE_ACTION_RE = re.compile(
    r"^observe:(?P<evidence_kind>[a-z-]+):"
    r"(?P<reference>[A-Za-z0-9][A-Za-z0-9._/#:@=-]{0,159})$"
)
OBSERVABLE_GATE_RE = re.compile(
    r"\b(?:absent|contains?|counts?|emits?|equals?|exists?|exits?|matches?|"
    r"passes?|present|recorded|reports?|reproduces?|returns?|zero|nonzero)\b",
    re.IGNORECASE,
)
GENERIC_FREE_TEXT_RE = re.compile(r"\b(?:done|later)\b", re.IGNORECASE)
UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\{[^{}\r\n]+\}")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")

IMPLEMENTATION_COLUMNS = (
    "lesson id",
    "destination checked",
    "status",
    "existing coverage",
    "gap to patch",
)
ROUTING_COLUMNS = (
    "lesson id",
    "disposition",
    "destination",
    "primary root evidence",
    "independent root families",
    "one-off correctness impact",
    "prior synthesis match",
    "prior synthesis decision",
    "confidence",
    "limitations / counter-evidence",
    "action kind",
    "action",
    "validation gate",
    "defer trigger",
    "why",
)
IMPLEMENTATION_STATUSES = {"new", "already covered", "partial", "conflicts"}
DISPOSITIONS = {
    "apply",
    "monitor",
    "already-covered",
    "supersede",
    "defer",
    "discard",
}
AUDIT_DISPOSITIONS = {"apply", "already-covered", "supersede", "defer"}
STATUS_COMPATIBILITY = {
    "apply": {"new", "partial"},
    "already-covered": {"already covered"},
    "supersede": {"conflicts"},
    "defer": {"new", "partial", "conflicts"},
}
PRIOR_DECISIONS = {"none", "dedupe", "supersede"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
ACTION_KIND_COMPATIBILITY = {
    "apply": {"validate", "test", "edit", "create", "document"},
    "monitor": {"observe"},
    "already-covered": {"none"},
    "supersede": {"none"},
    "defer": {"defer"},
    "discard": {"none"},
}
CORRECTNESS_KINDS = {
    "incorrect-output",
    "data-loss",
    "contract-break",
    "runtime-failure",
    "workflow-blocker",
}
DEFER_EVENT_KINDS = {
    "recurrence",
    "test-failure",
    "incident",
    "artifact-change",
    "threshold",
}
STRUCTURED_EVIDENCE_KINDS = {"test", "query", "artifact", "command", "trace"}
EMPTY_MARKERS = {"", "-", "--", "n/a", "na", "none"}
GENERIC_PLACEHOLDERS = {
    "action",
    "done",
    "example",
    "example text",
    "later",
    "replace me",
    "source path or session key",
    "validation gate",
    "why",
}
PLACEHOLDER_PATH_SEGMENTS = {
    "example",
    "file",
    "path",
    "path-to",
    "project",
    "skill-name",
}


@dataclass(frozen=True)
class Diagnostic:
    """One concise, actionable validation failure."""

    code: str
    message: str
    lesson_id: str | None = None
    field: str | None = None


def clean_cell(value: str) -> str:
    """Remove Markdown code ticks and surrounding whitespace from a table cell."""

    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        value = value[1:-1].strip()
    return value


def normalize_header(value: str) -> str:
    """Return a stable comparison form for a Markdown table header."""

    return re.sub(r"\s+", " ", clean_cell(value)).casefold()


def split_table_row(line: str) -> list[str]:
    """Split one simple Markdown table row, preserving escaped pipes."""

    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    parts = re.split(r"(?<!\\)\|", text)
    return [clean_cell(part.replace(r"\|", "|")) for part in parts]


def section_body(text: str, heading: str) -> str | None:
    """Return the body of an exact level-two section."""

    match = re.search(
        SECTION_RE_TEMPLATE.format(heading=re.escape(heading)), text, re.MULTILINE
    )
    if match is None:
        return None
    next_match = NEXT_SECTION_RE.search(text, match.end())
    end = next_match.start() if next_match else len(text)
    return text[match.end() : end]


def parse_table(
    text: str, section: str, required_columns: Sequence[str]
) -> tuple[list[dict[str, str]], list[Diagnostic]]:
    """Parse the first Markdown table in a named section."""

    body = section_body(text, section)
    if body is None:
        return [], [
            Diagnostic(
                "missing_section",
                f"add the '## {section}' section",
                field=section,
            )
        ]

    lines = body.splitlines()
    for index in range(len(lines) - 1):
        if "|" not in lines[index] or "|" not in lines[index + 1]:
            continue
        headers = [normalize_header(cell) for cell in split_table_row(lines[index])]
        separators = [clean_cell(cell) for cell in split_table_row(lines[index + 1])]
        if len(headers) != len(separators) or not all(
            TABLE_SEPARATOR_RE.fullmatch(cell) for cell in separators
        ):
            continue

        missing = [column for column in required_columns if column not in headers]
        if missing:
            return [], [
                Diagnostic(
                    "missing_columns",
                    f"add required columns: {', '.join(missing)}",
                    field=section,
                )
            ]

        rows: list[dict[str, str]] = []
        diagnostics: list[Diagnostic] = []
        for line_number, line in enumerate(lines[index + 2 :], start=index + 3):
            if not line.strip() or "|" not in line:
                if rows:
                    break
                continue
            cells = split_table_row(line)
            if len(cells) != len(headers):
                diagnostics.append(
                    Diagnostic(
                        "invalid_table_row",
                        f"row {line_number} has {len(cells)} cells; expected {len(headers)}",
                        field=section,
                    )
                )
                continue
            rows.append(dict(zip(headers, cells, strict=True)))
        if not rows:
            diagnostics.append(
                Diagnostic("empty_table", "add at least one lesson row", field=section)
            )
        return rows, diagnostics

    return [], [
        Diagnostic(
            "missing_table", f"add a Markdown table under '## {section}'", field=section
        )
    ]


def is_placeholder(value: str, *, allow_none: bool = False) -> bool:
    """Return whether a required cell is empty, generic, or templated."""

    normalized = clean_cell(value).casefold()
    if normalized in EMPTY_MARKERS:
        return not (allow_none and normalized == "none")
    if normalized in GENERIC_PLACEHOLDERS:
        return True
    return PLACEHOLDER_RE.search(value) is not None


def is_specific_text(value: str) -> bool:
    """Return whether general free text is populated and not a placeholder."""

    value = clean_cell(value)
    return (
        not is_placeholder(value)
        and GENERIC_FREE_TEXT_RE.search(value) is None
        and re.search(r"[A-Za-z0-9]", value) is not None
    )


def is_artifact_reference(value: str) -> bool:
    """Return whether a value is a specific artifact, test, check, or query reference."""

    value = clean_cell(value).replace("\\", "/")
    return not is_placeholder(value) and (
        FILE_REFERENCE_RE.fullmatch(value) is not None
        or NAMED_REFERENCE_RE.fullmatch(value) is not None
    )


def is_structured_reference(value: str) -> bool:
    """Return whether a bounded reference has machine-visible structure."""

    return (
        STRUCTURED_REFERENCE_RE.fullmatch(value) is not None
        and STRUCTURED_REFERENCE_SEPARATOR_RE.search(value) is not None
    )


def unresolved_placeholder_diagnostics(text: str) -> list[Diagnostic]:
    """Find unresolved brace placeholders outside fenced examples."""

    diagnostics: list[Diagnostic] = []
    fence_char: str | None = None
    fence_length = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
            continue
        if fence_char is not None:
            continue
        for match in UNRESOLVED_PLACEHOLDER_RE.finditer(line):
            diagnostics.append(
                Diagnostic(
                    "unresolved_placeholder",
                    f"replace unresolved placeholder at line {line_number}, column {match.start() + 1}",
                    field="report",
                )
            )
    return diagnostics


def is_exact_destination(value: str) -> bool:
    """Return whether a destination is a concrete portable path, memory key, or discard."""

    value = clean_cell(value).replace("\\", "/")
    if value == "discard":
        return True
    if is_placeholder(value):
        return False
    path_without_anchor = value.split("#", 1)[0]
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    path_parts = path_without_anchor.split("/")
    if any(part in {"", ".", ".."} for part in path_parts):
        return False
    if any(part.casefold() in PLACEHOLDER_PATH_SEGMENTS for part in path_parts):
        return False
    return (
        EXACT_PATH_RE.fullmatch(value) is not None
        or MEMORY_DESTINATION_RE.fullmatch(value) is not None
    )


def is_exact_prior_match(value: str) -> bool:
    """Return whether a prior synthesis match names a Markdown artifact and L* ID."""

    value = clean_cell(value).replace("\\", "/")
    return is_exact_destination(value) and PRIOR_MATCH_RE.fullmatch(value) is not None


def index_rows(
    rows: list[dict[str, str]], section: str
) -> tuple[dict[str, dict[str, str]], list[Diagnostic]]:
    """Index table rows by a valid, unique lesson ID."""

    indexed: dict[str, dict[str, str]] = {}
    diagnostics: list[Diagnostic] = []
    for row in rows:
        lesson_id = clean_cell(row.get("lesson id", ""))
        if LESSON_ID_RE.fullmatch(lesson_id) is None:
            diagnostics.append(
                Diagnostic(
                    "invalid_lesson_id",
                    "use an uppercase lesson ID such as L1",
                    lesson_id=lesson_id or None,
                    field=section,
                )
            )
            continue
        if lesson_id in indexed:
            diagnostics.append(
                Diagnostic(
                    "duplicate_lesson_id",
                    f"keep exactly one {section} row for {lesson_id}",
                    lesson_id=lesson_id,
                    field=section,
                )
            )
            continue
        indexed[lesson_id] = row
    return indexed, diagnostics


def add_required_text(
    diagnostics: list[Diagnostic], lesson_id: str, field: str, value: str
) -> None:
    """Require non-placeholder text in one report cell."""

    if is_placeholder(value):
        diagnostics.append(
            Diagnostic(
                "incomplete_field",
                f"replace '{field}' with concrete content",
                lesson_id,
                field,
            )
        )


def validate_prior_synthesis(
    diagnostics: list[Diagnostic], lesson_id: str, route: dict[str, str]
) -> None:
    """Validate a routing row's prior-synthesis match and decision."""

    disposition = route["disposition"].casefold()
    prior_match = clean_cell(route["prior synthesis match"])
    prior_decision = route["prior synthesis decision"].casefold()
    if prior_decision not in PRIOR_DECISIONS:
        diagnostics.append(
            Diagnostic(
                "invalid_prior_decision",
                f"use one of: {', '.join(sorted(PRIOR_DECISIONS))}",
                lesson_id,
                "prior synthesis decision",
            )
        )
    match_is_none = prior_match.casefold() == "none"
    decision_is_none = prior_decision == "none"
    if match_is_none != decision_is_none:
        diagnostics.append(
            Diagnostic(
                "prior_synthesis_mismatch",
                "use none for both prior-synthesis fields, or pair an exact match with dedupe/supersede",
                lesson_id,
                "prior synthesis match",
            )
        )
    if not match_is_none and not is_exact_prior_match(prior_match):
        diagnostics.append(
            Diagnostic(
                "invalid_prior_match",
                "use an exact artifact-and-lesson reference such as path.md#L2",
                lesson_id,
                "prior synthesis match",
            )
        )
    if (disposition == "supersede") != (prior_decision == "supersede"):
        diagnostics.append(
            Diagnostic(
                "supersession_mismatch",
                "pair supersede disposition with supersede prior-synthesis decision",
                lesson_id,
                "prior synthesis decision",
            )
        )
    if prior_decision == "dedupe" and disposition not in {
        "already-covered",
        "discard",
    }:
        diagnostics.append(
            Diagnostic(
                "dedupe_disposition_mismatch",
                "route a deduplicated prior-synthesis match as already-covered or discard",
                lesson_id,
                "disposition",
            )
        )


def validate_primary_root_evidence(
    diagnostics: list[Diagnostic], lesson_id: str, route: dict[str, str]
) -> int | None:
    """Validate primary-root entries and return their unique family count."""

    value = clean_cell(route["primary root evidence"])
    prior_match = clean_cell(route["prior synthesis match"]).replace("\\", "/")
    if value == "none":
        return 0
    if is_placeholder(value):
        diagnostics.append(
            Diagnostic(
                "invalid_primary_root_evidence",
                "use semicolon-separated root-family-id@artifact-reference entries",
                lesson_id,
                "primary root evidence",
            )
        )
        return None

    family_ids: set[str] = set()
    valid = True
    for entry in (item.strip() for item in value.split(";")):
        if entry.count("@") != 1:
            diagnostics.append(
                Diagnostic(
                    "invalid_primary_root_evidence",
                    "use root-family-id@artifact-reference for every entry",
                    lesson_id,
                    "primary root evidence",
                )
            )
            valid = False
            continue
        family_id, artifact = (part.strip() for part in entry.split("@", 1))
        artifact = artifact.replace("\\", "/")
        if ROOT_FAMILY_ID_RE.fullmatch(family_id) is None or not is_artifact_reference(
            artifact
        ):
            diagnostics.append(
                Diagnostic(
                    "invalid_primary_root_evidence",
                    "use a stable family ID and specific artifact, test, check, or query reference",
                    lesson_id,
                    "primary root evidence",
                )
            )
            valid = False
        if family_id in family_ids:
            diagnostics.append(
                Diagnostic(
                    "duplicate_root_family",
                    f"keep one primary evidence entry for root family {family_id}",
                    lesson_id,
                    "primary root evidence",
                )
            )
            valid = False
        family_ids.add(family_id)
        artifact_folded = artifact.casefold()
        artifact_file = artifact_folded.split("#", 1)[0]
        prior_file = prior_match.casefold().split("#", 1)[0]
        if "docs/lessons-learned/" in artifact_folded or (
            prior_match.casefold() != "none" and artifact_file == prior_file
        ):
            diagnostics.append(
                Diagnostic(
                    "prior_synthesis_as_primary_evidence",
                    "use primary evidence; prior synthesis cannot count as a root family",
                    lesson_id,
                    "primary root evidence",
                )
            )
            valid = False
    return len(family_ids) if valid else None


def validate_correctness_impact(value: str) -> bool:
    """Validate the exact one-off correctness-impact grammar."""

    match = CORRECTNESS_IMPACT_RE.fullmatch(clean_cell(value))
    if match is None:
        return False
    return (
        match.group("kind") in CORRECTNESS_KINDS
        and match.group("evidence_kind") in STRUCTURED_EVIDENCE_KINDS
        and is_structured_reference(match.group("affected"))
        and is_structured_reference(match.group("evidence"))
        and "docs/lessons-learned/" not in match.group("evidence").casefold()
    )


def validate_defer_trigger(value: str) -> bool:
    """Validate the exact defer trigger and evidence grammar."""

    match = DEFER_TRIGGER_RE.fullmatch(clean_cell(value))
    if match is None:
        return False
    return (
        match.group("event_kind") in DEFER_EVENT_KINDS
        and match.group("evidence_kind") in STRUCTURED_EVIDENCE_KINDS
        and is_structured_reference(match.group("event_ref"))
        and is_structured_reference(match.group("evidence"))
        and "docs/lessons-learned/" not in match.group("evidence").casefold()
    )


def validate_observe_action(value: str) -> bool:
    """Validate the exact monitor observation grammar."""

    match = OBSERVE_ACTION_RE.fullmatch(clean_cell(value))
    if match is None:
        return False
    return match.group(
        "evidence_kind"
    ) in STRUCTURED_EVIDENCE_KINDS and is_structured_reference(match.group("reference"))


def validate_route(
    diagnostics: list[Diagnostic], lesson_id: str, route: dict[str, str]
) -> None:
    """Validate one terminal routing row."""

    disposition = route["disposition"].casefold()
    destination = clean_cell(route["destination"])
    for field in (
        "confidence",
        "limitations / counter-evidence",
        "validation gate",
        "why",
    ):
        add_required_text(diagnostics, lesson_id, field, route[field])

    if disposition not in DISPOSITIONS:
        diagnostics.append(
            Diagnostic(
                "invalid_disposition",
                f"use one of: {', '.join(sorted(DISPOSITIONS))}",
                lesson_id,
                "disposition",
            )
        )
    if not is_exact_destination(destination):
        diagnostics.append(
            Diagnostic(
                "invalid_destination",
                "use an exact destination path, memory key, or discard",
                lesson_id,
                "destination",
            )
        )
    if disposition == "discard" and destination != "discard":
        diagnostics.append(
            Diagnostic(
                "discard_destination_mismatch",
                "set destination to discard for a discard disposition",
                lesson_id,
                "destination",
            )
        )
    if disposition in DISPOSITIONS - {"discard"} and destination == "discard":
        diagnostics.append(
            Diagnostic(
                "active_destination_missing",
                "use an exact non-discard destination for this disposition",
                lesson_id,
                "destination",
            )
        )

    confidence = route["confidence"].casefold()
    if confidence not in CONFIDENCE_VALUES:
        diagnostics.append(
            Diagnostic(
                "invalid_confidence",
                "use high, medium, or low",
                lesson_id,
                "confidence",
            )
        )

    roots_text = clean_cell(route["independent root families"])
    declared_root_count: int | None = None
    if ROOT_COUNT_RE.fullmatch(roots_text) is None:
        diagnostics.append(
            Diagnostic(
                "invalid_root_family_count",
                "use a nonnegative integer count of independent primary-evidence root families",
                lesson_id,
                "independent root families",
            )
        )
    else:
        declared_root_count = int(roots_text)

    derived_root_count = validate_primary_root_evidence(diagnostics, lesson_id, route)
    if (
        declared_root_count is not None
        and derived_root_count is not None
        and declared_root_count != derived_root_count
    ):
        diagnostics.append(
            Diagnostic(
                "root_family_count_mismatch",
                f"declared {declared_root_count} root families but primary evidence has {derived_root_count}",
                lesson_id,
                "independent root families",
            )
        )

    impact = clean_cell(route["one-off correctness impact"])
    if disposition == "apply" and derived_root_count is not None:
        if derived_root_count == 0:
            diagnostics.append(
                Diagnostic(
                    "unsupported_apply",
                    "apply requires at least one primary-evidence root family",
                    lesson_id,
                    "disposition",
                )
            )
        elif derived_root_count == 1:
            if not validate_correctness_impact(impact):
                diagnostics.append(
                    Diagnostic(
                        "invalid_correctness_impact",
                        "use 'correctness:<kind>:<affected-reference> | evidence:<kind>:<reference>' with supported kinds",
                        lesson_id,
                        "one-off correctness impact",
                    )
                )
        elif impact != "none":
            diagnostics.append(
                Diagnostic(
                    "unexpected_correctness_impact",
                    "use exact none when apply has at least 2 independent root families",
                    lesson_id,
                    "one-off correctness impact",
                )
            )
    elif disposition != "apply" and impact != "none":
        diagnostics.append(
            Diagnostic(
                "unexpected_correctness_impact",
                "use exact none unless a one-root apply needs correctness justification",
                lesson_id,
                "one-off correctness impact",
            )
        )

    validate_prior_synthesis(diagnostics, lesson_id, route)

    action_kind = route["action kind"].casefold()
    allowed_kinds = ACTION_KIND_COMPATIBILITY.get(disposition, set())
    if action_kind not in allowed_kinds:
        diagnostics.append(
            Diagnostic(
                "action_kind_disposition_mismatch",
                f"use action kind {', '.join(sorted(allowed_kinds)) or 'none'} for {disposition}",
                lesson_id,
                "action kind",
            )
        )

    action = clean_cell(route["action"])
    if action_kind in {"validate", "test", "edit", "create", "document"}:
        prefix = f"{action_kind} "
        action_detail = (
            action[len(prefix) :] if action.casefold().startswith(prefix) else ""
        )
        if not is_specific_text(action_detail):
            diagnostics.append(
                Diagnostic(
                    "action_prefix_mismatch",
                    f"start the action with '{action_kind} ' and name its concrete target",
                    lesson_id,
                    "action",
                )
            )
    elif action_kind == "observe":
        if not validate_observe_action(action):
            diagnostics.append(
                Diagnostic(
                    "invalid_observe_action",
                    "use exact 'observe:<evidence-kind>:<reference>' with a supported evidence kind",
                    lesson_id,
                    "action",
                )
            )
    elif action_kind == "none" and action != "none":
        diagnostics.append(
            Diagnostic(
                "action_prefix_mismatch",
                "use exact none when action kind is none",
                lesson_id,
                "action",
            )
        )
    elif action_kind == "defer" and action != "defer until trigger":
        diagnostics.append(
            Diagnostic(
                "action_prefix_mismatch",
                "use exact 'defer until trigger' when action kind is defer",
                lesson_id,
                "action",
            )
        )

    gate = clean_cell(route["validation gate"])
    if not is_specific_text(gate) or OBSERVABLE_GATE_RE.search(gate) is None:
        diagnostics.append(
            Diagnostic(
                "invalid_validation_gate",
                "name an observable pass condition; generic later/done is invalid",
                lesson_id,
                "validation gate",
            )
        )

    trigger = clean_cell(route["defer trigger"])
    if disposition == "defer":
        if not validate_defer_trigger(trigger):
            diagnostics.append(
                Diagnostic(
                    "missing_defer_trigger",
                    "use 'when:<event-kind>:<reference> | evidence:<kind>:<reference>' with supported kinds",
                    lesson_id,
                    "defer trigger",
                )
            )
    elif trigger.casefold() != "n/a":
        diagnostics.append(
            Diagnostic(
                "unexpected_defer_trigger",
                "use exact n/a unless the disposition is defer",
                lesson_id,
                "defer trigger",
            )
        )


def validate_audit(
    diagnostics: list[Diagnostic],
    lesson_id: str,
    audit: dict[str, str],
    route: dict[str, str],
) -> None:
    """Validate one required durable-destination implementation audit."""

    disposition = route["disposition"].casefold()
    destination = clean_cell(route["destination"])
    checked_destination = clean_cell(audit["destination checked"])
    status = audit["status"].casefold()
    existing = clean_cell(audit["existing coverage"])
    gap = clean_cell(audit["gap to patch"])

    if (
        not is_exact_destination(checked_destination)
        or checked_destination == "discard"
    ):
        diagnostics.append(
            Diagnostic(
                "invalid_destination",
                "use an exact non-discard destination checked",
                lesson_id,
                "destination checked",
            )
        )
    if checked_destination.replace("\\", "/") != destination.replace("\\", "/"):
        diagnostics.append(
            Diagnostic(
                "destination_mismatch",
                "use the same exact destination in implementation and routing",
                lesson_id,
                "destination checked",
            )
        )

    if status not in IMPLEMENTATION_STATUSES:
        diagnostics.append(
            Diagnostic(
                "invalid_status",
                f"use one of: {', '.join(sorted(IMPLEMENTATION_STATUSES))}",
                lesson_id,
                "status",
            )
        )
    elif status not in STATUS_COMPATIBILITY.get(disposition, set()):
        allowed = ", ".join(sorted(STATUS_COMPATIBILITY.get(disposition, set())))
        diagnostics.append(
            Diagnostic(
                "status_disposition_mismatch",
                f"use status {allowed} with disposition {disposition}",
                lesson_id,
                "status",
            )
        )

    if status == "new":
        if existing.casefold() != "none":
            diagnostics.append(
                Diagnostic(
                    "invalid_existing_coverage",
                    "use exact none when implementation status is new",
                    lesson_id,
                    "existing coverage",
                )
            )
    elif not is_specific_text(existing):
        diagnostics.append(
            Diagnostic(
                "invalid_existing_coverage",
                "describe concrete existing coverage for this status",
                lesson_id,
                "existing coverage",
            )
        )

    if disposition in {"already-covered", "supersede"}:
        if gap.casefold() != "none":
            diagnostics.append(
                Diagnostic(
                    "forbidden_gap",
                    "use exact none because this disposition makes no edit",
                    lesson_id,
                    "gap to patch",
                )
            )
    elif not is_specific_text(gap):
        diagnostics.append(
            Diagnostic(
                "incomplete_gap",
                "name the concrete implementation gap; placeholders and none are invalid",
                lesson_id,
                "gap to patch",
            )
        )


def has_table(body: str | None) -> bool:
    """Return whether a section body appears to contain a Markdown table."""

    if body is None:
        return False
    lines = body.splitlines()
    return any(
        "|" in lines[index] and "|" in lines[index + 1]
        for index in range(len(lines) - 1)
    )


def validate_report(text: str) -> tuple[list[str], list[Diagnostic]]:
    """Validate one lessons report and return stable lesson IDs and diagnostics."""

    diagnostics = unresolved_placeholder_diagnostics(text)
    heading_ids = LESSON_HEADING_RE.findall(text)
    heading_counts = Counter(heading_ids)
    for lesson_id, count in sorted(heading_counts.items()):
        if count > 1:
            diagnostics.append(
                Diagnostic(
                    "duplicate_lesson_id",
                    f"keep exactly one lesson heading for {lesson_id}",
                    lesson_id,
                    "lesson heading",
                )
            )
    if not heading_ids:
        diagnostics.append(
            Diagnostic(
                "missing_lesson_id",
                "add at least one lesson heading such as '### [L1] Title'",
                field="lesson heading",
            )
        )

    routing_rows, table_diagnostics = parse_table(text, "Routing", ROUTING_COLUMNS)
    diagnostics.extend(table_diagnostics)
    routing, row_diagnostics = index_rows(routing_rows, "Routing")
    diagnostics.extend(row_diagnostics)

    heading_set = set(heading_counts)
    routing_set = set(routing)
    for lesson_id in sorted(heading_set - routing_set, key=lambda item: int(item[1:])):
        diagnostics.append(
            Diagnostic(
                "missing_lesson_id",
                f"add {lesson_id} to Routing",
                lesson_id,
                "Routing",
            )
        )
    for lesson_id in sorted(routing_set - heading_set, key=lambda item: int(item[1:])):
        diagnostics.append(
            Diagnostic(
                "orphan_lesson_id",
                f"remove {lesson_id} or add its lesson heading",
                lesson_id,
                "Routing",
            )
        )

    common_ids = heading_set & routing_set
    expected_audit_ids = {
        lesson_id
        for lesson_id in common_ids
        if routing[lesson_id]["disposition"].casefold() in AUDIT_DISPOSITIONS
    }
    implementation: dict[str, dict[str, str]] = {}
    implementation_body = section_body(text, "Implementation Audit")
    if expected_audit_ids or has_table(implementation_body):
        implementation_rows, table_diagnostics = parse_table(
            text, "Implementation Audit", IMPLEMENTATION_COLUMNS
        )
        diagnostics.extend(table_diagnostics)
        implementation, row_diagnostics = index_rows(
            implementation_rows, "Implementation Audit"
        )
        diagnostics.extend(row_diagnostics)

    implementation_set = set(implementation)
    for lesson_id in sorted(
        expected_audit_ids - implementation_set, key=lambda item: int(item[1:])
    ):
        diagnostics.append(
            Diagnostic(
                "missing_implementation_audit",
                f"add a durable-destination implementation audit for {lesson_id}",
                lesson_id,
                "Implementation Audit",
            )
        )
    for lesson_id in sorted(
        implementation_set - expected_audit_ids, key=lambda item: int(item[1:])
    ):
        code = (
            "orphan_lesson_id"
            if lesson_id not in heading_set
            else "unexpected_implementation_audit"
        )
        message = (
            f"remove {lesson_id} or add its lesson heading"
            if lesson_id not in heading_set
            else f"remove {lesson_id}; monitor and discard do not use implementation audit rows"
        )
        diagnostics.append(Diagnostic(code, message, lesson_id, "Implementation Audit"))

    for lesson_id in sorted(common_ids, key=lambda item: int(item[1:])):
        validate_route(diagnostics, lesson_id, routing[lesson_id])
        if lesson_id in expected_audit_ids and lesson_id in implementation:
            validate_audit(
                diagnostics, lesson_id, implementation[lesson_id], routing[lesson_id]
            )

    diagnostics.sort(
        key=lambda item: (
            int(item.lesson_id[1:])
            if item.lesson_id and LESSON_ID_RE.fullmatch(item.lesson_id)
            else 0,
            item.field or "",
            item.code,
        )
    )
    lesson_ids = sorted(heading_counts, key=lambda item: int(item[1:]))
    return lesson_ids, diagnostics


def build_envelope(path: Path, text: str) -> dict[str, object]:
    """Build the stable JSON result envelope for one report."""

    lesson_ids, diagnostics = validate_report(text)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not diagnostics,
        "result": {
            "report": str(path),
            "lesson_count": len(lesson_ids),
            "lesson_ids": lesson_ids,
            "diagnostics": [asdict(item) for item in diagnostics],
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Validate lesson IDs and terminal routing dispositions."
    )
    parser.add_argument("report", type=Path, help="Lessons-learned Markdown report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the report validator and emit one stable JSON envelope."""

    args = parse_args(argv)
    path = args.report.resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        envelope: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "result": {
                "report": str(path),
                "lesson_count": 0,
                "lesson_ids": [],
                "diagnostics": [
                    asdict(
                        Diagnostic(
                            "input_error",
                            f"read the report as UTF-8: {exc.__class__.__name__}",
                            field="report",
                        )
                    )
                ],
            },
        }
        print(json.dumps(envelope, indent=2, sort_keys=True))
        return 2

    envelope = build_envelope(path, text)
    print(json.dumps(envelope, indent=2, sort_keys=True))
    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
