#!/usr/bin/env python3
"""Semantic checks for AGENTS.md guidance against a target repository."""

from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tomllib

from bounded_io import (
    ArgumentParseError,
    InputFileError,
    ValueFreeArgumentParser,
    command_line_error,
    error_envelope,
    has_unsafe_windows_path_syntax,
    is_within,
    preflight_repo_files,
    read_repo_text,
    resolve_repo_root,
)
from codex_instruction_sources import (
    instruction_candidate_names,
    normalize_project_doc_fallback_filenames,
    same_native_file,
)

LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")

KNOWN_PATH_NAMES = {
    ".env",
    ".env.example",
    ".python-version",
    "AGENTS.md",
    "CHANGELOG.md",
    "Dockerfile",
    "Gemfile",
    "Makefile",
    "README.md",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "databricks.yml",
    "deno.json",
    "docker-compose.yml",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "tsconfig.json",
    "uv.lock",
}

KNOWN_PATH_SUFFIXES = {
    ".cfg",
    ".csproj",
    ".fsproj",
    ".ini",
    ".ipynb",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".mjs",
    ".py",
    ".ps1",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

COMMAND_LANGUAGES = {"bash", "console", "powershell", "ps1", "sh", "shell", "zsh"}
PARSEABLE_LANGUAGES = {"json", "python", "py", "toml"}

TOOL_CONFIGS = {
    "databricks": ("databricks.yml", ".databrickscfg"),
    "docker": ("Dockerfile", "docker-compose.yml", "compose.yml", "compose.yaml"),
    "go": ("go.mod",),
    "gradle": ("build.gradle", "build.gradle.kts", "gradlew", "gradlew.bat"),
    "mvn": ("pom.xml", "mvnw", "mvnw.cmd"),
    "node": ("package.json",),
    "npm": ("package.json",),
    "pnpm": ("package.json", "pnpm-lock.yaml"),
    "poetry": ("pyproject.toml", "poetry.lock"),
    "pytest": ("pyproject.toml", "pytest.ini", "conftest.py"),
    "python": ("pyproject.toml", "requirements.txt", "setup.py"),
    "python3": ("pyproject.toml", "requirements.txt", "setup.py"),
    "ruff": ("pyproject.toml", "ruff.toml", ".ruff.toml"),
    "uv": ("pyproject.toml", "uv.lock"),
    "yarn": ("package.json", "yarn.lock"),
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str
    code: str = "semantic-error"
    severity: str = "error"


@dataclass(frozen=True)
class CodeBlock:
    language: str
    start_line: int
    code: str


@dataclass(frozen=True)
class InlineCodeSpan:
    line: int
    value: str
    line_text: str
    start: int
    end: int


def is_url(target: str) -> bool:
    if re.match(r"^[A-Za-z]:[\\/]", target):
        return False
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target))


def strip_fragment(target: str) -> str:
    return target.split("#", 1)[0]


def normalize_candidate_path(value: str) -> str:
    value = strip_fragment(value.strip().strip("'\"")).strip()
    return value.rstrip(".,;:")


def reference_annotation_context(line: str, start: int, end: int) -> str:
    """Return annotation text directly adjacent to one reference token only."""
    previous_span = line.rfind("`", 0, start)
    next_span = line.find("`", end)
    prefix = line[(previous_span + 1 if previous_span >= 0 else 0) : start]
    suffix = line[end : (next_span if next_span >= 0 else len(line))]
    return f"{prefix} {suffix}".casefold()


