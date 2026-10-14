from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import analyze_project


def create_directory_alias(target: Path, alias: Path) -> None:
    try:
        os.symlink(target, alias, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            pytest.skip(f"Directory symlink creation is unavailable: {symlink_error}")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "New-Item",
            "-ItemType",
            "Junction",
            "-Path",
            str(alias),
            "-Target",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not alias.exists():
        pytest.skip(
            f"Directory junction creation is unavailable: {result.stderr.strip()}"
        )


def remove_directory_alias(alias: Path) -> None:
    if alias.is_symlink():
        alias.unlink()
    elif alias.exists():
        alias.rmdir()


def test_analyzer_detects_root_python_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "example"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        encoding="utf-8",
    )

    analysis = analyze_project.analyze_project(tmp_path)

    assert analysis.schema_version == "1.3"
    assert "Python" in analysis.languages
    assert "pyproject.toml" in analysis.config_files
    assert "pytest" in analysis.tools
    assert {
        "framework": "pytest",
        "config": "pyproject.toml",
    } in analysis.test_frameworks
    assert {
        "path": "pyproject.toml",
        "source": "project.requires-python",
        "value": ">=3.11",
    } in analysis.python_version_hints


def test_analyzer_adds_metadata_only_codex_instruction_audit(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("do not expose this content", encoding="utf-8")
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "TEAM.md").write_text("another private instruction", encoding="utf-8")

    analysis = analyze_project.analyze_project(
        tmp_path,
        cwd=nested,
        project_doc_fallback_filenames=["TEAM.md"],
        project_doc_byte_limit=1,
    )

    audit = analysis.codex_project_instruction_audit
    assert audit["runtime_attestation"] == "not-verified"
    assert audit["candidate_count"] == 2
    assert [item["path"] for item in audit["instruction_files"]] == [
        "AGENTS.md",
        "packages/api/TEAM.md",
    ]
    assert all(
        item["exceeds_byte_limit_if_selected"] for item in audit["instruction_files"]
    )
    assert audit["selected_chain_bounds"]["could_exceed_byte_limit"] is True
    assert "do not expose this content" not in analyze_project.format_json(analysis)
    markdown = analyze_project.format_markdown(analysis)
    assert "## AGENTS.md Files Found\n\n- `AGENTS.md`" in markdown
    assert "## Codex Project Instruction Audit" in markdown
    assert "Project instruction bytes" not in markdown
    assert (
        "Documented per-directory precedence: `AGENTS.override.md` -> `AGENTS.md` -> `configured-fallbacks`"
        in markdown
    )


def test_analyzer_cli_rejects_cwd_outside_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    script = Path(analyze_project.__file__)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(repo_root),
            "--cwd",
            str(outside),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["findings"][0]["code"] == "cwd-escape"
    assert str(outside) not in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ["--format", "json", "--max-depth", "INVALID_TYPE_MARKER"],
        ["--format", "json", "--max-files", "0"],
        ["--format", "json"],
    ],
)
def test_analyzer_json_argument_errors_are_value_free(
    tmp_path: Path, arguments: list[str]
) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(analyze_project.__file__)), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert "INVALID_TYPE_MARKER" not in result.stdout
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-arguments"


def test_analyzer_markdown_omits_empty_configuration_files_section(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'example'\n", encoding="utf-8"
    )

    markdown = analyze_project.format_markdown(
        analyze_project.analyze_project(tmp_path)
    )

    assert "## Configuration Files Found\n\n##" not in markdown
    assert "## Configuration Files Found\n\n- `pyproject.toml`" in markdown
    assert "## Next Steps" not in markdown


def test_analyzer_detects_nested_package_json(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "web"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        """{
  "scripts": {
    "test": "vitest run",
    "build": "tsc -b"
  },
  "dependencies": {
    "react": "^19.0.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0"
  }
}
""",
        encoding="utf-8",
    )

    analysis = analyze_project.analyze_project(tmp_path)

    assert "packages/web/package.json" in analysis.config_files
    assert "React" in analysis.frameworks
    assert "TypeScript" in analysis.languages
    assert {
        "path": "packages/web/package.json",
        "script": "test",
        "command": "vitest run",
    } in analysis.command_inventory


