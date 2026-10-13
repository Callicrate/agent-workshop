"""Validate a skill directory against the skill contract."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from project_specific import (
    MarkerMissingError,
    MarkerReadError,
    PROJECT_MARKER_LINK,
    PROJECT_MARKER_NAME,
    parse_marker_bytes,
    read_marker_bytes,
    resolve_marker_project_path,
)
from inventory_skills import (
    discovery_contract_issues,
    parse_frontmatter,
)

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
PLACEHOLDER_TARGET_RE = re.compile(r"(^|/)(relative/path|path/to|example|placeholder)(\.|/|$)")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")

REQUIRED_HEADINGS = (
    "When to Use",
    "When NOT to Use",
    "Workflow",
    "Deterministic Tools",
    "References",
)

WARN_LINE_BUDGET = 150
ERROR_LINE_BUDGET = 250
NAME_MAX_LENGTH = 64


@dataclass(frozen=True)
class Issue:
    severity: str
    message: str


@dataclass(frozen=True)
class MarkdownLink:
    """One supported Markdown link destination or an explicit parse failure."""

    target: str | None
    error: str | None


@dataclass(frozen=True)
class LocalTarget:
    """A lexical, contained local target that is safe to probe."""

    components: tuple[str, ...]
    placeholder: bool


ExternalTarget = Literal["external", "fragment"]


def normalize_heading(text: str) -> str:
    """Normalize a markdown heading for loose matching."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def has_heading(headings: list[str], expected: str) -> bool:
    """Return True when a required heading exists."""
    expected_norm = normalize_heading(expected)
    return any(normalize_heading(heading).startswith(expected_norm) for heading in headings)


def collect_headings(text: str) -> list[str]:
    """Collect markdown headings from a skill file."""
    return [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("##")]


def resolve_target(target: str) -> tuple[Path, Path]:
    """Return the skill directory and SKILL.md path for the target."""
    path = Path(target)
    if path.is_dir():
        skill_dir = path.resolve()
        skill_md = skill_dir / "SKILL.md"
    else:
        skill_dir = path.parent.resolve()
        skill_md = skill_dir / path.name

    return skill_dir, skill_md


def has_required_project_marker_link(text: str) -> bool:
    """Return whether the exact marker link is the first nonblank body line."""
    frontmatter_match = FRONTMATTER_RE.match(text)
    if not frontmatter_match:
        return False
    for line in text[frontmatter_match.end() :].splitlines():
        if line.strip():
            return line == PROJECT_MARKER_LINK
    return False


def has_only_required_project_marker_link(text: str) -> bool:
    """Return whether the reserved marker literal appears once at its required position."""
    return text.count(PROJECT_MARKER_LINK) == 1 and has_required_project_marker_link(text)


def validate_project_specific_skill(skill_dir: Path, text: str) -> list[Issue]:
    """Validate an optional project-specific skill identity marker."""
    marker_path = skill_dir / PROJECT_MARKER_NAME
    marker_link_present = has_required_project_marker_link(text)
    marker_link_count = text.count(PROJECT_MARKER_LINK)
    try:
        marker_bytes = read_marker_bytes(marker_path)
    except MarkerMissingError:
        if marker_link_count:
            return [
                Issue(
                    "error",
                    "project-specific-skill is reserved for a marked skill at the required body position",
                )
            ]
        return []
    except MarkerReadError as exc:
        return [Issue("error", str(exc))]

    issues: list[Issue] = []
    if not marker_link_present:
        issues.append(Issue("error", "project-specific skill must link [project-specific-skill](project-specific-skill)"))
    if marker_link_count != 1:
        issues.append(Issue("error", "project-specific-skill link must appear exactly once"))

    project_path_text, marker_error = parse_marker_bytes(marker_bytes)
    if marker_error:
        return issues + [Issue("error", marker_error)]
    if project_path_text is None:
        return issues + [Issue("error", "project-specific-skill must contain exactly one path")]

    _, project_slug, project_error = resolve_marker_project_path(project_path_text)
    if project_error:
        return issues + [Issue("error", project_error)]
    if project_slug is None:
        return issues + [Issue("error", "project-specific-skill project directory name has no valid ASCII slug")]
    if not skill_dir.name.startswith(f"{project_slug}-"):
        return issues + [
            Issue(
                "error",
                f"project-specific skill folder must start with '{project_slug}-'",
            )
        ]
    return issues


