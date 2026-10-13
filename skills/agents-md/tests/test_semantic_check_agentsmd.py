from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import semantic_check_agentsmd


SKILL_ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_SCRIPT = SKILL_ROOT / "scripts" / "semantic_check_agentsmd.py"


def write_repo_agentsmd(tmp_path: Path, text: str) -> Path:
    agentsmd = tmp_path / "AGENTS.md"
    agentsmd.write_text(text, encoding="utf-8")
    return agentsmd


def semantic_codes(
    repo_root: Path, agentsmd: Path, *, fallback_filenames: tuple[str, ...] = ()
) -> set[str]:
    violations = semantic_check_agentsmd.semantic_check(
        repo_root,
        agentsmd,
        None,
        strict_command_tools=False,
        fallback_filenames=fallback_filenames,
    )
    return {violation.code for violation in violations}


def test_semantic_check_reports_missing_inline_path(tmp_path: Path) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Context

- Read `docs/missing.md` before changing workflows.
""",
    )

    assert "missing-path" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_rejects_etc_passwd_inline_path(tmp_path: Path) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Context

- Do not rely on `/etc/passwd` for local repository behavior.
""",
    )

    assert "path-escape" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_reports_missing_path_even_when_other_path_is_planned(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("real\n", encoding="utf-8")
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Context

- `docs/planned.md` is Planned, but `docs/missing.md` must already exist.
""",
    )

    assert "missing-path" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_reports_invalid_python_containing_bracket(
    tmp_path: Path,
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Context

```python
value = [
```
""",
    )

    assert "invalid-code-block" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_annotations_are_isolated_per_reference(tmp_path: Path) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Context

- `docs/planned.md` is Planned, but `../../outside/secret.md` must already exist.
""",
    )

    assert "path-escape" in semantic_codes(tmp_path, agentsmd)


@pytest.mark.parametrize(
    "line, expected",
    [
        (
            "- `docs/planned.md` is PLANNED and `docs/missing.md` must exist.",
            "missing-path",
        ),
        (
            "- External path `/etc/passwd` and `../../outside.md` must exist.",
            "path-escape",
        ),
        (
            "- `docs/planned.md` is planned; `docs/missing.md` must exist.",
            "missing-path",
        ),
    ],
)
def test_semantic_annotations_do_not_cross_reference_boundaries(
    tmp_path: Path, line: str, expected: str
) -> None:
    agentsmd = write_repo_agentsmd(tmp_path, f"# Example\n\n## Context\n\n{line}\n")

    assert expected in semantic_codes(tmp_path, agentsmd)


@pytest.mark.parametrize(
    "line",
    [
        "- External platform path: `/etc/passwd` is inspected only outside this repo.",
        "- Platform path `/etc/passwd` is not a local edit target.",
        "- Platform path `%USERPROFILE%\\.config\\tool.toml` is not a repo edit target.",
        "- Platform path `$HOME/.config/tool.toml` is not a repo edit target.",
        "- Task-specific pattern `status/<run-id>.md` is created by each run.",
    ],
)
def test_semantic_check_accepts_explicit_platform_and_task_placeholders(
    tmp_path: Path, line: str
) -> None:
    agentsmd = write_repo_agentsmd(tmp_path, f"# Example\n\n## Context\n\n{line}\n")

    assert not semantic_codes(tmp_path, agentsmd)


def test_semantic_check_rejects_external_path_used_as_local_edit_target(
    tmp_path: Path,
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n- Update external path `/etc/passwd`.\n",
    )

    assert "path-escape" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_parses_valid_python_lists(tmp_path: Path) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n```python\nvalue = [1, 2, 3]\n```\n",
    )

    assert "invalid-code-block" not in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_skips_only_recognized_example_placeholder(
    tmp_path: Path,
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n```python\nvalue = <task-value>\n```\n",
    )

    assert "invalid-code-block" not in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_checks_every_shell_segment_and_remaining_placeholder_block(
    tmp_path: Path,
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n```bash\npython <script> && ./missing-command\n```\n\n```python\nvalue = <task-value>\nbroken = [\n```\n",
    )

    codes = semantic_codes(tmp_path, agentsmd)

    assert "missing-command-path" in codes
    assert "invalid-code-block" in codes


@pytest.mark.parametrize("operator", ["|", "|&"])
def test_semantic_check_checks_each_pipeline_segment(
    tmp_path: Path, operator: str
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n```bash\npython <script> "
        f"{operator} ./missing-command\n```\n",
    )

    assert "missing-command-path" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_accepts_a_legitimate_pipeline_and_keeps_quoted_pipes(
    tmp_path: Path,
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n```bash\n"
        "python -c \"print('a|b')\" | python -m json.tool\n"
        "python -c \"print('a\\\\|b')\" && python -m json.tool\n"
        "python -c \"print('$(a | b)')\" | python -m json.tool\n"
        "```\n",
    )

    assert "invalid-command-block" not in semantic_codes(tmp_path, agentsmd)


@pytest.mark.parametrize(
    ("line", "first_kinds", "second_kinds"),
    [
        (
            "- `docs/first` and `docs/second` are a Pattern.",
            set(),
            {"pattern"},
        ),
        (
            "- **PLANNED:** (`docs/first`); `docs/second` must exist.",
            {"planned"},
            set(),
        ),
        (
            "- `docs/first` (**eXtErNaL**), then `docs/second` must exist.",
            {"external"},
            set(),
        ),
    ],
)
def test_semantic_annotations_bind_only_to_their_adjacent_inline_span(
    line: str, first_kinds: set[str], second_kinds: set[str]
) -> None:
    spans = list(semantic_check_agentsmd.INLINE_CODE_PATTERN.finditer(line))

    assert (
        semantic_check_agentsmd.path_annotation_kinds(
            line, spans[0].start(), spans[0].end()
        )
        == first_kinds
    )
    assert (
        semantic_check_agentsmd.path_annotation_kinds(
            line, spans[1].start(), spans[1].end()
        )
        == second_kinds
    )


@pytest.mark.parametrize(
    "path", [r"C:outside.md", r"\outside.md", r"\\server\share\file.md", r"\\?\C:\x"]
)
def test_semantic_check_rejects_unsafe_windows_reference_syntax(
    tmp_path: Path, path: str
) -> None:
    agentsmd = write_repo_agentsmd(
        tmp_path, f"# Example\n\n## Context\n\n- Read `{path}`.\n"
    )

    assert "path-unsafe" in semantic_codes(tmp_path, agentsmd)


def test_semantic_cli_rejects_outside_evidence_without_leaking_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agentsmd = write_repo_agentsmd(repo, "# Example\n\n## Context\n")
    marker = "OUTSIDE_SECRET_EVIDENCE_MARKER"
    evidence = tmp_path / f"{marker}.md"
    evidence.write_text(marker, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SEMANTIC_SCRIPT),
            "--repo-root",
            str(repo),
            "--agents-file",
            str(agentsmd),
            "--evidence",
            str(evidence),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert marker not in result.stdout
    assert '"status": "error"' in result.stdout
    assert '"code": "evidence-escape"' in result.stdout


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"# Example\n\xff", "agents-file-invalid-utf8"),
        (b"# Example\x00\n", "agents-file-binary"),
    ],
)
def test_semantic_cli_rejects_malformed_input_with_json_only_error(
    tmp_path: Path,
    payload: bytes,
    code: str,
) -> None:
    agentsmd = tmp_path / "AGENTS.md"
    agentsmd.write_bytes(payload)

    result = subprocess.run(
        [
            sys.executable,
            str(SEMANTIC_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--agents-file",
            str(agentsmd),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert "Traceback" not in result.stdout
    assert f'"code": "{code}"' in result.stdout


def test_semantic_check_recognizes_nested_override_scope(tmp_path: Path) -> None:
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "api" / "AGENTS.override.md").write_text(
        "# API\n", encoding="utf-8"
    )
    (tmp_path / "packages" / "api" / "config.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Project Rules

- Update `packages/api/config.py` from root guidance.
""",
    )

    assert "nested-scope-conflict" in semantic_codes(tmp_path, agentsmd)


