"""Audit JSONL evidence slices before extracting lessons."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, TypedDict

ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rate_limited", re.compile(r"\brate[- ]?limit(?:ed|s|ing)?\b", re.IGNORECASE)),
    ("canceled", re.compile(r"\bcancell?ed\b|\bcancelled\b", re.IGNORECASE)),
    (
        "length_limit",
        re.compile(r"length limit|hit the length|response was too long", re.IGNORECASE),
    ),
    (
        "no_response",
        re.compile(r"no response was returned|empty response", re.IGNORECASE),
    ),
    (
        "request_failed",
        re.compile(
            r"request failed|failed to process|generic request failed", re.IGNORECASE
        ),
    ),
)
FRUSTRATION_RE = re.compile(
    r"\b(redo|missed|again|wrong|poisoned|must|can't assume|cannot assume|failed twice|rate[- ]?limit)",
    re.IGNORECASE,
)
DEFAULT_PHRASES = (
    "missed history",
    "already implemented",
    "rate-limited",
    "length limit",
    "no response was returned",
    "canceled",
)
ELIGIBLE_SUFFIXES = frozenset({".json", ".jsonl"})
MARKDOWN_SAFE_PATH_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/:.-"
)


class ParseFailure(TypedDict):
    kind: str
    line: int
    column: int


class SessionSummary(TypedDict):
    session_key: str
    source_paths: list[str]
    source_family: str
    records: int
    turns: int
    user_turns: int
    assistant_turns: int
    error_turns: int
    error_categories: dict[str, int]
    empty: bool
    prompt_only: bool
    high_frustration: bool
    phrase_matches: dict[str, int]


class AuditReport(TypedDict):
    inputs: list[str]
    files_read: list[str]
    parse_failures: dict[str, ParseFailure]
    source_families: dict[str, int]
    sessions: int
    turns: int
    empty_sessions: int
    prompt_only_sessions: int
    error_turns: int
    error_categories: dict[str, int]
    high_frustration_sessions: list[str]
    duplicate_session_keys: dict[str, list[str]]
    phrase_matches: dict[str, int]
    session_summaries: list[SessionSummary]


@dataclass(slots=True)
class SessionAccumulator:
    """Counters retained for one session without retaining record text."""

    source_paths: set[str] = field(default_factory=set)
    records: int = 0
    turns: int = 0
    user_turns: int = 0
    assistant_turns: int = 0
    error_turns: int = 0
    error_categories: Counter[str] = field(default_factory=Counter)
    phrase_matches: Counter[str] = field(default_factory=Counter)
    frustration_hits: int = 0

    def add_record(
        self, source_path: str, record: Any, phrases: tuple[str, ...]
    ) -> None:
        """Add one parsed record and discard its reconstructed text immediately."""
        text = "\n".join(
            fragment.strip() for fragment in text_fragments(record) if fragment.strip()
        )
        role = record_role(record)
        categories = categories_for(text)
        phrase_matches = phrases_for(text, phrases)

        self.source_paths.add(source_path)
        self.records += 1
        self.turns += bool(text)
        self.user_turns += role in {"user", "human"}
        self.assistant_turns += role in {"assistant", "agent"}
        self.error_turns += bool(categories)
        self.error_categories.update(categories)
        self.phrase_matches.update(phrase_matches)
        self.frustration_hits += len(FRUSTRATION_RE.findall(text))

    def merge(self, other: SessionAccumulator) -> None:
        """Merge a successfully parsed file's transactional counters."""
        self.source_paths.update(other.source_paths)
        self.records += other.records
        self.turns += other.turns
        self.user_turns += other.user_turns
        self.assistant_turns += other.assistant_turns
        self.error_turns += other.error_turns
        self.error_categories.update(other.error_categories)
        self.phrase_matches.update(other.phrase_matches)
        self.frustration_hits += other.frustration_hits


class InputParseError(Exception):
    """Carry a bounded, value-free input diagnostic."""

    def __init__(self, kind: str, line: int, column: int) -> None:
        super().__init__(kind)
        self.diagnostic: ParseFailure = {"kind": kind, "line": line, "column": column}


def source_family(path: Path) -> str:
    """Classify a source file path into a broad chat/evidence family."""
    text = path.as_posix().casefold()
    if ".codex" in text or "/codex/" in text:
        return "codex_sessions"
    if "workspacestorage" in text or "github.copilot-chat" in text:
        return "vscode_copilot_storage"
    if ".copilot-retrospective" in text:
        return "retrospective_artifact"
    if path.suffix.casefold() == ".jsonl":
        return "exported_jsonl"
    return "manual_or_export"


