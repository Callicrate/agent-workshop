#!/usr/bin/env python3
"""Safely classify Python tracebacks without echoing their sensitive payloads."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from diagnostic_safety import (
    RedactionMetadata,
    combine_metadata,
    error_envelope,
    read_stdin_bounded,
    read_utf8_bounded,
    safe_path,
    sanitize_text,
)

ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "error-cheatsheet.json"
MAX_TRACEBACK_BYTES = 1_048_576
_EXCEPTION_NAME = r"(?:[A-Za-z_]\w*\.)*[A-Z][A-Za-z0-9_]*(?:Error|ExceptionGroup|Exception|Warning|Exit|Interrupt|Failure)"
_HEADER = re.compile(rf"^\s*(?P<type>{_EXCEPTION_NAME})(?::\s*(?P<message>.*))?\s*$")
_WARNING_LOCATION = re.compile(rf"^.+?:\d+(?::\d+)?:\s*(?P<type>{_EXCEPTION_NAME})(?::\s*(?P<message>.*))?\s*$")
_SOURCE_OR_NOISE = re.compile(
    r"^(?:\s*File \"|\s*\^|\s*Traceback \(|\s*During handling|\s*The above exception|"
    r"\s*\+-[-+]+|\s*\|\s*-+|\s*=+|\s*-+\s*(?:FAILURES|warnings summary|short test summary|test session starts)|"
    r"\s*E\s+|\s*FAILED\s|\s*PASSED\s|\s*={2,}).*",
    re.IGNORECASE,
)
_GROUP_SEPARATOR = re.compile(r"^\s*(?:\|\s*)?\+-+\s*\d+\s*-+\s*$")
_NOTE = re.compile(r"^\s*(?:note|notes?)\s*:\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ExceptionCandidate:
    error_type: str
    error_message: str
    position: int
    is_warning: bool


@dataclass
class TracebackAnalysis:
    """Sanitized deterministic summary of a traceback-like diagnostic."""

    error_type: str
    error_message: str
    file_path: str | None
    line_number: int | None
    code_context: None
    likely_causes: list[str]
    suggested_fixes: list[str]
    related_patterns: list[str]
    exception_chain: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    redaction: dict[str, int | bool] = field(default_factory=dict)


def load_error_patterns(asset_path: Path = ASSET_PATH) -> list[dict[str, object]]:
    """Load canonical error patterns, rejecting malformed asset shapes."""
    try:
        data = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("could not load error-pattern asset") from exc
    if not isinstance(data, dict):
        raise ValueError("error-pattern asset has invalid shape")
    patterns: list[dict[str, object]] = []
    for section_name, section in data.items():
        if not isinstance(section_name, str) or not isinstance(section, dict):
            continue
        for pattern_key, details in section.items():
            if not isinstance(pattern_key, str) or not isinstance(details, dict):
                continue
            pattern = details.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                continue
            causes = details.get("causes")
            fixes = details.get("fixes")
            quick_fix = details.get("quick_fix")
            suggested = [quick_fix] if isinstance(quick_fix, str) and quick_fix else []
            if isinstance(fixes, list):
                suggested.extend(item for item in fixes if isinstance(item, str) and item)
            patterns.append(
                {
                    "section": section_name,
                    "name": pattern_key,
                    "pattern": pattern,
                    "causes": [item for item in causes if isinstance(item, str) and item] if isinstance(causes, list) else [],
                    "fixes": suggested,
                }
            )
    return patterns


def unique_preserve_order(items: list[str]) -> list[str]:
    """Return unique non-empty values while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _append_message(candidate: ExceptionCandidate, line: str) -> ExceptionCandidate:
    message = f"{candidate.error_message} {line}".strip()
    return ExceptionCandidate(candidate.error_type, message, candidate.position, candidate.is_warning)