def display_path(skill_dir: Path, path: Path) -> str:
    """Return a stable relative path for issue output."""
    return path.relative_to(skill_dir).as_posix()


def _is_escaped(text: str, index: int) -> bool:
    """Return whether the character at index has an odd escaped backslash run."""
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def mask_markdown_code(text: str) -> str:
    """Mask fenced and inline code without applying a backtracking Markdown regex.

    This intentionally supports only the small Markdown subset used by the link
    validator. It is linear in the document size and leaves normal prose intact.
    """
    masked = list(text)
    offset = 0
    fence_character: str | None = None
    fence_length = 0

    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = FENCE_OPEN_RE.match(body)
        if fence_character is None:
            if match:
                marker = match.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                for index in range(offset, offset + len(line)):
                    masked[index] = " "
        else:
            for index in range(offset, offset + len(line)):
                masked[index] = " "
            if match:
                marker = match.group(1)
                if marker[0] == fence_character and len(marker) >= fence_length:
                    fence_character = None
                    fence_length = 0
        offset += len(line)

    index = 0
    while index < len(text):
        if masked[index] != "`" or _is_escaped(text, index):
            index += 1
            continue
        run_end = index
        while run_end < len(text) and masked[run_end] == "`":
            run_end += 1
        delimiter = text[index:run_end]
        close = text.find(delimiter, run_end)
        if close < 0:
            index = run_end
            continue
        for masked_index in range(index, close + len(delimiter)):
            masked[masked_index] = " "
        index = close + len(delimiter)
    return "".join(masked)


def _parse_destination(payload: str) -> MarkdownLink:
    """Parse one intentionally narrow Markdown destination syntax.

    Angle-bracket destinations are supported. Link titles are explicitly not,
    because accepting them would require a broader Markdown grammar and creates
    ambiguity between a portable path and title text.
    """
    destination = payload.strip()
    if not destination:
        return MarkdownLink(None, "empty Markdown link destination")
    if destination.startswith("<"):
        closing = destination.find(">", 1)
        if closing < 0:
            return MarkdownLink(None, "unterminated angle-bracket Markdown link destination")
        if closing != len(destination) - 1:
            return MarkdownLink(None, "Markdown link titles are unsupported; use a destination only")
        target = destination[1:closing]
        if not target:
            return MarkdownLink(None, "empty Markdown link destination")
        return MarkdownLink(target, None)
    if any(character.isspace() for character in destination):
        return MarkdownLink(None, "Markdown link titles are unsupported; use a destination only")
    return MarkdownLink(destination, None)


def _find_closing_bracket(text: str, start: int) -> int | None:
    """Find a balanced unescaped closing square bracket from one label start."""
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _find_closing_paren(text: str, start: int) -> int | None:
    """Find a bounded nested parenthesis close for one inline destination."""
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _reference_definition_destination(line: str) -> MarkdownLink | None:
    """Return a reference-definition destination from one unfenced line."""
    index = 0
    while index < len(line) and line[index] in " \t":
        index += 1
    if index > 3 or index >= len(line) or line[index] != "[" or _is_escaped(line, index):
        return None
    closing = _find_closing_bracket(line, index)
    if closing is None or closing + 1 >= len(line) or line[closing + 1] != ":":
        return None
    destination_start = closing + 2
    while destination_start < len(line) and line[destination_start] in " \t":
        destination_start += 1
    if destination_start == len(line):
        return MarkdownLink(None, "empty Markdown reference definition destination")
    return _parse_destination(line[destination_start:].rstrip("\r\n"))


