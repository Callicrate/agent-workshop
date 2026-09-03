"""Create and validate Databricks project status reports."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from string import Template
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FILENAME_RE = re.compile(r"^\d{8}T\d{6}-status\.md$")
MARKER_RE = re.compile(r"<!-- STATUS-REPORT:.*?-->", re.DOTALL)
PLACEHOLDER_RE = re.compile(r"\$[A-Z_]+|\$\{[A-Z_]+\}")
FENCED_CODE_RE = re.compile(r"^(```|~~~)[^\n]*\n[\s\S]*?^\1\s*$", re.MULTILINE)
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "status-report.md"
ALLOWED_HEALTH_STATES = {"healthy", "watch", "degraded", "critical", "unknown", "not applicable"}
ALLOWED_COVERAGE_STATES = {"complete", "partial", "blocked", "not applicable"}
ALLOWED_FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
ALLOWED_CONFIDENCE_STATES = {"high", "medium", "low"}
HEALTH_STATE_RANK = {"healthy": 0, "watch": 1, "degraded": 2, "critical": 3}
LATEST_WINDOW_BASIS = "latest complete days"
CUSTOM_WINDOW_BASIS_PREFIXES = ("user-specified:", "change-bounded:", "retention-limited:")
EXPECTED_SCORECARD_DIMENSIONS = {
    "Reliability / uptime",
    "Jobs / pipelines",
    "Tables / data quality",
    "Models / MLflow",
    "Serving",
    "Usage / performance / cost",
    "Security / operations",
}
CONTEXT_RECEIPT_VERSION = 1
CONTEXT_MAX_FILE_BYTES = 128 * 1024
REQUIRED_HEADINGS = (
    "# Databricks Project Status:",
    "## Report Metadata",
    "## Executive Summary",
    "## Scope And Source Coverage",
    "### Target Manifest",
    "### Evidence Coverage Ledger",
    "## Health Scorecard",
    "## Active Incidents And Failures",
    "## Reliability And Uptime",
    "## Jobs And Pipelines",
    "## Tables And Data Products",
    "## Models And MLflow",
    "## Serving Endpoints",
    "## Usage, Performance, And Cost",
    "## Security And Operational Readiness",
    "## Trends And Emerging Risks",
    "## Findings",
    "## Prioritized Recommendations",
    "## Unknowns And Evidence Gaps",
    "## Evidence Appendix",
)
REQUIRED_SUBSTANTIVE_SECTIONS = tuple(
    heading for heading in REQUIRED_HEADINGS if heading not in {"# Databricks Project Status:", "## Report Metadata"}
)


def parse_timestamp(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    compact = re.fullmatch(r"\d{8}T\d{6}", value)
    if compact:
        parsed = dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
        return parsed.replace(tzinfo=dt.timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def format_window(value: dt.datetime) -> str:
    return value.isoformat()


def complete_day_windows(
    generated_at: dt.datetime,
    review_days: int,
    timezone_name: str,
) -> tuple[dt.datetime, dt.datetime, dt.datetime, dt.datetime]:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone_name}") from exc
    local_now = generated_at.astimezone(timezone)
    review_end = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    review_start = review_end - dt.timedelta(days=review_days)
    baseline_end = review_start
    baseline_start = baseline_end - dt.timedelta(days=review_days)
    return review_start, review_end, baseline_start, baseline_end


def validate_profile(profile: str) -> str:
    normalized = profile.strip()
    if not normalized or normalized.casefold() == "unverified":
        raise ValueError("profile must be the explicit Databricks CLI profile verified during preflight")
    if any(character in normalized for character in ("\r", "\n", "`")):
        raise ValueError("profile must not contain newlines or backticks")
    return normalized


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("context receipt contains duplicate JSON keys")
        result[key] = value
    return result


def validated_https_host(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return f"https://{parsed.hostname}{port}"


def validated_principal(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 200 or not re.fullmatch(r"[A-Za-z0-9_.@-]+", candidate):
        return None
    return candidate


def context_receipt_metadata(context_file: Path, profile: str) -> str:
    try:
        if context_file.stat().st_size <= 0 or context_file.stat().st_size > CONTEXT_MAX_FILE_BYTES:
            raise ValueError
        raw = context_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        raise ValueError("context receipt is unavailable or exceeds the supported size") from None
    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicate_json_keys, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise ValueError("context receipt must be valid JSON with unique object keys") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), list):
        raise ValueError("context receipt must include a profiles list")
    matching = [item for item in payload["profiles"] if isinstance(item, dict) and item.get("profile") == profile]
    if len(matching) != 1:
        raise ValueError(f"context receipt must contain exactly one receipt for profile {profile}")
    receipt = matching[0]
    effective_context = receipt.get("effective_context")
    if not isinstance(effective_context, dict):
        raise ValueError(f"context receipt does not prove an effective host for profile {profile}")
    host = validated_https_host(effective_context.get("host"))
    version = effective_context.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != CONTEXT_RECEIPT_VERSION or effective_context.get("ok") is not True or effective_context.get("profile") != profile or host is None:
        raise ValueError(f"context receipt does not prove an effective host for profile {profile}")
    current_user = receipt.get("current_user")
    principal = current_user.get("principal") if isinstance(current_user, dict) else None
    if not isinstance(principal, dict) or current_user.get("ok") is not True or principal.get("valid") is not True:
        raise ValueError(f"context receipt does not prove a current-user principal for profile {profile}")
    principal_name = validated_principal(principal.get("userName")) or validated_principal(principal.get("id"))
    if principal_name is None:
        raise ValueError(f"context receipt does not prove a current-user principal for profile {profile}")
    return f"`{host}` / `{principal_name}`"


def git_metadata(project_root: Path) -> tuple[str, str, str]:
    def run(*arguments: str) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(project_root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False, ""
        return completed.returncode == 0, completed.stdout.strip()

    branch_ok, branch = run("rev-parse", "--abbrev-ref", "HEAD")
    commit_ok, commit = run("rev-parse", "HEAD")
    status_ok, status = run("status", "--porcelain=v1", "--untracked-files=normal", "--", ".")
    if not branch_ok or not commit_ok:
        unavailable = "Unavailable: not a Git worktree"
        return unavailable, unavailable, unavailable
    worktree_state = "dirty" if status else "clean" if status_ok else "Unavailable: Git status failed"
    return ("DETACHED" if branch == "HEAD" else branch), commit, worktree_state


def create_report(
    project_root: Path,
    project_name: str | None,
    profile: str,
    timestamp: str | None,
    review_days: int,
    window_timezone: str,
    context_file: Path | None = None,
) -> Path:
    if review_days < 1:
        raise ValueError("review_days must be at least 1")
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root must already exist and be a directory: {root}")
    profile = validate_profile(profile)
    workspace_context = context_receipt_metadata(context_file, profile) if context_file is not None else "Unknown until verified"
    generated_at = parse_timestamp(timestamp)
    repository_branch, repository_commit, repository_worktree = git_metadata(root)
    review_start, review_end, baseline_start, baseline_end = complete_day_windows(
        generated_at,
        review_days,
        window_timezone,
    )
    report_dir = root / "status-reports"
    if report_dir.is_symlink():
        raise ValueError(f"status-reports must not be a symbolic link: {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{generated_at.strftime('%Y%m%dT%H%M%S')}-status.md"
    if os.path.lexists(report_path):
        raise FileExistsError(f"Refusing to overwrite existing report: {report_path}")
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    rendered = template.substitute(
        PROJECT_NAME=project_name or root.name,
        REPORT_PATH=report_path.relative_to(root).as_posix(),
        GENERATED_AT=format_utc(generated_at),
        PROJECT_ROOT=root.as_posix(),
        REPOSITORY_BRANCH=repository_branch,
        REPOSITORY_COMMIT=repository_commit,
        REPOSITORY_WORKTREE=repository_worktree,
        PROFILE=profile,
        REVIEW_START=format_window(review_start),
        REVIEW_END=format_window(review_end),
        BASELINE_START=format_window(baseline_start),
        BASELINE_END=format_window(baseline_end),
        WINDOW_TIMEZONE=window_timezone,
    )
    rendered = rendered.replace("Unknown until verified", workspace_context, 1)
    report_path.write_text(rendered, encoding="utf-8", newline="\n")
    return report_path


def section_body(text: str, heading: str) -> str:
    level = len(heading) - len(heading.lstrip("#"))
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*$([\s\S]*?)(?=^#{{1,{level}}}\s|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


def metadata_value(text: str, label: str) -> str:
    match = re.search(rf"^- \*\*{re.escape(label)}:\*\* `([^`]*)`(?: UTC)?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def window_values(text: str, label: str) -> tuple[str, str]:
    match = re.search(
        rf"^- \*\*{re.escape(label)}:\*\* `([^`]*)` to `([^`]*)`\s*$",
        text,
        re.MULTILINE,
    )
    return (match.group(1).strip(), match.group(2).strip()) if match else ("", "")


def table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.fullmatch(r"\|?[\s|:-]+\|?", stripped):
            continue
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows[1:] if rows else []


def has_required_heading(text: str, heading: str) -> bool:
    if heading == "# Databricks Project Status:":
        return bool(re.search(r"^# Databricks Project Status:\s+\S.*$", text, re.MULTILINE))
    return bool(re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE))


def validate_coverage_ledger(text: str, problems: list[str]) -> None:
    rows = table_rows(section_body(text, "### Evidence Coverage Ledger"))
    if not rows:
        problems.append("evidence coverage ledger must contain at least one source row")
        return

    observed_sources: set[str] = set()
    counts_pattern = re.compile(r"^pages\s*=\s*(\d+)\s*;\s*raw\s*=\s*(\d+)\s*;\s*unique\s*=\s*(\d+)$", re.IGNORECASE)
    for row in rows:
        if len(row) != 6:
            problems.append("each evidence coverage row must contain exactly six columns")
            continue
        source, scope, counts, windows, coverage, limitation = row
        source_key = source.casefold()
        if not source or source_key in observed_sources:
            problems.append(f"evidence coverage source must be non-empty and unique: {source or '(missing)'}")
        observed_sources.add(source_key)

        coverage_state = coverage.casefold()
        if coverage_state not in ALLOWED_COVERAGE_STATES:
            problems.append(f"invalid evidence coverage state for {source}: {coverage}")
            continue
        if not scope or scope.casefold() == "unknown":
            problems.append(f"evidence coverage scope must be explicit for {source}")
        if coverage_state in {"complete", "partial"}:
            counts_match = counts_pattern.fullmatch(counts)
            if not counts_match:
                problems.append(f"evidence counts for {source} must use pages=<n>; raw=<n>; unique=<n>")
            else:
                pages, raw, unique = (int(value) for value in counts_match.groups())
                if pages < 1 or unique > raw:
                    problems.append(f"evidence counts are inconsistent for {source}")
            lowered_windows = windows.casefold()
            if "requested=" not in lowered_windows or "observed=" not in lowered_windows:
                problems.append(f"evidence window for {source} must record requested=<range>; observed=<range>")
        if coverage_state in {"partial", "blocked", "not applicable"} and len(limitation.strip()) < 12:
            problems.append(f"evidence limitation must explain the impact for {source}")


def validate_findings_table(text: str, problems: list[str]) -> None:
    rows = table_rows(section_body(text, "## Findings"))
    observed_ids: set[str] = set()
    for row in rows:
        if len(row) != 7:
            problems.append("each finding row must contain exactly seven columns")
            continue
        finding_id, severity, confidence, owner, evidence, recommendation, verification = row
        finding_key = finding_id.casefold()
        if not finding_id or finding_key in observed_ids:
            problems.append(f"finding IDs must be non-empty and unique: {finding_id or '(missing)'}")
        observed_ids.add(finding_key)
        if severity.casefold() not in ALLOWED_FINDING_SEVERITIES:
            problems.append(f"invalid finding severity for {finding_id}: {severity}")
        if confidence.casefold() not in ALLOWED_CONFIDENCE_STATES:
            problems.append(f"invalid finding confidence for {finding_id}: {confidence}")
        for label, value in (
            ("resource / owner", owner),
            ("evidence and impact", evidence),
            ("recommendation", recommendation),
            ("verification", verification),
        ):
            if len(value.strip()) < 4:
                problems.append(f"finding {finding_id or '(missing)'} lacks {label}")


def parse_report_datetime(value: str, label: str, problems: list[str]) -> dt.datetime | None:
    if not value:
        problems.append(f"missing report metadata: {label}")
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        problems.append(f"invalid ISO-8601 timestamp for {label}: {value}")
        return None
    if parsed.tzinfo is None:
        problems.append(f"timestamp must include an offset for {label}: {value}")
        return None
    return parsed


def validate_time_contract(text: str, path: Path, problems: list[str]) -> None:
    generated = parse_report_datetime(metadata_value(text, "Generated at"), "Generated at", problems)
    timezone_name = metadata_value(text, "Window timezone")
    window_basis = metadata_value(text, "Window basis")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        problems.append(f"invalid IANA window timezone: {timezone_name or '(missing)'}")
        timezone = None

    filename_timestamp = path.name.removesuffix("-status.md")
    try:
        filename_time = parse_timestamp(filename_timestamp)
    except ValueError:
        problems.append(f"filename contains an invalid timestamp: {filename_timestamp}")
        filename_time = None
    if generated and filename_time and generated.astimezone(dt.timezone.utc) != filename_time:
        problems.append("filename timestamp does not match Generated at")

    current_raw = window_values(text, "Current window")
    baseline_raw = window_values(text, "Baseline window")
    current = tuple(
        parse_report_datetime(value, f"Current window {position}", problems)
        for value, position in zip(current_raw, ("start", "end"), strict=True)
    )
    baseline = tuple(
        parse_report_datetime(value, f"Baseline window {position}", problems)
        for value, position in zip(baseline_raw, ("start", "end"), strict=True)
    )
    if timezone is None or any(value is None for value in (*current, *baseline)):
        return
    current_start, current_end = current
    baseline_start, baseline_end = baseline
    assert current_start and current_end and baseline_start and baseline_end
    localized = [value.astimezone(timezone) for value in (current_start, current_end, baseline_start, baseline_end)]
    if any(value.time() != dt.time.min for value in localized):
        problems.append("current and baseline windows must use complete local calendar-day boundaries")
    current_days = (localized[1].date() - localized[0].date()).days
    baseline_days = (localized[3].date() - localized[2].date()).days
    if current_days < 1 or baseline_days < 1:
        problems.append("current and baseline windows must have positive duration")
    if current_days != baseline_days:
        problems.append("current and baseline windows must have equal calendar-day lengths")
    if baseline_end != current_start:
        problems.append("baseline window must end where the current window starts")
    if generated and current_end > generated.astimezone(current_end.tzinfo):
        problems.append("current window must not end after report generation")
    normalized_basis = window_basis.casefold()
    if normalized_basis == LATEST_WINDOW_BASIS:
        if generated:
            expected_end = generated.astimezone(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
            if current_end != expected_end:
                problems.append("latest-complete-days window must end at the latest completed local midnight")
    elif any(normalized_basis.startswith(prefix) for prefix in CUSTOM_WINDOW_BASIS_PREFIXES):
        _, _, reason = window_basis.partition(":")
        if len(reason.strip()) < 8:
            problems.append("custom window basis must include a specific reason after the colon")
    else:
        problems.append(
            "window basis must be Latest complete days or a reasoned user-specified, change-bounded, or retention-limited basis"
        )


def substantive_text(body: str) -> str:
    body = MARKER_RE.sub("", body)
    kept: list[str] = []
    in_table = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            in_table = False
            continue
        if line.startswith("#"):
            continue
        if line.startswith("|"):
            if re.fullmatch(r"\|?[\s|:-]+\|?", line):
                continue
            if not in_table:
                in_table = True
                continue
        else:
            in_table = False
        kept.append(line)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def validate_report(report_path: Path, project_root: Path) -> list[str]:
    problems: list[str] = []
    path = report_path.resolve()
    root = project_root.resolve()
    if not root.is_dir():
        problems.append(f"project root must exist and be a directory: {root}")
    expected_parent = root / "status-reports"
    if path.parent != expected_parent:
        problems.append(f"report must be directly under the project root: {expected_parent}")
    if not FILENAME_RE.fullmatch(path.name):
        problems.append("filename must match YYYYMMDDTHHmmSS-status.md")
    if not path.is_file():
        problems.append("report file does not exist")
        return problems
    text = path.read_text(encoding="utf-8")
    structural_text = FENCED_CODE_RE.sub("", text)
    for heading in REQUIRED_HEADINGS:
        if not has_required_heading(structural_text, heading):
            problems.append(f"missing required heading: {heading}")
    if MARKER_RE.search(text):
        problems.append("remove all STATUS-REPORT template comments after filling the report")
    if PLACEHOLDER_RE.search(text):
        problems.append("unresolved template placeholder remains")
    if "Unknown until verified" in text:
        problems.append("replace scaffold metadata defaults with verified values or explicit access gaps")
    try:
        validate_profile(metadata_value(text, "Databricks profile"))
    except ValueError as exc:
        problems.append(str(exc))
    for label in ("Repository branch", "Repository commit"):
        if not metadata_value(text, label):
            problems.append(f"missing report metadata: {label}")
    repository_worktree = metadata_value(text, "Repository worktree")
    if repository_worktree not in {
        "clean",
        "dirty",
        "Unavailable: not a Git worktree",
        "Unavailable: Git status failed",
    }:
        problems.append("repository worktree metadata must be clean, dirty, or an explicit unavailable state")
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if sum(cell == "Unknown" for cell in cells) >= 4:
            problems.append(f"replace scaffold table row with evidence or explicit reasons: {cells[0]}")
    expected_root = f"- **Repository root:** `{root.as_posix()}`"
    if expected_root not in text:
        problems.append("report repository-root metadata does not match --project-root")
    expected_report_path = (
        f"- **Report path:** `{path.relative_to(root).as_posix()}`" if path.is_relative_to(root) else ""
    )
    if expected_report_path and expected_report_path not in text:
        problems.append("report path metadata does not match the report location")
    if "**Operation mode:** Inspect-only" not in text:
        problems.append("report must state inspect-only operation mode")
    status_match = re.search(
        r"^- \*\*Overall status:\*\* `([^`]*)`\s*$",
        structural_text,
        re.MULTILINE,
    )
    overall_status = status_match.group(1).strip().casefold() if status_match else ""
    if overall_status not in ALLOWED_HEALTH_STATES - {"not applicable"}:
        problems.append("overall status must be healthy, watch, degraded, critical, or unknown")
    scorecard_rows = table_rows(section_body(structural_text, "## Health Scorecard"))
    observed_dimensions: set[str] = set()
    observed_statuses: list[str] = []
    for row in scorecard_rows:
        if len(row) < 2:
            continue
        observed_dimensions.add(row[0])
        dimension_status = row[1].casefold()
        observed_statuses.append(dimension_status)
        if dimension_status not in ALLOWED_HEALTH_STATES:
            problems.append(f"invalid health scorecard status for {row[0]}: {row[1]}")
    missing_dimensions = sorted(EXPECTED_SCORECARD_DIMENSIONS - observed_dimensions)
    if missing_dimensions:
        problems.append(f"health scorecard is missing dimensions: {', '.join(missing_dimensions)}")
    ranked_statuses = [HEALTH_STATE_RANK[status] for status in observed_statuses if status in HEALTH_STATE_RANK]
    if ranked_statuses and max(ranked_statuses) > 0:
        if overall_status not in HEALTH_STATE_RANK or HEALTH_STATE_RANK[overall_status] < max(ranked_statuses):
            problems.append("overall status must not be healthier than the most severe scorecard dimension")
    if overall_status == "healthy" and (
        "unknown" in observed_statuses or not any(status == "healthy" for status in observed_statuses)
    ):
        problems.append("overall status cannot be healthy while material scorecard evidence is unknown")
    validate_coverage_ledger(structural_text, problems)
    validate_findings_table(structural_text, problems)
    validate_time_contract(text, path, problems)
    for heading in REQUIRED_SUBSTANTIVE_SECTIONS:
        if len(substantive_text(section_body(structural_text, heading))) < 40:
            problems.append(f"section lacks substantive evidence or an explicit not-applicable reason: {heading}")
    for heading in ("### Immediate", "### Near Term", "### Longer Term"):
        if len(substantive_text(section_body(structural_text, heading))) < 20:
            problems.append(f"recommendation subsection lacks content: {heading}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or validate a Databricks project status report")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a timestamped report scaffold")
    create.add_argument("--project-root", default=".", help="Project root that will own status-reports/")
    create.add_argument("--project-name", help="Display name; defaults to the project-root directory name")
    create.add_argument("--profile", required=True, help="Verified explicit Databricks CLI profile")
    create.add_argument("--timestamp", help="UTC ISO-8601 or YYYYMMDDTHHmmSS; defaults to now")
    create.add_argument("--review-days", type=int, default=30, help="Current and baseline window length")
    create.add_argument("--window-timezone", default="UTC", help="IANA timezone for complete calendar-day windows")
    create.add_argument("--context-file", help="Redacted databricks-api-calls context JSON for the explicit profile")

    validate = subparsers.add_parser("validate", help="Validate a completed status report")
    validate.add_argument("report", help="Path to the report Markdown file")
    validate.add_argument("--project-root", default=".", help="Project root that owns status-reports/")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "create":
        try:
            path = create_report(
                Path(args.project_root),
                args.project_name,
                args.profile,
                args.timestamp,
                args.review_days,
                args.window_timezone,
                Path(args.context_file) if args.context_file else None,
            )
        except (FileExistsError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(path)
        return 0

    problems = validate_report(Path(args.report), Path(args.project_root))
    print(
        json.dumps({"report": str(Path(args.report).resolve()), "valid": not problems, "problems": problems}, indent=2)
    )
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