def test_analyzer_detects_common_nested_package_json_layouts(tmp_path: Path) -> None:
    for prefix in ("apps", "services"):
        package_dir = tmp_path / prefix / "api"
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(
            """{
  "scripts": {
    "lint": "eslint ."
  },
  "dependencies": {
    "express": "^5.0.0"
  }
}
""",
            encoding="utf-8",
        )

    analysis = analyze_project.analyze_project(tmp_path)

    assert "apps/api/package.json" in analysis.config_files
    assert "services/api/package.json" in analysis.config_files
    assert {
        "path": "apps/api/package.json",
        "script": "lint",
        "command": "eslint .",
    } in analysis.command_inventory
    assert "Express" in analysis.frameworks


def test_analyzer_detects_package_managers_from_lockfiles(tmp_path: Path) -> None:
    for lockfile in ("package-lock.json", "pnpm-lock.yaml", "uv.lock", "Cargo.lock"):
        (tmp_path / lockfile).write_text("", encoding="utf-8")

    analysis = analyze_project.analyze_project(tmp_path)

    assert {
        "manager": "npm",
        "evidence": "package-lock.json",
    } in analysis.package_managers
    assert {
        "manager": "pnpm",
        "evidence": "pnpm-lock.yaml",
    } in analysis.package_managers
    assert {"manager": "uv", "evidence": "uv.lock"} in analysis.package_managers
    assert {"manager": "Cargo", "evidence": "Cargo.lock"} in analysis.package_managers
    assert any(
        item["kind"] == "ambiguous-evidence" for item in analysis.uncertainty_items
    )


def test_analyzer_markdown_renders_json_fact_sections(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        """{
  "scripts": {
    "test": "npm run unit"
  }
}
""",
        encoding="utf-8",
    )

    analysis = analyze_project.analyze_project(tmp_path)
    markdown = analyze_project.format_markdown(analysis)

    assert "## Package Managers\n\n- `npm` (`package-lock.json`)" in markdown
    assert (
        "## Command Inventory\n\n- `package.json` script `test`: `npm run unit`"
        in markdown
    )
    assert "## Suggestions for AGENTS.md" not in markdown