def collect_markdown_links(text: str) -> list[MarkdownLink]:
    """Collect inline links/images and reference definitions from a safe subset.

    Code fences, inline-code spans, and escaped examples are intentionally masked
    first. The scanner advances monotonically and never invokes a general-purpose
    Markdown parser or unbounded backtracking regular expression.
    """
    masked = mask_markdown_code(text)
    links: list[MarkdownLink] = []

    for line in masked.splitlines(keepends=True):
        definition = _reference_definition_destination(line)
        if definition is not None:
            links.append(definition)

    index = 0
    while index < len(masked):
        if masked[index] != "[" or _is_escaped(masked, index):
            index += 1
            continue
        closing_bracket = _find_closing_bracket(masked, index)
        if closing_bracket is None:
            break
        paren_start = closing_bracket + 1
        if paren_start >= len(masked) or masked[paren_start] != "(":
            index = closing_bracket + 1
            continue
        closing_paren = _find_closing_paren(masked, paren_start)
        if closing_paren is None:
            links.append(MarkdownLink(None, "unterminated inline Markdown link destination"))
            break
        links.append(_parse_destination(masked[paren_start + 1 : closing_paren]))
        index = closing_paren + 1
    return links


def classify_local_target(raw_target: str, source_components: tuple[str, ...]) -> LocalTarget | ExternalTarget | str:
    """Classify a raw destination before any target filesystem operation.

    A string return is an unsafe-syntax reason. Callers must not resolve, stat, or
    otherwise probe a target unless this returns ``LocalTarget``.
    """
    lower_target = raw_target.casefold()
    if lower_target.startswith(("http://", "https://", "mailto:", "copilot-skill:")):
        return "external"

    path_text, _, _ = raw_target.partition("#")
    if not path_text:
        return "fragment"
    if any(ord(character) < 32 or ord(character) == 127 for character in path_text):
        return "control character in local link target"
    if "%" in path_text:
        return "percent-encoded local link targets are unsupported"
    if "\\" in path_text:
        return "backslash, UNC, and device local link targets are unsupported"
    if path_text.startswith("/"):
        return "absolute or UNC local link target"
    if ":" in path_text:
        return "drive-qualified, device, or ADS local link target"
    if path_text.startswith("~"):
        return "home-relative local link target"
    if "//" in path_text:
        return "empty local link path segment"

    components = list(source_components)
    for segment in path_text.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if not components:
                return "lexical escape beyond skill root"
            components.pop()
            continue
        if not segment:
            return "empty local link path segment"
        if segment.endswith((".", " ")):
            return "non-portable trailing dot or space in local link target"
        components.append(segment)

    return LocalTarget(tuple(components), bool(PLACEHOLDER_TARGET_RE.search(path_text)))


def _reparse_or_symlink(status: os.stat_result) -> bool:
    """Return whether an entry is a symlink or Windows reparse point."""
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & reparse_point)


def _contained_regular_file_issue(skill_dir: Path, target: LocalTarget, raw_target: str, context: str) -> Issue | None:
    """Check exact-case, contained regular-file existence after lexical approval.

    The outer root has already been resolved and may itself have been a symlink.
    Every component below that root is enumerated and lstat'd without following a
    linked component, which prevents a packaged resource from escaping through an
    internal symlink or Windows reparse point.
    """
    if target.placeholder:
        return None
    current = skill_dir
    for index, component in enumerate(target.components):
        try:
            with os.scandir(current) as entries:
                exact_entry = None
                case_mismatch = False
                for entry in entries:
                    if entry.name == component:
                        exact_entry = entry
                        break
                    if entry.name.casefold() == component.casefold():
                        case_mismatch = True
        except OSError as exc:
            return Issue("error", f"unable to inspect linked resource {raw_target}: {exc.strerror or exc}")

        if exact_entry is None:
            if case_mismatch:
                return Issue("error", f"linked resource case mismatch: {raw_target}")
            return Issue("error", f"missing {context}: {raw_target}")

        entry_path = current / component
        try:
            entry_status = os.lstat(entry_path)
        except OSError as exc:
            return Issue("error", f"unable to inspect linked resource {raw_target}: {exc.strerror or exc}")
        if _reparse_or_symlink(entry_status):
            return Issue("error", f"linked resource uses internal symlink or reparse point: {raw_target}")
        if index < len(target.components) - 1:
            if not stat.S_ISDIR(entry_status.st_mode):
                return Issue("error", f"linked resource component is not a directory: {raw_target}")
        elif not stat.S_ISREG(entry_status.st_mode):
            return Issue("error", f"linked resource is not a regular file: {raw_target}")
        current = entry_path
    return None


