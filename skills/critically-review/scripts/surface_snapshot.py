#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from review_io import (
    SafeArgumentParser,
    ToolError,
    add_error_format,
    safe_identifier,
    safe_main,
    write_output,
)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
}

TOOL_PATTERNS = [
    re.compile(r"@(?:\w+\.)?(?:tool|command)\s*\("),
    re.compile(r"\.register_tool\s*\("),
    re.compile(r"Tool\s*\(\s*name\s*="),
    re.compile(r"name\s*=\s*[\"']([A-Za-z0-9_.-]+)[\"']"),
]

ROUTE_PATTERN = re.compile(
    r"@(?:\w+\.)?(?:get|post|put|delete|patch|route)\s*\(\s*[\"']([^\"']+)[\"']"
)
ARGPARSE_PATTERN = re.compile(r"add_parser\s*\(\s*[\"']([^\"']+)[\"']")
CLICK_PATTERN = re.compile(
    r"@(?:\w+\.)?command\s*\(\s*(?:name\s*=\s*)?[\"']?([^\"')]+)?"
)
FRONTMATTER_FIELD = re.compile(r"^(name|description):\s*(.*)$")
DOC_PATH_PATTERN = re.compile(
    r"(?:^|[\s(\[])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?:\.[A-Za-z0-9]+)?)"
)


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def add_item(
    items: list[dict[str, Any]],
    name: str,
    kind: str,
    path: Path,
    root: Path,
    evidence: str = "",
) -> None:
    item = {"name": name, "kind": kind, "path": rel(path, root)}
    if evidence:
        item["evidence"] = evidence.strip()[:240]
    items.append(item)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = FRONTMATTER_FIELD.match(line.strip())
        if match:
            fields[match.group(1)] = match.group(2).strip().strip('"')
    return fields


def snapshot(root: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    docs_referenced: set[str] = set()

    for path in iter_files(root):
        relative = rel(path, root)
        suffix = path.suffix.lower()
        name = path.name

        if name in {"AGENTS.md", "README.md"}:
            add_item(items, name, "repo-doc", path, root)

        if suffix == ".md":
            if name.endswith(".prompt.md"):
                add_item(items, name, "prompt", path, root)
            if name == "SKILL.md":
                fields = parse_frontmatter(read_text(path))
                skill_name = fields.get("name") or path.parent.name
                add_item(
                    items,
                    skill_name,
                    "skill",
                    path,
                    root,
                    fields.get("description", ""),
                )

        if suffix in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            text = read_text(path)
            for pattern in TOOL_PATTERNS[:-1]:
                if pattern.search(text):
                    for name_match in TOOL_PATTERNS[-1].finditer(text):
                        add_item(
                            items,
                            name_match.group(1),
                            "tool-registration",
                            path,
                            root,
                            name_match.group(0),
                        )
                    if not any(
                        item["path"] == relative and item["kind"] == "tool-registration"
                        for item in items
                    ):
                        add_item(items, path.stem, "tool-registration", path, root)
            for match in ROUTE_PATTERN.finditer(text):
                add_item(items, match.group(1), "route", path, root, match.group(0))
            for match in ARGPARSE_PATTERN.finditer(text):
                add_item(
                    items, match.group(1), "cli-subcommand", path, root, match.group(0)
                )
            for match in CLICK_PATTERN.finditer(text):
                command_name = (match.group(1) or path.stem).strip("'\"")
                if command_name:
                    add_item(
                        items, command_name, "cli-command", path, root, match.group(0)
                    )

        if name == "package.json":
            try:
                package = json.loads(read_text(path))
            except json.JSONDecodeError:
                package = {}
            scripts = package.get("scripts") if isinstance(package, dict) else None
            if isinstance(scripts, dict):
                for script_name in scripts:
                    add_item(items, script_name, "package-script", path, root)
            bin_entries = package.get("bin") if isinstance(package, dict) else None
            if isinstance(bin_entries, dict):
                for bin_name in bin_entries:
                    add_item(items, bin_name, "package-bin", path, root)
            elif isinstance(bin_entries, str):
                add_item(
                    items,
                    package.get("name", path.parent.name),
                    "package-bin",
                    path,
                    root,
                )

        if name == "pyproject.toml":
            text = read_text(path)
            in_scripts = False
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("["):
                    in_scripts = stripped in {
                        "[project.scripts]",
                        "[tool.poetry.scripts]",
                    }
                    continue
                if in_scripts and "=" in stripped and not stripped.startswith("#"):
                    script_name = stripped.split("=", 1)[0].strip()
                    add_item(
                        items, script_name, "python-entry-point", path, root, stripped
                    )

        if suffix in {".md", ".txt", ".rst"}:
            text = read_text(path)
            for match in DOC_PATH_PATTERN.finditer(text):
                candidate = match.group(1)
                if not candidate.startswith(("http/", "https/")):
                    docs_referenced.add(candidate)

    by_kind: dict[str, int] = defaultdict(int)
    for item in items:
        by_kind[item["kind"]] += 1

    missing_doc_paths = sorted(
        candidate
        for candidate in docs_referenced
        if not (root / candidate).exists()
        and not candidate.startswith(("node_modules/", "http/", "https/"))
    )

    return {
        "root": safe_identifier(root),
        "counts": dict(sorted(by_kind.items())),
        "items": sorted(
            items, key=lambda item: (item["kind"], item["name"], item["path"])
        ),
        "referenced_paths_missing": missing_doc_paths[:500],
    }


def main() -> int:
    parser = SafeArgumentParser(
        description="Inventory common repository surfaces for critical technical reviews."
    )
    parser.add_argument(
        "--root", default=".", help="Repository or folder root to inventory."
    )
    parser.add_argument(
        "--output", help="New JSON output path. Prints to stdout when omitted."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Use supported safe replacement for an existing output.",
    )
    add_error_format(parser)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise ToolError("root-invalid")
    data = snapshot(root)
    payload = json.dumps(data, indent=2) + "\n"
    if args.output:
        output = write_output(payload, args.output, [], force=args.force)
        print(json.dumps({"output": safe_identifier(output)}, sort_keys=True))
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(safe_main(main))