def path_annotation_kinds(line: str, start: int, end: int) -> set[str]:
    """Return labels attached to this span, never labels for a later span."""
    previous_span_end = line.rfind("`", 0, start)
    next_span_start = line.find("`", end)
    prefix = line[(previous_span_end + 1 if previous_span_end >= 0 else 0) : start]
    suffix = line[end : (next_span_start if next_span_start >= 0 else len(line))]
    prefix_label = re.sub(r"[\s*_:;,.()\[\]{}-]+$", "", prefix.casefold())
    if previous_span_end >= 0 and re.search(r"[;.!?]", prefix):
        prefix_label = ""
    suffix_label = re.sub(r"^[\s*_:;,.()\[\]{}-]+", "", suffix.casefold())
    kinds: set[str] = set()
    if re.match(
        r"(?:(?:is|are)\s+)?(?:a\s+)?(?:planned|does not exist|not present|do not use until implemented)\b",
        suffix_label,
    ):
        kinds.add("planned")
    if (
        prefix_label.endswith("external")
        or re.search(r"(?:^|\s)external(?:\s+platform)?(?:\s+path)?$", prefix_label)
        or re.match(r"(?:(?:is|are)\s+)?(?:an?\s+)?external\b", suffix_label)
    ):
        kinds.add("external")
    if re.search(r"platform(?:-specific)?\s+path$", prefix_label) or re.match(
        r"(?:(?:is|are)\s+)?platform(?:-specific)?\b", suffix_label
    ):
        kinds.add("platform")
    if re.search(r"(?:task-specific\s+)?pattern$", prefix_label) or re.match(
        r"(?:(?:is|are)\s+)?(?:a\s+)?(?:task-specific\s+)?pattern\b",
        suffix_label,
    ):
        kinds.add("pattern")
    if prefix_label.endswith("planned"):
        kinds.add("planned")
    return kinds


def is_local_edit_target(line: str, start: int, end: int) -> bool:
    context = reference_annotation_context(line, start, end)
    if re.search(
        r"\b(?:do not|don't|never|not)\b[^.;!?]{0,40}\b(?:create|delete|edit|modify|move|remove|rename|update|write)\b",
        context,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:create|delete|edit|modify|move|remove|rename|update|write)\b",
            context,
        )
    )


def has_glob_pattern(value: str) -> bool:
    return any(character in value for character in ("*", "?", "["))


RECOGNIZED_PLACEHOLDER_PATTERN = re.compile(
    r"(?:<[a-z][a-z0-9_-]{0,63}>|\$\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%)"
)


def has_recognized_placeholder(value: str) -> bool:
    return RECOGNIZED_PLACEHOLDER_PATTERN.search(value) is not None


def is_environment_value(value: str) -> bool:
    if re.search(
        r"(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%)",
        value,
    ):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
        return True
    return False


def looks_like_path(value: str) -> bool:
    if not value:
        return False
    if has_unsafe_windows_path_syntax(value):
        return True
    if is_url(value):
        return False
    if any(character.isspace() for character in value):
        return False

    normalized = value.replace("\\", "/")
    name = Path(normalized).name
    if "/" in normalized or normalized.startswith("."):
        return True
    if name in KNOWN_PATH_NAMES:
        return True
    return Path(name).suffix in KNOWN_PATH_SUFFIXES


def resolve_repo_path(repo_root: Path, value: str) -> Path:
    normalized = normalize_candidate_path(value).replace("\\", "/")
    if has_unsafe_windows_path_syntax(normalized):
        raise ValueError("unsafe path syntax")
    return (repo_root / normalized).resolve()


def iter_markdown_links(path: Path, text: str) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in LINK_PATTERN.finditer(line):
            target = match.group(2).strip()
            if target:
                links.append((line_number, target))
    return links


def iter_inline_code(path: Path, text: str) -> list[InlineCodeSpan]:
    spans: list[InlineCodeSpan] = []
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
        for match in INLINE_CODE_PATTERN.finditer(line):
            spans.append(
                InlineCodeSpan(
                    line_number,
                    match.group(1).strip(),
                    line,
                    match.start(),
                    match.end(),
                )
            )

    return spans


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