def _validate_links_from_text(
    skill_dir: Path,
    text: str,
    source_components: tuple[str, ...],
    context: str,
) -> list[Issue]:
    """Validate supported Markdown links from one Markdown source document."""
    issues: list[Issue] = []
    for markdown_link in collect_markdown_links(text):
        if markdown_link.error:
            issues.append(Issue("error", markdown_link.error))
            continue
        assert markdown_link.target is not None
        raw_target = markdown_link.target
        classification = classify_local_target(raw_target, source_components)
        if classification in ("external", "fragment"):
            continue
        if isinstance(classification, str):
            issues.append(Issue("error", f"unsafe linked resource {raw_target}: {classification}"))
            continue
        issue = _contained_regular_file_issue(skill_dir, classification, raw_target, context)
        if issue is not None:
            issues.append(issue)
    return issues


def validate_relative_links(skill_dir: Path, text: str) -> list[Issue]:
    """Validate SKILL.md links against contained, portable regular files."""
    issues = _validate_links_from_text(skill_dir, text, (), "linked resource")
    if PROJECT_MARKER_LINK in text and not has_only_required_project_marker_link(text):
        issues.append(
            Issue(
                "error",
                "project-specific-skill link is reserved for the required marked-skill position",
            )
        )
    return issues


def validate_markdown_resource_links(skill_dir: Path) -> list[Issue]:
    """Validate resource Markdown links relative to their source within the skill."""
    issues: list[Issue] = []
    for markdown_path in sorted(skill_dir.rglob("*.md")):
        if markdown_path.name == "SKILL.md":
            continue
        relative_markdown_path = markdown_path.relative_to(skill_dir)
        try:
            source_status = os.lstat(markdown_path)
        except OSError as exc:
            issues.append(
                Issue("error", f"unable to inspect markdown {display_path(skill_dir, markdown_path)}: {exc.strerror or exc}")
            )
            continue
        if _reparse_or_symlink(source_status) or not stat.S_ISREG(source_status.st_mode):
            issues.append(Issue("error", f"markdown resource is not a contained regular file: {relative_markdown_path.as_posix()}"))
            continue
        try:
            text = markdown_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            issues.append(
                Issue("error", f"unable to read markdown {display_path(skill_dir, markdown_path)}: {exc.reason}")
            )
            continue
        issues.extend(
            _validate_links_from_text(
                skill_dir,
                text,
                relative_markdown_path.parent.parts,
                f"linked resource in {relative_markdown_path.as_posix()}",
            )
        )
    return issues


def validate_resource_directories(skill_dir: Path) -> list[Issue]:
    """Warn when optional resource directories exist but contain no files."""
    issues: list[Issue] = []
    for directory_name in ("assets", "scripts", "templates", "agents"):
        directory = skill_dir / directory_name
        if not directory.exists() or not directory.is_dir():
            continue

        if not any(path.is_file() for path in directory.rglob("*")):
            issues.append(Issue("warning", f"empty resource directory: {directory_name}"))
    return issues


def validate_python_scripts(skill_dir: Path) -> list[Issue]:
    """Validate Python helper scripts for syntax errors."""
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists():
        return []

    issues: list[Issue] = []
    for script_path in sorted(scripts_dir.rglob("*.py")):
        relative_path = display_path(skill_dir, script_path)
        try:
            source = script_path.read_text(encoding="utf-8")
            compile(source, relative_path, "exec")
        except SyntaxError as exc:
            line = exc.lineno or 0
            column = exc.offset or 0
            issues.append(
                Issue(
                    "error",
                    f"python syntax error in {relative_path}:{line}:{column}: {exc.msg}",
                )
            )
        except UnicodeDecodeError as exc:
            issues.append(Issue("error", f"unable to read script {relative_path}: {exc.reason}"))
    return issues