def test_semantic_check_recognizes_explicit_nested_fallback_scope(
    tmp_path: Path,
) -> None:
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "api" / "TEAM.md").write_text("# API\n", encoding="utf-8")
    (tmp_path / "packages" / "api" / "config.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    agentsmd = write_repo_agentsmd(
        tmp_path,
        """# Example

## Project Rules

- Update `packages/api/config.py` from root guidance.
""",
    )

    assert "nested-scope-conflict" in semantic_codes(
        tmp_path,
        agentsmd,
        fallback_filenames=("TEAM.md",),
    )


def test_semantic_check_treats_root_override_as_root_scope(tmp_path: Path) -> None:
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("# API\n", encoding="utf-8")
    (nested / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    override = tmp_path / "AGENTS.override.md"
    override.write_text(
        """# Example

## Project Rules

- Update `packages/api/config.py` from root guidance.
""",
        encoding="utf-8",
    )

    assert "nested-scope-conflict" in semantic_codes(tmp_path, override)


def test_nested_instruction_scope_directories_are_deduplicated(tmp_path: Path) -> None:
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "AGENTS.override.md").write_text("# API override\n", encoding="utf-8")
    (nested / "AGENTS.md").write_text("# API normal\n", encoding="utf-8")
    (nested / "TEAM.md").write_text("# API fallback\n", encoding="utf-8")

    assert semantic_check_agentsmd.find_nested_agents_scopes(
        tmp_path, ("TEAM.md",)
    ) == ["packages/api/"]


@pytest.mark.parametrize("root_filename", ["AGENTS.md", "AGENTS.override.md"])
def test_root_instruction_symlink_keeps_lexical_root_identity(
    tmp_path: Path, root_filename: str
) -> None:
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("# API\n", encoding="utf-8")
    (nested / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    target = tmp_path / "shared-root-guidance.md"
    target.write_text(
        """# Example

## Project Rules

- Update `packages/api/config.py` from root guidance.
""",
        encoding="utf-8",
    )
    root_candidate = tmp_path / root_filename
    try:
        os.symlink(target, root_candidate)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    assert "nested-scope-conflict" in semantic_codes(tmp_path, root_candidate)


def test_semantic_check_rejects_root_candidate_symlinked_outside_repo(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    root_candidate = tmp_path / "AGENTS.md"
    try:
        os.symlink(outside, root_candidate)
    except OSError as exc:
        outside.unlink()
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    try:
        assert "agents-file-escape" in semantic_codes(tmp_path, root_candidate)
        result = subprocess.run(
            [
                sys.executable,
                str(SEMANTIC_SCRIPT),
                "--repo-root",
                str(tmp_path),
                "--agents-file",
                str(root_candidate),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "resolves outside repo root" in result.stderr
    finally:
        outside.unlink(missing_ok=True)