def test_analyzer_reports_invalid_utf8_and_binary_candidates_without_traceback(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\x00private-content-marker")

    analysis = analyze_project.analyze_project(tmp_path)

    assert analysis.languages == ["Python"]
    assert analysis.frameworks == []
    assert any(
        item.get("code") == "analysis-candidate-binary"
        for item in analysis.uncertainty_items
    )
    output = analyze_project.format_json(analysis)
    assert "private-content-marker" not in output


def test_analyzer_excludes_file_symlink_to_outside_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = "OUTSIDE_ANALYZER_FILE_SECRET"
    outside = tmp_path / "outside.py"
    outside.write_text(f"print('{marker}')\n", encoding="utf-8")
    alias = repo / "leak.py"
    try:
        os.symlink(outside, alias)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    analysis = analyze_project.analyze_project(repo)
    output = analyze_project.format_json(analysis)

    assert analysis.source_files_sampled == 0
    assert any(
        item.get("code") == "outside-root-file-alias"
        for item in analysis.uncertainty_items
    )
    assert marker not in output
    assert str(outside) not in output


def test_analyzer_excludes_generated_directory_alias_to_outside(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside-generated"
    outside.mkdir()
    marker = "OUTSIDE_GENERATED_SECRET"
    (outside / "secret.py").write_text(marker, encoding="utf-8")
    alias = repo / "generated"
    create_directory_alias(outside, alias)
    try:
        analysis = analyze_project.analyze_project(repo, follow_symlinks=True)
        output = analyze_project.format_json(analysis)

        assert "generated/" not in analysis.generated_candidates
        assert any(
            item.get("code") == "outside-root-directory-alias"
            for item in analysis.uncertainty_items
        )
        assert marker not in output
        assert str(outside) not in output
    finally:
        remove_directory_alias(alias)


def test_analyzer_follow_symlinks_only_follows_alias_within_root(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared"
    target.mkdir()
    (target / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    alias = tmp_path / "linked"
    create_directory_alias(target, alias)
    try:
        without_follow = analyze_project.analyze_project(
            tmp_path, follow_symlinks=False
        )
        with_follow = analyze_project.analyze_project(tmp_path, follow_symlinks=True)

        assert without_follow.source_files_sampled == 1
        assert with_follow.source_files_sampled == 1
    finally:
        remove_directory_alias(alias)


def test_analyzer_rejects_hardlink_candidate_when_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 'secret'\n", encoding="utf-8")
    alias = repo / "alias.py"
    try:
        os.link(outside, alias)
    except OSError as exc:
        pytest.skip(f"Hardlink creation is unavailable: {exc}")

    analysis = analyze_project.analyze_project(repo)

    assert analysis.source_files_sampled == 0
    assert any(
        item.get("code") == "hardlink-file-alias" for item in analysis.uncertainty_items
    )


def test_analyzer_file_byte_cap_boundary_and_plus_one(tmp_path: Path) -> None:
    content = '[project]\nname = "bounded"\n'
    config = tmp_path / "pyproject.toml"
    config.write_text(content, encoding="utf-8")
    byte_count = len(config.read_bytes())

    at_limit = analyze_project.analyze_project(tmp_path, max_file_bytes=byte_count)
    below_limit = analyze_project.analyze_project(
        tmp_path, max_file_bytes=byte_count - 1
    )

    assert at_limit.scan_summary["truncated"] is False
    assert not any(
        item.get("code") == "per-file-byte-limit" for item in at_limit.uncertainty_items
    )
    assert below_limit.scan_summary["truncated"] is True
    assert any(
        item.get("code") == "per-file-byte-limit"
        for item in below_limit.uncertainty_items
    )


def test_analyzer_total_byte_cap_boundary_and_plus_one(tmp_path: Path) -> None:
    content = '{"scripts": {"test": "pytest"}}'
    (tmp_path / "package.json").write_text(content, encoding="utf-8")
    byte_count = len(content.encode("utf-8"))

    at_limit = analyze_project.analyze_project(tmp_path, max_total_bytes=byte_count)
    below_limit = analyze_project.analyze_project(
        tmp_path, max_total_bytes=byte_count - 1
    )

    assert at_limit.scan_summary["observed"]["bytes_read"] == byte_count
    assert at_limit.scan_summary["truncated"] is False
    assert at_limit.command_inventory
    assert below_limit.scan_summary["truncated"] is True
    assert any(
        item.get("code") == "total-byte-limit" for item in below_limit.uncertainty_items
    )


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"max_directories": 1}, "directory-limit"),
        ({"max_entries": 1}, "entry-limit"),
    ],
)
def test_analyzer_directory_and_entry_caps_report_truncation(
    tmp_path: Path,
    kwargs: dict[str, int],
    code: str,
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("TWO = 2\n", encoding="utf-8")

    analysis = analyze_project.analyze_project(tmp_path, **kwargs)

    assert analysis.scan_summary["truncated"] is True
    assert any(item.get("code") == code for item in analysis.uncertainty_items)


def test_analyzer_directory_cap_accepts_boundary_and_reports_cap_plus_one(
    tmp_path: Path,
) -> None:
    at_limit = analyze_project.analyze_project(tmp_path, max_directories=1)
    (tmp_path / "child").mkdir()
    over_limit = analyze_project.analyze_project(tmp_path, max_directories=1)

    assert at_limit.scan_summary["truncated"] is False
    assert over_limit.scan_summary["truncated"] is True
    assert any(
        item.get("code") == "directory-limit" for item in over_limit.uncertainty_items
    )


def test_analyzer_entry_cap_accepts_boundary_and_reports_cap_plus_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    at_limit = analyze_project.analyze_project(tmp_path, max_entries=1)
    (tmp_path / "two.py").write_text("TWO = 2\n", encoding="utf-8")
    over_limit = analyze_project.analyze_project(tmp_path, max_entries=1)

    assert at_limit.scan_summary["truncated"] is False
    assert over_limit.scan_summary["truncated"] is True
    assert any(
        item.get("code") == "entry-limit" for item in over_limit.uncertainty_items
    )


def test_analyzer_output_is_deterministic_across_repeated_runs(tmp_path: Path) -> None:
    for name in ("zeta.py", "Alpha.py", "middle.py"):
        (tmp_path / name).write_text(f"VALUE = {name!r}\n", encoding="utf-8")

    first = analyze_project.format_json(analyze_project.analyze_project(tmp_path))
    second = analyze_project.format_json(analyze_project.analyze_project(tmp_path))

    assert first == second