def validate_shell_scripts(skill_dir: Path) -> list[Issue]:
    """Validate shell helper scripts for line endings and basic Bash syntax when Bash is available."""
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists():
        return []

    issues: list[Issue] = []
    bash_path = shutil.which("bash")
    for script_path in sorted(list(scripts_dir.rglob("*.sh")) + list(scripts_dir.rglob("*.bash"))):
        relative_path = display_path(skill_dir, script_path)
        try:
            data = script_path.read_bytes()
        except OSError as exc:
            issues.append(Issue("error", f"unable to read shell script {relative_path}: {exc}"))
            continue
        if b"\r\n" in data:
            issues.append(Issue("error", f"shell script uses CRLF line endings: {relative_path}"))
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(Issue("error", f"unable to decode shell script {relative_path}: {exc.reason}"))
            continue
        if "\x00" in source:
            issues.append(Issue("error", f"shell script contains NUL bytes: {relative_path}"))
        if bash_path:
            try:
                result = subprocess.run(
                    [bash_path, "-n"],
                    input=data,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                issues.append(Issue("error", f"shell syntax check timed out: {relative_path}"))
            else:
                if result.returncode != 0:
                    stderr = result.stderr.decode("utf-8", errors="replace").strip()
                    detail = stderr.splitlines()[-1] if stderr else "bash -n failed"
                    issues.append(Issue("error", f"shell syntax error in {relative_path}: {detail}"))
    return issues

def validate_skill(target: str) -> list[Issue]:
    """Validate one skill directory or SKILL.md file."""
    skill_dir, skill_md = resolve_target(target)
    issues: list[Issue] = []

    try:
        skill_entry = os.lstat(skill_md)
    except FileNotFoundError:
        return [Issue("error", f"SKILL.md not found: {skill_md}")]
    except OSError as exc:
        return [Issue("error", f"unable to inspect SKILL.md: {exc.strerror or exc}")]
    if _reparse_or_symlink(skill_entry) or not stat.S_ISREG(skill_entry.st_mode):
        return [Issue("error", "SKILL.md must be a regular file, not a symlink or reparse point")]

    text = skill_md.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    headings = collect_headings(text)
    issues.extend(Issue("error", diagnostic) for diagnostic in frontmatter.diagnostics)

    name = frontmatter.get("name")

    if not name:
        issues.append(Issue("error", "missing frontmatter field: name"))
    else:
        if len(name) > NAME_MAX_LENGTH:
            issues.append(Issue("error", f"frontmatter name is too long ({len(name)} chars > {NAME_MAX_LENGTH})"))
        if not NAME_RE.fullmatch(name):
            issues.append(Issue("error", "frontmatter name must be lowercase with hyphen separators"))
        if name != skill_dir.name:
            issues.append(
                Issue(
                    "error",
                    f"frontmatter name '{name}' does not match folder name '{skill_dir.name}'",
                )
            )

    issues.extend(Issue("error", message) for message in discovery_contract_issues(frontmatter))
    for heading in REQUIRED_HEADINGS:
        if not has_heading(headings, heading):
            issues.append(Issue("error", f"missing required section: {heading}"))

    line_count = len(text.splitlines())
    has_resource_dirs = any((skill_dir / name).exists() for name in ("references", "scripts", "assets", "templates", "agents"))
    if line_count > ERROR_LINE_BUDGET:
        issues.append(Issue("error", f"SKILL.md is too long ({line_count} lines > {ERROR_LINE_BUDGET})"))
    elif has_resource_dirs and line_count > WARN_LINE_BUDGET:
        issues.append(
            Issue(
                "warning",
                f"SKILL.md is heavy for a skill with resources ({line_count} lines > {WARN_LINE_BUDGET})",
            )
        )

    issues.extend(validate_resource_directories(skill_dir))
    issues.extend(validate_project_specific_skill(skill_dir, text))
    issues.extend(validate_relative_links(skill_dir, text))
    issues.extend(validate_markdown_resource_links(skill_dir))
    issues.extend(validate_python_scripts(skill_dir))
    issues.extend(validate_shell_scripts(skill_dir))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate skill structure and base SKILL.md contract")
    parser.add_argument("targets", nargs="+", help="Skill directory or SKILL.md path")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures",
    )
    args = parser.parse_args()

    failed = False
    for target in args.targets:
        issues = validate_skill(target)
        print(f"\n== {target} ==")
        if not issues:
            print("PASS")
            continue

        for issue in issues:
            print(f"{issue.severity.upper()}: {issue.message}")

        if any(issue.severity == "error" for issue in issues):
            failed = True
        elif args.strict and any(issue.severity == "warning" for issue in issues):
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
