"""Collect deterministic project-history evidence for lessons-learned work."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".ipynb"}
DOC_DIR_NAMES = {
    "docs",
    "doc",
    "documentation",
    "guide",
    "guides",
    "inbox",
    "note",
    "notes",
    "reference_docs",
}
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

# Directories whose contents are previous retrospective output,
# not source material.  Exclude them from scanning.
LESSONS_LEARNED_DIRS = ("docs/lessons-learned",)


def is_excluded_path(relative_path: Path) -> bool:
    """Return whether a path should be excluded from evidence collection."""
    relative_posix = relative_path.as_posix()
    if any(
        relative_posix == excluded or relative_posix.startswith(f"{excluded}/") for excluded in LESSONS_LEARNED_DIRS
    ):
        return True
    return any(part in EXCLUDED_DIR_NAMES for part in relative_path.parts[:-1])


def is_doc_candidate(relative_path: Path) -> bool:
    """Return whether a file should be treated as project documentation."""
    if relative_path.suffix.casefold() not in DOC_EXTENSIONS:
        return False
    if len(relative_path.parts) == 1:
        return True

    for part in relative_path.parts[:-1]:
        normalized = part.casefold()
        if normalized in DOC_DIR_NAMES:
            return True
        if normalized.startswith("doc") or normalized.endswith("docs"):
            return True

    return False


def safe_git_log(project_root: Path, limit: int) -> list[str]:
    """Return a compact git log, excluding previous lessons-learned output."""
    exclude_args = [f":(exclude,glob){directory}/**" for directory in LESSONS_LEARNED_DIRS]
    command = [
        "git",
        "-C",
        str(project_root),
        "log",
        f"--max-count={limit}",
        "--date=short",
        "--pretty=format:%ad %h %s",
        "--",
        ".",
        *exclude_args,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def find_docs(project_root: Path) -> list[str]:
    """Return project documentation files worth reading for retrospectives."""
    found: list[str] = []

    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue

        relative_path = path.relative_to(project_root)
        if is_excluded_path(relative_path):
            continue
        if not is_doc_candidate(relative_path):
            continue

        found.append(relative_path.as_posix())

    return found


def find_chat_sessions(workspace_fragment: str | None) -> list[str]:
    """Best-effort scan for VS Code chat sessions matching a workspace fragment."""
    if not workspace_fragment:
        return []

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []

    workspace_storage = Path(appdata) / "Code" / "User" / "workspaceStorage"
    if not workspace_storage.exists():
        return []

    matches: set[str] = set()
    for workspace_dir in workspace_storage.iterdir():
        workspace_json = workspace_dir / "workspace.json"
        if not workspace_json.exists():
            continue
        try:
            text = workspace_json.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if workspace_fragment.casefold() not in text.casefold():
            continue
        chat_dir = workspace_dir / "chatSessions"
        if chat_dir.exists():
            for session in chat_dir.rglob("*.json"):
                matches.add(str(session))

    return sorted(matches)


def safe_contains(path: Path, fragment: str | None, max_bytes: int = 5_000_000) -> bool:
    """Return whether a text-ish file contains fragment, bounded by size."""
    if not fragment:
        return True
    try:
        if path.stat().st_size > max_bytes:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return fragment.casefold() in text.casefold()


def find_codex_sessions(workspace_fragment: str | None) -> list[str]:
    """Best-effort scan for Codex JSONL sessions matching a workspace fragment."""
    if not workspace_fragment:
        return []
    codex_root = Path.home() / ".codex"
    if not codex_root.exists():
        return []
    candidate_dirs = [codex_root / "sessions", codex_root / "archived_sessions"]
    matches: list[str] = []
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for session in sorted(directory.rglob("*.jsonl")):
            if safe_contains(session, workspace_fragment):
                matches.append(str(session))
    return matches


_Report = dict[str, Any]


def build_report(project_root: Path, workspace_fragment: str | None, git_limit: int) -> _Report:
    """Build the project-history inventory report."""
    docs = find_docs(project_root)
    git_log = safe_git_log(project_root, git_limit)
    vs_code_sessions = find_chat_sessions(workspace_fragment)
    codex_sessions = find_codex_sessions(workspace_fragment)
    coverage = {
        "docs": {"included": bool(docs), "count": len(docs), "reason": "project documentation scan"},
        "git_history": {"included": bool(git_log), "count": len(git_log), "reason": "git log scan"},
        "vscode_copilot_storage": {
            "included": bool(vs_code_sessions),
            "count": len(vs_code_sessions),
            "reason": "workspace fragment matched VS Code workspaceStorage"
            if workspace_fragment
            else "workspace fragment not provided",
        },
        "codex_sessions": {
            "included": bool(codex_sessions),
            "count": len(codex_sessions),
            "reason": "workspace fragment matched Codex sessions"
            if workspace_fragment
            else "workspace fragment not provided",
        },
        "lessons_learned_outputs": {
            "included": False,
            "count": 0,
            "reason": "previous lessons-learned outputs are excluded by policy",
        },
    }
    return {
        "project_root": str(project_root),
        "workspace_fragment": workspace_fragment,
        "source_coverage": coverage,
        "docs": docs,
        "git_log": git_log,
        "chat_sessions": vs_code_sessions,
        "codex_sessions": codex_sessions,
    }


def print_markdown(report: _Report) -> None:
    """Print the history report as markdown."""
    print("# Project History Inventory\n")
    print(f"Project root: {report['project_root']}\n")

    print("## Source Coverage")
    for family, status in report["source_coverage"].items():
        included = "included" if status["included"] else "excluded or none found"
        print(f"- {family}: {included}; count={status['count']}; {status['reason']}")

    print("\n## Documentation Sources")
    docs = report["docs"]
    if docs:
        for doc in docs:
            print(f"- {doc}")
    else:
        print("- none found")

    print("\n## Git History")
    git_log = report["git_log"]
    if git_log:
        for line in git_log:
            print(f"- {line}")
    else:
        print("- no git history available")

    print("\n## VS Code Chat Sessions")
    chat_sessions = report["chat_sessions"]
    if chat_sessions:
        for session in chat_sessions:
            print(f"- {session}")
    else:
        print("- none found or workspace fragment not provided")

    print("\n## Codex Sessions")
    codex_sessions = report["codex_sessions"]
    if codex_sessions:
        for session in codex_sessions:
            print(f"- {session}")
    else:
        print("- none found or workspace fragment not provided")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect deterministic project-history evidence")
    parser.add_argument("project_root", help="Path to the project root")
    parser.add_argument(
        "--workspace-fragment",
        help="Substring used to match VS Code workspace metadata when scanning chat sessions",
    )
    parser.add_argument("--git-limit", type=int, default=40, help="Maximum git log entries to return")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        parser.error(f"Project root does not exist: {project_root}")
    if not project_root.is_dir():
        parser.error(f"Project root is not a directory: {project_root}")

    report = build_report(
        project_root=project_root,
        workspace_fragment=args.workspace_fragment,
        git_limit=args.git_limit,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_markdown(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