def discover_input_files(
    paths: list[Path],
) -> tuple[list[Path], dict[str, ParseFailure]]:
    """Discover eligible files and reject explicit inputs that yield none."""
    found: list[Path] = []
    failures: dict[str, ParseFailure] = {}
    for input_path in paths:
        path = input_path.resolve()
        if path.is_dir():
            directory_files = [
                child
                for child in sorted(path.rglob("*"))
                if child.is_file() and child.suffix.casefold() in ELIGIBLE_SUFFIXES
            ]
            if directory_files:
                found.extend(directory_files)
            else:
                failures[str(path)] = {
                    "kind": "no_eligible_files",
                    "line": 0,
                    "column": 0,
                }
        elif path.is_file() and path.suffix.casefold() in ELIGIBLE_SUFFIXES:
            found.append(path)
        else:
            failures[str(path)] = {
                "kind": "unsupported_input",
                "line": 0,
                "column": 0,
            }
    files = sorted(dict.fromkeys(path.resolve() for path in found))
    return files, dict(sorted(failures.items()))


def iter_input_files(paths: list[Path]) -> list[Path]:
    """Return eligible files while preserving the legacy discovery helper API."""
    files, _ = discover_input_files(paths)
    return files


def _decode_json_record(raw_record: bytes, line_number: int) -> Any:
    """Decode one strict-UTF-8 JSON record with value-free failures."""
    try:
        text = raw_record.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InputParseError("invalid_utf8", line_number, exc.start + 1) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputParseError(
            "invalid_json", line_number + exc.lineno - 1, exc.colno
        ) from None
    except ValueError:
        raise InputParseError("invalid_json", line_number, 1) from None


def iter_jsonl_records(path: Path, max_record_bytes: int | None) -> Iterator[Any]:
    """Yield strict-UTF-8 JSONL records one at a time from a binary stream."""
    read_limit = max_record_bytes + 3 if max_record_bytes is not None else -1
    with path.open("rb") as stream:
        line_number = 0
        while True:
            raw_line = stream.readline(read_limit)
            if not raw_line:
                return
            line_number += 1
            payload = raw_line.removesuffix(b"\n").removesuffix(b"\r")
            if max_record_bytes is not None and len(payload) > max_record_bytes:
                raise InputParseError(
                    "record_too_large", line_number, max_record_bytes + 1
                )
            if not payload.strip():
                # Decode blank lines too so invalid UTF-8 cannot hide in whitespace.
                try:
                    payload.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise InputParseError(
                        "invalid_utf8", line_number, exc.start + 1
                    ) from None
                continue
            yield _decode_json_record(payload, line_number)


def _records_from_monolithic_json(path: Path, max_input_bytes: int) -> Iterator[Any]:
    """Yield records from an explicitly enabled, caller-bounded JSON document."""
    with path.open("rb") as stream:
        raw_document = stream.read(max_input_bytes + 1)
    if len(raw_document) > max_input_bytes:
        raise InputParseError("input_too_large", 1, max_input_bytes + 1)
    data = _decode_json_record(raw_document, 1)
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        for key in ("messages", "turns", "records", "items", "events"):
            value = data.get(key)
            if isinstance(value, list):
                yield from value
                return
    yield data


def text_fragments(value: Any) -> list[str]:
    """Extract likely human-readable text from flexible chat export records."""
    fragments: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        for item in value:
            fragments.extend(text_fragments(item))
        return fragments
    if not isinstance(value, dict):
        return fragments

    seen: set[str] = set()
    for key in (
        "text",
        "content",
        "message",
        "prompt",
        "response",
        "title",
        "error",
        "output",
    ):
        nested = value.get(key)
        if isinstance(nested, str):
            fragments.append(nested)
            seen.add(key)
        elif isinstance(nested, list | dict):
            fragments.extend(text_fragments(nested))
            seen.add(key)
    for key, nested in value.items():
        if key in seen:
            continue
        if key in {
            "id",
            "timestamp",
            "created_at",
            "updated_at",
            "createdAt",
            "updatedAt",
        }:
            continue
        fragments.extend(text_fragments(nested))
    return fragments