def verify_path_reference(
    repo_root: Path,
    source_path: Path,
    line_number: int,
    value: str,
    *,
    line_text: str,
    span_start: int = 0,
    span_end: int | None = None,
) -> list[Violation]:
    candidate = normalize_candidate_path(value)
    if not looks_like_path(candidate):
        return []
    if span_end is None:
        span_end = span_start + len(value)
    annotations = path_annotation_kinds(line_text, span_start, span_end)
    if has_unsafe_windows_path_syntax(candidate):
        return [
            Violation(
                source_path,
                line_number,
                "Referenced path uses unsafe Windows syntax",
                code="path-unsafe",
            )
        ]
    if has_recognized_placeholder(candidate) or is_environment_value(candidate):
        return []
    if has_glob_pattern(candidate):
        try:
            matches = list(repo_root.glob(candidate.replace("\\", "/")))
        except (OSError, RuntimeError, ValueError):
            return [
                Violation(
                    source_path,
                    line_number,
                    "Path pattern could not be resolved safely",
                    code="path-pattern-invalid",
                )
            ]
        if matches or "pattern" in annotations:
            return []
        return [
            Violation(
                source_path,
                line_number,
                "Path pattern has no matches in target repo",
                code="path-pattern",
            )
        ]

    try:
        resolved = resolve_repo_path(repo_root, candidate)
    except (OSError, RuntimeError, ValueError):
        return [
            Violation(
                source_path,
                line_number,
                "Referenced path could not be resolved safely",
                code="path-resolution",
            )
        ]
    if not is_within(resolved, repo_root):
        if ({"external", "platform"} & annotations) and not is_local_edit_target(
            line_text, span_start, span_end
        ):
            return []
        return [
            Violation(
                source_path,
                line_number,
                "Referenced path escapes repo root",
                code="path-escape",
            )
        ]
    if not resolved.exists():
        if annotations & {"planned", "external", "platform", "pattern"}:
            return []
        return [
            Violation(
                source_path,
                line_number,
                "Referenced path does not exist in target repo",
                code="missing-path",
            )
        ]
    return []


def load_package_scripts(repo_root: Path) -> dict[str, str]:
    package_json = repo_root / "package.json"
    if not package_json.exists():
        return {}
    try:
        _, content, _ = read_repo_text(repo_root, package_json, label="package-config")
        data = json.loads(content)
    except (InputFileError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(name): str(command) for name, command in scripts.items()}


def load_make_targets(repo_root: Path) -> set[str]:
    targets: set[str] = set()
    makefile = repo_root / "Makefile"
    if not makefile.exists():
        return targets
    try:
        _, content, _ = read_repo_text(repo_root, makefile, label="makefile")
    except InputFileError:
        return targets
    for line in content.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+):", line)
        if match is not None:
            targets.add(match.group(1))
    return targets


def repo_has_tool_config(repo_root: Path, tool: str) -> bool:
    return any((repo_root / config).exists() for config in TOOL_CONFIGS.get(tool, ()))


def split_command_line(line: str) -> list[str]:
    command = line.strip()
    if not command or command.startswith("#"):
        return []
    command = re.split(r"\s+#", command, maxsplit=1)[0].strip()
    if not command:
        return []
    command = RECOGNIZED_PLACEHOLDER_PATTERN.sub("placeholder_value", command)
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return []

    while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0]):
        parts.pop(0)
    return parts