def parse_traceback(text: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Extract exception headers, chain order, notes, and a safe location.

    The last exception header wins. A warning is selected only if no exception
    header exists. Source lines, group separators, and pytest summaries never
    become message continuations or notes.
    """
    candidates: list[ExceptionCandidate] = []
    notes: list[str] = []
    last_candidate_index: int | None = None
    active_notes = False
    skip_frame_source = False
    for position, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if raw_line.lstrip().startswith('File "'):
            skip_frame_source = True
            active_notes = False
            continue
        if skip_frame_source:
            # A traceback frame's next physical line is source, never a message.
            skip_frame_source = False
            active_notes = False
            continue
        if not line or _SOURCE_OR_NOISE.match(raw_line) or _GROUP_SEPARATOR.match(raw_line):
            active_notes = False
            continue
        note_match = _NOTE.match(raw_line)
        if note_match:
            notes.append(note_match.group(1))
            active_notes = True
            continue
        if active_notes and raw_line[:1].isspace() and not _HEADER.match(raw_line):
            notes.append(line)
            continue
        candidate_line = re.sub(r"^\s*\|\s?", "", raw_line)
        match = _HEADER.match(candidate_line) or _WARNING_LOCATION.match(candidate_line)
        if match:
            error_type = match.group("type")
            message = match.group("message") or ""
            candidate = ExceptionCandidate(
                error_type=error_type,
                error_message=message,
                position=position,
                is_warning=error_type.lower().endswith("warning"),
            )
            candidates.append(candidate)
            last_candidate_index = len(candidates) - 1
            active_notes = False
            continue
        if last_candidate_index is not None and not raw_line[:1].isspace():
            # PEP 678 renders exception notes as plain lines after the exception.
            notes.append(line)
            active_notes = True
            continue
        if last_candidate_index is not None and raw_line[:1].isspace() and not raw_line.lstrip().startswith(("|", "+")):
            candidates[last_candidate_index] = _append_message(candidates[last_candidate_index], line)

    selected = next((item for item in reversed(candidates) if not item.is_warning), None)
    if selected is None:
        selected = candidates[-1] if candidates else ExceptionCandidate("Unknown", "", -1, False)

    location_match = None
    for match in re.finditer(r'File "([^"]+)", line (\d+)', text):
        location_match = match
    file_path = safe_path(location_match.group(1), repo_root=repo_root) if location_match else None
    line_number = int(location_match.group(2)) if location_match else None
    return {
        "error_type": selected.error_type,
        "error_message": selected.error_message,
        "file_path": file_path,
        "line_number": line_number,
        "code_context": None,
        "exception_chain": [
            {"error_type": item.error_type, "error_message": item.error_message}
            for item in candidates
        ],
        "notes": notes,
    }


def _sanitize_list(items: list[str]) -> tuple[list[str], RedactionMetadata]:
    sanitized = [sanitize_text(item) for item in items]
    return [item[0] for item in sanitized], combine_metadata(*(item[1] for item in sanitized))


def analyze(traceback_text: str, *, repo_root: Path | None = None) -> TracebackAnalysis:
    """Analyze a traceback and return only privacy-safe output fields."""
    parsed = parse_traceback(traceback_text, repo_root=repo_root)
    error_type, type_meta = sanitize_text(parsed["error_type"], limit=256)
    error_message, message_meta = sanitize_text(parsed["error_message"])
    causes: list[str] = []
    fixes: list[str] = []
    related_patterns: list[str] = []
    error_line = f"{error_type}: {error_message}".strip()
    for diagnostics in load_error_patterns():
        pattern = cast(str, diagnostics["pattern"])
        try:
            matched = re.search(pattern, error_line, re.IGNORECASE) is not None
        except re.error:
            matched = False
        if matched:
            causes.extend(cast(list[str], diagnostics["causes"]))
            fixes.extend(cast(list[str], diagnostics["fixes"]))
            related_patterns.append(cast(str, diagnostics["name"]))
    if not causes:
        causes = ["No known pattern matched. Inspect the last user-code frame and the upstream value contract."]
        fixes = [
            "Inspect the producing call or lookup before adding a fallback.",
            "Capture a bounded type, shape, key, or length summary at the failing boundary.",
        ]
    causes, causes_meta = _sanitize_list(unique_preserve_order(causes))
    fixes, fixes_meta = _sanitize_list(unique_preserve_order(fixes))
    chain: list[dict[str, str]] = []
    metadata_items = [type_meta, message_meta, causes_meta, fixes_meta]
    for item in cast(list[dict[str, str]], parsed["exception_chain"]):
        safe_type, safe_type_meta = sanitize_text(item["error_type"], limit=256)
        safe_message, safe_message_meta = sanitize_text(item["error_message"])
        metadata_items.extend([safe_type_meta, safe_message_meta])
        chain.append({"error_type": safe_type, "error_message": safe_message})
    safe_notes, notes_meta = _sanitize_list(cast(list[str], parsed["notes"]))
    metadata_items.append(notes_meta)
    return TracebackAnalysis(
        error_type=error_type,
        error_message=error_message,
        file_path=cast(str | None, parsed["file_path"]),
        line_number=cast(int | None, parsed["line_number"]),
        code_context=None,
        likely_causes=causes,
        suggested_fixes=fixes,
        related_patterns=unique_preserve_order(related_patterns),
        exception_chain=chain,
        notes=safe_notes,
        redaction=combine_metadata(*metadata_items).as_dict(),
    )


def format_analysis(analysis: TracebackAnalysis) -> str:
    """Format a safe, intentionally source-free human summary."""
    output = ["TRACEBACK ANALYSIS", f"Error Type: {analysis.error_type}", f"Message: {analysis.error_message}"]
    if analysis.file_path:
        output.append(f"Location: {analysis.file_path}:{analysis.line_number}")
    if analysis.related_patterns:
        output.append(f"Pattern: {', '.join(analysis.related_patterns)}")
    output.append("Likely Causes:")
    output.extend(f"  {index}. {cause}" for index, cause in enumerate(analysis.likely_causes, 1))
    output.append("Suggested Fixes:")
    output.extend(f"  {index}. {fix}" for index, fix in enumerate(analysis.suggested_fixes, 1))
    if analysis.notes:
        output.append("Notes:")
        output.extend(f"  - {note}" for note in analysis.notes)
    return "\n".join(output)


def success_envelope(analysis: TracebackAnalysis) -> dict[str, Any]:
    return {"ok": True, "schema_version": "1.0", "tool": "analyze_traceback", "error": None, "analysis": asdict(analysis)}


def failure_envelope(code: str, message: object) -> dict[str, Any]:
    return {"ok": False, "schema_version": "1.0", "tool": "analyze_traceback", "error": error_envelope(code, message), "analysis": None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a Python traceback safely")
    parser.add_argument("file", nargs="?", help="UTF-8 traceback text file, or - for stdin")
    parser.add_argument("--json", action="store_true", help="Output a stable JSON envelope")
    parser.add_argument("--repo-root", type=Path, help="Allow repo-relative locations under this root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    traceback_text = ""
    try:
        if args.file and args.file != "-":
            traceback_text = read_utf8_bounded(Path(args.file), max_bytes=MAX_TRACEBACK_BYTES)
        else:
            traceback_text = read_stdin_bounded(max_bytes=MAX_TRACEBACK_BYTES)
        if not traceback_text.strip():
            raise ValueError("no traceback text was provided")
        analysis = analyze(traceback_text, repo_root=args.repo_root)
        envelope = success_envelope(analysis)
    except FileNotFoundError:
        envelope = failure_envelope("input_not_found", "input file was not found")
    except UnicodeDecodeError:
        envelope = failure_envelope("invalid_utf8", "input is not valid UTF-8")
    except json.JSONDecodeError:
        envelope = failure_envelope("invalid_json", "diagnostic asset is not valid JSON")
    except ValueError as exc:
        envelope = failure_envelope("invalid_input", exc)
    except OSError:
        envelope = failure_envelope("read_error", "input could not be read")
    except (MemoryError, RecursionError):
        envelope = failure_envelope("resource_error", "resource limit exceeded while processing input")
    except Exception:
        envelope = failure_envelope("internal_error", "diagnostic processing failed")
    if args.json:
        print(json.dumps(envelope, sort_keys=True))
    elif envelope["ok"]:
        print(format_analysis(cast(TracebackAnalysis, analysis)))
    else:
        error = cast(dict[str, Any], envelope["error"])
        print(f"ERROR: {error['code']}: {error['message']}")
    return 0 if envelope["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