def record_role(record: Any) -> str:
    """Best-effort role extraction from common chat export shapes."""
    if not isinstance(record, dict):
        return "unknown"
    for key in ("role", "speaker", "sender"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    author = record.get("author")
    if isinstance(author, dict):
        value = author.get("role") or author.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return "unknown"


def session_key_for(path: Path, record: Any) -> str:
    """Return a stable session key from a record or its source file."""
    if isinstance(record, dict):
        for key in (
            "session_id",
            "sessionId",
            "conversation_id",
            "conversationId",
            "chat_session_id",
            "thread_id",
        ):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return path.stem


def categories_for(text: str) -> list[str]:
    """Return error categories found in a turn."""
    return [name for name, pattern in ERROR_PATTERNS if pattern.search(text)]


def phrases_for(text: str, phrases: tuple[str, ...]) -> list[str]:
    """Return configured phrase matches found in a turn."""
    lowered = text.casefold()
    return [phrase for phrase in phrases if phrase.casefold() in lowered]


def summarize_file(
    path: Path,
    phrases: tuple[str, ...],
    *,
    allow_monolithic_json: bool,
    max_input_bytes: int | None,
    max_record_bytes: int | None,
) -> tuple[dict[str, SessionAccumulator], ParseFailure | None]:
    """Transactionally summarize one file, discarding all counters on failure."""
    if path.suffix.casefold() == ".json" and not allow_monolithic_json:
        return {}, {"kind": "monolithic_json_refused", "line": 1, "column": 1}

    file_sessions: dict[str, SessionAccumulator] = {}
    try:
        if path.suffix.casefold() == ".jsonl":
            records = iter_jsonl_records(path, max_record_bytes)
        else:
            if max_input_bytes is None:
                raise ValueError("Monolithic JSON requires a positive max_input_bytes")
            records = _records_from_monolithic_json(path, max_input_bytes)
        source_path = str(path)
        for record in records:
            session_key = session_key_for(path, record)
            accumulator = file_sessions.setdefault(session_key, SessionAccumulator())
            accumulator.add_record(source_path, record, phrases)
    except InputParseError as exc:
        return {}, exc.diagnostic
    except OSError:
        return {}, {"kind": "io_error", "line": 0, "column": 0}
    return file_sessions, None


def build_report(
    input_paths: list[Path],
    phrases: tuple[str, ...],
    *,
    allow_monolithic_json: bool = False,
    max_input_bytes: int | None = None,
    max_record_bytes: int | None = None,
) -> AuditReport:
    """Build a deterministic coverage/failure report for evidence slices."""
    if allow_monolithic_json and (max_input_bytes is None or max_input_bytes <= 0):
        raise ValueError("allow_monolithic_json requires a positive max_input_bytes")
    if max_input_bytes is not None and not allow_monolithic_json:
        raise ValueError("max_input_bytes requires allow_monolithic_json")
    if max_input_bytes is not None and max_input_bytes <= 0:
        raise ValueError("max_input_bytes must be positive")
    if max_record_bytes is not None and max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be positive")

    files, failures = discover_input_files(input_paths)
    grouped: dict[str, SessionAccumulator] = {}
    for path in files:
        file_sessions, failure = summarize_file(
            path,
            phrases,
            allow_monolithic_json=allow_monolithic_json,
            max_input_bytes=max_input_bytes,
            max_record_bytes=max_record_bytes,
        )
        if failure is not None:
            failures[str(path)] = failure
            continue
        for session_key, file_accumulator in file_sessions.items():
            grouped.setdefault(session_key, SessionAccumulator()).merge(
                file_accumulator
            )

    session_summaries: list[SessionSummary] = []
    total_errors: Counter[str] = Counter()
    total_phrases: Counter[str] = Counter()
    source_families: Counter[str] = Counter(source_family(path) for path in files)
    high_frustration_sessions: list[str] = []
    for session_key, accumulator in sorted(grouped.items()):
        source_paths = sorted(accumulator.source_paths)
        high_frustration = (
            sum(accumulator.error_categories.values()) >= 3
            or accumulator.frustration_hits >= 3
        )
        if high_frustration:
            high_frustration_sessions.append(session_key)
        total_errors.update(accumulator.error_categories)
        total_phrases.update(accumulator.phrase_matches)
        session_summaries.append(
            {
                "session_key": session_key,
                "source_paths": source_paths,
                "source_family": source_family(Path(source_paths[0]))
                if source_paths
                else "unknown",
                "records": accumulator.records,
                "turns": accumulator.turns,
                "user_turns": accumulator.user_turns,
                "assistant_turns": accumulator.assistant_turns,
                "error_turns": accumulator.error_turns,
                "error_categories": dict(sorted(accumulator.error_categories.items())),
                "empty": accumulator.turns == 0,
                "prompt_only": accumulator.user_turns > 0
                and accumulator.assistant_turns == 0,
                "high_frustration": high_frustration,
                "phrase_matches": dict(sorted(accumulator.phrase_matches.items())),
            }
        )

    duplicate_keys = {
        summary["session_key"]: summary["source_paths"]
        for summary in session_summaries
        if len(summary["source_paths"]) > 1
    }
    return {
        "inputs": [str(path) for path in input_paths],
        "files_read": [str(path) for path in files],
        "parse_failures": failures,
        "source_families": dict(sorted(source_families.items())),
        "sessions": len(session_summaries),
        "turns": sum(session["turns"] for session in session_summaries),
        "empty_sessions": sum(1 for session in session_summaries if session["empty"]),
        "prompt_only_sessions": sum(
            1 for session in session_summaries if session["prompt_only"]
        ),
        "error_turns": sum(session["error_turns"] for session in session_summaries),
        "error_categories": dict(sorted(total_errors.items())),
        "high_frustration_sessions": high_frustration_sessions,
        "duplicate_session_keys": duplicate_keys,
        "phrase_matches": dict(sorted(total_phrases.items())),
        "session_summaries": session_summaries,
    }


def print_markdown(report: AuditReport) -> None:
    """Print a compact Markdown audit report."""
    print("# Evidence Slice Audit\n")
    print("## Coverage")
    print(f"- inputs: {len(report['inputs'])}")
    print(f"- files read: {len(report['files_read'])}")
    print(f"- sessions: {report['sessions']}")
    print(f"- turns: {report['turns']}")
    print(f"- source families: {report['source_families']}")
    print(f"- parse failures: {len(report['parse_failures'])}")
    for path, failure in report["parse_failures"].items():
        escaped_path = escape_path_for_markdown(path)
        print(
            f"- parse failure path={escaped_path}; kind={failure['kind']}; "
            f"line={failure['line']}; column={failure['column']}"
        )
    print("\n## Failure Classification")
    print(f"- empty sessions: {report['empty_sessions']}")
    print(f"- prompt-only sessions: {report['prompt_only_sessions']}")
    print(f"- error-bearing turns: {report['error_turns']}")
    print(f"- error categories: {report['error_categories']}")
    print(f"- high-frustration sessions: {report['high_frustration_sessions']}")
    print("\n## Duplicate Session Keys")
    if report["duplicate_session_keys"]:
        for key, paths in report["duplicate_session_keys"].items():
            print(f"- {key}: {paths}")
    else:
        print("- none")
    print("\n## Phrase Matches")
    print(f"- {report['phrase_matches']}")


def escape_path_for_markdown(path: str) -> str:
    """Percent-encode every UTF-8 path byte that could create Markdown syntax."""
    return "".join(
        chr(byte) if byte in MARKDOWN_SAFE_PATH_BYTES else f"%{byte:02X}"
        for byte in path.encode("utf-8", errors="surrogatepass")
    )


def positive_int(value: str) -> int:
    """Parse a caller-supplied positive byte limit."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit JSONL evidence slices before lesson extraction"
    )
    parser.add_argument("paths", nargs="+", help="JSONL files or directories to audit")
    parser.add_argument(
        "--phrase",
        action="append",
        default=[],
        help="Additional case-insensitive phrase to count in turn text",
    )
    parser.add_argument(
        "--allow-monolithic-json",
        action="store_true",
        help="Allow .json input only when --max-input-bytes is also supplied",
    )
    parser.add_argument(
        "--max-input-bytes",
        type=positive_int,
        help="Maximum bytes read from each explicitly allowed monolithic JSON input",
    )
    parser.add_argument(
        "--max-record-bytes",
        type=positive_int,
        help="Maximum bytes per JSONL record; caller-supplied because record sizes vary",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of Markdown"
    )
    args = parser.parse_args()

    if args.allow_monolithic_json and args.max_input_bytes is None:
        parser.error("--allow-monolithic-json requires --max-input-bytes")
    if args.max_input_bytes is not None and not args.allow_monolithic_json:
        parser.error("--max-input-bytes requires --allow-monolithic-json")

    paths = [Path(path).resolve() for path in args.paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        parser.error(f"Input paths do not exist: {', '.join(missing)}")
    phrases = tuple(dict.fromkeys((*DEFAULT_PHRASES, *args.phrase)))
    report = build_report(
        paths,
        phrases,
        allow_monolithic_json=args.allow_monolithic_json,
        max_input_bytes=args.max_input_bytes,
        max_record_bytes=args.max_record_bytes,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_markdown(report)
    return 1 if report["parse_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