def split_command_segments(line: str) -> list[str]:
    """Split unquoted top-level shell operators without damaging shell words.

    Parenthesized command substitutions and subshell groups are bounded as one
    segment. Their internal syntax is intentionally not treated as part of the
    containing command chain.
    """
    command = line.strip()
    if not command or command.startswith("#"):
        return []
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    subshell_depth = 0
    index = 0
    while index < len(command):
        character = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            current.append(character)
            escaped = True
            index += 1
            continue
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            current.append(character)
            index += 1
            continue
        if character == "$" and following == "(":
            subshell_depth += 1
            current.extend((character, following))
            index += 2
            continue
        if character == "(":
            subshell_depth += 1
            current.append(character)
            index += 1
            continue
        if character == ")" and subshell_depth:
            subshell_depth -= 1
            current.append(character)
            index += 1
            continue
        operator_length = 0
        if subshell_depth == 0:
            if character == ";" or character == "|":
                operator_length = (
                    2 if character == "|" and following in {"|", "&"} else 1
                )
            elif character == "&" and following == "&":
                operator_length = 2
        if operator_length:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += operator_length
            continue
        current.append(character)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def check_command(
    repo_root: Path,
    source_path: Path,
    line_number: int,
    parts: list[str],
    *,
    strict_command_tools: bool,
) -> list[Violation]:
    if not parts:
        return []

    raw_command = parts[0].strip("'\"")
    if raw_command.startswith(("./", ".\\")):
        try:
            command_path = resolve_repo_path(repo_root, raw_command)
        except (OSError, RuntimeError, ValueError):
            return [
                Violation(
                    source_path,
                    line_number,
                    "Command path could not be resolved safely",
                    code="command-path-resolution",
                )
            ]
        if not is_within(command_path, repo_root):
            return [
                Violation(
                    source_path,
                    line_number,
                    "Command path escapes repo root",
                    code="command-path-escape",
                )
            ]
        if not command_path.exists():
            return [
                Violation(
                    source_path,
                    line_number,
                    "Command path does not exist",
                    code="missing-command-path",
                )
            ]
        return []

    command = Path(raw_command).name.lower()
    if command in {"cd", "copy", "del", "echo", "export", "mkdir", "set", "source"}:
        return []

    package_scripts = load_package_scripts(repo_root)
    make_targets = load_make_targets(repo_root)

    if command in {"npm", "pnpm"} and len(parts) >= 3 and parts[1] == "run":
        script = parts[2]
        if script not in package_scripts:
            return [
                Violation(
                    source_path,
                    line_number,
                    "package.json does not define the requested script",
                    code="missing-package-script",
                )
            ]
        return []

    if command == "yarn" and len(parts) >= 2:
        script = parts[2] if len(parts) >= 3 and parts[1] == "run" else parts[1]
        if script not in package_scripts:
            return [
                Violation(
                    source_path,
                    line_number,
                    "package.json does not define the requested script",
                    code="missing-package-script",
                )
            ]
        return []

    if command == "make" and len(parts) >= 2:
        target = parts[1]
        if target not in make_targets:
            return [
                Violation(
                    source_path,
                    line_number,
                    "Makefile does not define the requested target",
                    code="missing-make-target",
                )
            ]
        return []

    if repo_has_tool_config(repo_root, command):
        return []

    if not strict_command_tools:
        return []

    if shutil.which(command) is not None:
        return []

    return [
        Violation(
            source_path,
            line_number,
            "Command tool is not on PATH and no matching repo config was found",
            code="missing-command-tool",
        )
    ]


def check_commands(
    repo_root: Path, source_path: Path, text: str, *, strict_command_tools: bool
) -> list[Violation]:
    violations: list[Violation] = []
    for block in iter_code_blocks(text):
        if block.language not in COMMAND_LANGUAGES:
            continue
        for offset, line in enumerate(block.code.splitlines()):
            for segment in split_command_segments(line):
                parts = split_command_line(segment)
                if not parts:
                    violations.append(
                        Violation(
                            source_path,
                            block.start_line + offset,
                            "Command example could not be parsed",
                            code="invalid-command-block",
                        )
                    )
                    continue
                violations.extend(
                    check_command(
                        repo_root,
                        source_path,
                        block.start_line + offset,
                        parts,
                        strict_command_tools=strict_command_tools,
                    )
                )
    return violations


def check_parseable_examples(source_path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for block in iter_code_blocks(text):
        if block.language not in PARSEABLE_LANGUAGES:
            continue
        candidate = RECOGNIZED_PLACEHOLDER_PATTERN.sub("placeholder_value", block.code)
        try:
            if block.language in {"python", "py"}:
                ast.parse(candidate)
            elif block.language == "json":
                json.loads(candidate)
            elif block.language == "toml":
                tomllib.loads(candidate)
        except (SyntaxError, ValueError, tomllib.TOMLDecodeError):
            violations.append(
                Violation(
                    source_path,
                    block.start_line,
                    f"Invalid {block.language} code block",
                    code="invalid-code-block",
                )
            )
    return violations


def check_references(repo_root: Path, source_path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for line_number, target in iter_markdown_links(source_path, text):
        if is_url(target) or target.startswith("#"):
            continue
        target_path = normalize_candidate_path(target)
        if has_unsafe_windows_path_syntax(target_path):
            violations.append(
                Violation(
                    source_path,
                    line_number,
                    "Link uses unsafe Windows syntax",
                    code="link-unsafe",
                )
            )
            continue
        try:
            resolved = (source_path.parent / strip_fragment(target_path)).resolve()
        except (OSError, RuntimeError, ValueError):
            violations.append(
                Violation(
                    source_path,
                    line_number,
                    "Link could not be resolved safely",
                    code="link-resolution",
                )
            )
            continue
        if not is_within(resolved, repo_root):
            violations.append(
                Violation(
                    source_path,
                    line_number,
                    "Link escapes repo root",
                    code="link-escape",
                )
            )
        elif not resolved.exists():
            violations.append(
                Violation(
                    source_path,
                    line_number,
                    "Linked file does not exist",
                    code="missing-link",
                )
            )

    for span in iter_inline_code(source_path, text):
        violations.extend(
            verify_path_reference(
                repo_root,
                source_path,
                span.line,
                span.value,
                line_text=span.line_text,
                span_start=span.start,
                span_end=span.end,
            )
        )

    return violations


def collect_section_lines(
    text: str, target_headings: set[str]
) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    active = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match is not None:
            active = match.group(2).strip().lower() in target_headings
            continue
        if active:
            lines.append((line_number, line))

    return lines


def find_nested_agents_scopes(
    repo_root: Path, fallback_filenames: Iterable[str] = ()
) -> list[str]:
    """Find unique nested instruction directories for known project-doc names."""
    scopes: set[str] = set()
    for filename, _ in instruction_candidate_names(fallback_filenames):
        for path in repo_root.rglob(filename):
            try:
                resolved_path = path.resolve()
                resolved_parent = path.parent.resolve()
            except OSError:
                continue
            if not is_within(resolved_path, repo_root) or not resolved_path.is_file():
                continue
            if resolved_parent == repo_root or not is_within(
                resolved_parent, repo_root
            ):
                continue
            scope = resolved_parent.relative_to(repo_root).as_posix()
            if scope:
                scopes.add(scope.rstrip("/") + "/")
    return sorted(scopes)


def is_root_instruction_file(
    repo_root: Path, agentsmd: Path, fallback_filenames: Iterable[str]
) -> bool:
    """Return whether the lexical target is a contained root instruction candidate."""
    try:
        resolved_target = agentsmd.resolve()
        if agentsmd.parent.resolve() != repo_root or not is_within(
            resolved_target, repo_root
        ):
            return False
        if not resolved_target.is_file():
            return False
        for filename, _ in instruction_candidate_names(fallback_filenames):
            candidate = agentsmd.parent / filename
            if candidate.exists() and same_native_file(candidate, agentsmd):
                return True
        return False
    except OSError:
        return False


def check_nested_scope_conflicts(
    repo_root: Path,
    agentsmd: Path,
    text: str,
    *,
    fallback_filenames: Iterable[str] = (),
) -> list[Violation]:
    if not is_root_instruction_file(repo_root, agentsmd, fallback_filenames):
        return []

    nested_scopes = find_nested_agents_scopes(repo_root, fallback_filenames)
    if not nested_scopes:
        return []

    violations: list[Violation] = []
    for line_number, line in collect_section_lines(text, {"project rules"}):
        for match in INLINE_CODE_PATTERN.finditer(line):
            value = normalize_candidate_path(match.group(1)).replace("\\", "/")
            if not any(value.startswith(scope) for scope in nested_scopes):
                continue
            if any(marker in line.lower() for marker in ("scope", "nested", "except")):
                continue
            violations.append(
                Violation(
                    agentsmd,
                    line_number,
                    "Root Project Rules should not duplicate narrower nested AGENTS.md scope guidance",
                    code="nested-scope-conflict",
                )
            )
    return violations


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def semantic_check(
    repo_root: Path,
    agentsmd: Path,
    evidence: Path | None,
    *,
    strict_command_tools: bool,
    fallback_filenames: Iterable[str] = (),
    agentsmd_text: str | None = None,
    evidence_text: str | None = None,
) -> list[Violation]:
    try:
        resolved_repo_root = repo_root.resolve()
        resolved_agentsmd = agentsmd.resolve()
    except OSError:
        return [
            Violation(
                agentsmd,
                1,
                "AGENTS.md target could not be resolved",
                code="agents-file-resolution",
            )
        ]
    if not is_within(resolved_agentsmd, resolved_repo_root):
        return [
            Violation(
                agentsmd,
                1,
                "AGENTS.md target resolves outside repo root",
                code="agents-file-escape",
            )
        ]

    if agentsmd_text is None:
        try:
            resolved_agentsmd, agentsmd_text, _ = read_repo_text(
                resolved_repo_root, agentsmd, label="agents-file"
            )
        except InputFileError as exc:
            return [Violation(agentsmd, 1, exc.message, code=exc.code)]
        agentsmd = resolved_agentsmd
    violations: list[Violation] = []
    violations.extend(check_references(resolved_repo_root, agentsmd, agentsmd_text))
    violations.extend(
        check_nested_scope_conflicts(
            resolved_repo_root,
            agentsmd,
            agentsmd_text,
            fallback_filenames=fallback_filenames,
        )
    )
    violations.extend(
        check_commands(
            resolved_repo_root,
            agentsmd,
            agentsmd_text,
            strict_command_tools=strict_command_tools,
        )
    )
    violations.extend(check_parseable_examples(agentsmd, agentsmd_text))

    if evidence is not None:
        if evidence_text is None:
            try:
                resolved_evidence, evidence_text, _ = read_repo_text(
                    resolved_repo_root, evidence, label="evidence"
                )
            except InputFileError as exc:
                violations.append(Violation(evidence, 1, exc.message, code=exc.code))
                return violations
            evidence = resolved_evidence
        violations.extend(check_references(resolved_repo_root, evidence, evidence_text))
        violations.extend(
            check_commands(
                resolved_repo_root,
                evidence,
                evidence_text,
                strict_command_tools=strict_command_tools,
            )
        )
        violations.extend(check_parseable_examples(evidence, evidence_text))

    return violations


def main() -> int:
    parser = ValueFreeArgumentParser(
        description="Run semantic checks for AGENTS.md guidance."
    )
    parser.add_argument(
        "agentsmd", nargs="?", type=Path, help="Path to the AGENTS.md file to check"
    )
    parser.add_argument(
        "--agents-file", type=Path, help="Path to the AGENTS.md file to check"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Target repository root. Defaults to the AGENTS.md parent directory.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Optional repo-owned evidence file to check when one is explicitly required.",
    )
    parser.add_argument(
        "--strict-command-tools",
        action="store_true",
        help="Also require referenced command tools to be available on PATH when no repo config proves them. Does not execute project commands.",
    )
    parser.add_argument(
        "--project-doc-fallback-filename",
        action="append",
        default=[],
        help="Additional project instruction basename to recognize. Repeat for multiple names.",
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
        agentsmd, agentsmd_text, _ = read_repo_text(
            repo_root, prepared["agents-file"], label="agents-file"
        )
        evidence = prepared.get("evidence")
        evidence_text = None
        if evidence is not None:
            evidence, evidence_text, _ = read_repo_text(
                repo_root, evidence, label="evidence"
            )
    except InputFileError as exc:
        if args.json:
            print(json.dumps(error_envelope(exc), indent=2))
        else:
            print(f"Error: {exc.message}", file=sys.stderr)
        return 2

    try:
        fallback_filenames = normalize_project_doc_fallback_filenames(
            args.project_doc_fallback_filename
        )
        violations = semantic_check(
            repo_root,
            agentsmd,
            evidence,
            strict_command_tools=args.strict_command_tools,
            fallback_filenames=fallback_filenames,
            agentsmd_text=agentsmd_text,
            evidence_text=evidence_text,
        )
    except (InputFileError, ValueError) as exc:
        error = (
            exc
            if isinstance(exc, InputFileError)
            else InputFileError(
                "invalid-configuration", "configuration", "Invalid configuration"
            )
        )
        if args.json:
            print(json.dumps(error_envelope(error), indent=2))
        else:
            print(f"Error: {error.message}", file=sys.stderr)
        return 2
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
                                "path": display_path(violation.path, repo_root),
                                "line": violation.line,
                                "message": violation.message,
                            }
                            for violation in violations
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for violation in violations:
                print(
                    f"{display_path(violation.path, repo_root)}:{violation.line}: {violation.message}"
                )
        return 1

    if args.json:
        print(json.dumps({"status": "pass", "findings": []}, indent=2))
    else:
        print(f"AGENTS.md semantic check passed: {display_path(agentsmd, repo_root)}")
        if evidence is not None:
            print(
                f"Evidence semantic check passed: {display_path(evidence, repo_root)}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
