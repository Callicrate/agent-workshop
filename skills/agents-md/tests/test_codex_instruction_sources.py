from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import analyze_project
import codex_instruction_sources


SKILL_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = SKILL_ROOT / "scripts" / "codex_instruction_sources.py"


def write_instruction(path: Path, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_audit_orders_root_and_nested_candidates(tmp_path: Path) -> None:
    write_instruction(tmp_path / "AGENTS.override.md", 3)
    write_instruction(tmp_path / "AGENTS.md", 5)
    write_instruction(tmp_path / "TEAM.md", 7)
    nested = tmp_path / "packages" / "api"
    write_instruction(nested / "AGENTS.override.md", 11)
    write_instruction(nested / "AGENTS.md", 13)
    write_instruction(nested / "TEAM.md", 17)

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(
        tmp_path,
        cwd=nested,
        fallback_filenames=["TEAM.md"],
    )

    files = audit["instruction_files"]
    assert [item["path"] for item in files] == [
        "AGENTS.override.md",
        "AGENTS.md",
        "TEAM.md",
        "packages/api/AGENTS.override.md",
        "packages/api/AGENTS.md",
        "packages/api/TEAM.md",
    ]
    assert [item["class"] for item in files] == ["override", "agents", "fallback"] * 2
    assert [item["depth"] for item in files] == [0, 0, 0, 2, 2, 2]
    assert [item["cumulative_candidate_metadata_bytes"] for item in files] == [
        3,
        8,
        15,
        26,
        39,
        56,
    ]
    assert [item["selected_by_documented_precedence"] for item in files] == [
        True,
        False,
        False,
    ] * 2
    assert audit["candidate_metadata_total_bytes"] == 56
    assert audit["selected_chain_bounds"] == {
        "status": "not-verified",
        "documented_precedence": [
            "AGENTS.override.md",
            "AGENTS.md",
            "configured-fallbacks",
        ],
        "uncertainty": "Readability, active fallback configuration, and fresh-run runtime loading are not verified.",
        "lower_bound_bytes": 0,
        "upper_bound_bytes": 14,
        "byte_limit": 32768,
        "could_exceed_byte_limit": False,
    }
    assert audit["runtime_attestation"] == "not-verified"
    assert audit["global_codex_home_docs"] == "excluded-from-project-total"


def test_audit_supports_an_explicit_only_fallback_and_never_emits_content(
    tmp_path: Path,
) -> None:
    marker = "UNIQUE_CONTENT_MARKER_NOT_METADATA"
    (tmp_path / "PROJECT.md").write_text(marker, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--project-doc-fallback-filename",
            "PROJECT.md",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker not in result.stdout
    audit = json.loads(result.stdout)
    assert audit["candidate_count"] == 1
    assert audit["instruction_files"] == [
        {
            "path": "PROJECT.md",
            "filename": "PROJECT.md",
            "class": "fallback",
            "depth": 0,
            "file_bytes": len(marker),
            "cumulative_candidate_metadata_bytes": len(marker),
            "byte_limit": 32768,
            "exceeds_byte_limit_if_selected": False,
            "selected_by_documented_precedence": True,
        }
    ]


def test_audit_keeps_both_primary_candidates_in_the_same_directory(
    tmp_path: Path,
) -> None:
    write_instruction(tmp_path / "AGENTS.override.md")
    write_instruction(tmp_path / "AGENTS.md")

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(tmp_path)

    assert [
        (item["filename"], item["class"]) for item in audit["instruction_files"]
    ] == [
        ("AGENTS.override.md", "override"),
        ("AGENTS.md", "agents"),
    ]


def test_audit_cli_rejects_working_directory_outside_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--cwd",
            str(outside),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-configuration"


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        "nested/TEAM.md",
        r"nested\TEAM.md",
        r"C:\TEAM.md",
        "TEAM:ads.md",
        "TEAM.md ",
        "TEAM.md.",
        "CON.md",
        "lPt9.txt",
        "bad\nname.md",
        "bad\u2028name.md",
        "TEAM?.md",
        "TEAM*.md",
        "TEAM<.md",
        "TEAM>.md",
        'TEAM".md',
        "TEAM|.md",
    ],
)
def test_fallback_filenames_are_validated(filename: str) -> None:
    with pytest.raises(ValueError):
        codex_instruction_sources.normalize_project_doc_fallback_filenames([filename])


def test_fallback_filenames_are_stably_deduplicated(tmp_path: Path) -> None:
    write_instruction(tmp_path / "TEAM.md")

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(
        tmp_path,
        fallback_filenames=["TEAM.md", "TEAM.md"],
    )

    assert audit["fallback_filenames"] == ["TEAM.md"]
    assert [item["path"] for item in audit["instruction_files"]] == ["TEAM.md"]


def test_fallback_filename_normalization_preserves_distinct_case_and_unicode_names() -> (
    None
):
    normalized = codex_instruction_sources.normalize_project_doc_fallback_filenames(
        ["TEAM.md", "team.md"]
    )

    assert normalized == ("TEAM.md", "team.md")
    assert codex_instruction_sources.normalize_project_doc_fallback_filenames(
        ["straße.md", "strasse.md", "İ.md", "i̇.md"]
    ) == ("straße.md", "strasse.md", "İ.md", "i̇.md")


def test_fallback_aliases_dedupe_by_native_filesystem_identity(tmp_path: Path) -> None:
    write_instruction(tmp_path / "TEAM.md")
    if os.name != "nt":
        write_instruction(tmp_path / "team.md")

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(
        tmp_path,
        fallback_filenames=["TEAM.md", "team.md"],
    )

    expected_count = 1 if os.name == "nt" else 2
    assert len(audit["instruction_files"]) == expected_count
    assert audit["instruction_files"][0]["selected_by_documented_precedence"] is True
    assert audit["selected_chain_bounds"]["upper_bound_bytes"] == 1


def test_audit_cli_rejects_invalid_fallback_filename(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--project-doc-fallback-filename",
            "nested/TEAM.md",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-configuration"


def test_audit_json_error_is_value_free_for_invalid_fallback(tmp_path: Path) -> None:
    marker = "OUTSIDE_FALLBACK_MARKER"
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--project-doc-fallback-filename",
            f"nested/{marker}.md",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert marker not in result.stdout
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-configuration"


@pytest.mark.parametrize(
    "filename", ["CON.md", "TEAM:ads.md", "bad\nname.md", "TEAM?.md", "TEAM|.md"]
)
def test_audit_cli_rejects_cross_platform_hostile_fallback_names(
    tmp_path: Path, filename: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--project-doc-fallback-filename",
            filename,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-configuration"


def test_audit_excludes_and_reports_escaping_instruction_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}.md"
    outside.write_text("outside", encoding="utf-8")
    candidate = tmp_path / "AGENTS.md"
    try:
        os.symlink(outside, candidate)
    except OSError as exc:
        outside.unlink()
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    try:
        audit = codex_instruction_sources.audit_codex_project_instruction_sources(
            tmp_path
        )

        assert audit["instruction_files"] == []
        assert audit["diagnostics"] == [
            {
                "code": "outside-root-symlink",
                "path": "AGENTS.md",
                "filename": "AGENTS.md",
                "class": "agents",
                "depth": 0,
                "message": "Candidate resolves outside the repository root and was excluded",
            }
        ]
    finally:
        outside.unlink(missing_ok=True)


def test_byte_limit_is_an_advisory_with_a_custom_threshold(tmp_path: Path) -> None:
    write_instruction(tmp_path / "AGENTS.md", 32_769)

    default_audit = codex_instruction_sources.audit_codex_project_instruction_sources(
        tmp_path
    )
    custom_audit = codex_instruction_sources.audit_codex_project_instruction_sources(
        tmp_path, byte_limit=40_000
    )

    assert (
        default_audit["instruction_files"][0]["exceeds_byte_limit_if_selected"] is True
    )
    assert (
        custom_audit["instruction_files"][0]["exceeds_byte_limit_if_selected"] is False
    )
    assert default_audit["selected_chain_bounds"]["could_exceed_byte_limit"] is True


def test_same_directory_candidates_do_not_create_a_false_combined_instruction_chain(
    tmp_path: Path,
) -> None:
    write_instruction(tmp_path / "AGENTS.override.md", 20_000)
    write_instruction(tmp_path / "AGENTS.md", 20_000)

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(tmp_path)

    assert audit["candidate_metadata_total_bytes"] == 40_000
    assert "total_project_instruction_bytes" not in audit
    assert all("likely_truncated" not in item for item in audit["instruction_files"])
    assert [
        item["selected_by_documented_precedence"] for item in audit["instruction_files"]
    ] == [True, False]
    assert audit["selected_chain_bounds"]["upper_bound_bytes"] == 20_000
    assert audit["selected_chain_bounds"]["could_exceed_byte_limit"] is False


def test_documented_precedence_skips_empty_candidates_before_applying_byte_advisory(
    tmp_path: Path,
) -> None:
    write_instruction(tmp_path / "AGENTS.override.md", 0)
    write_instruction(tmp_path / "AGENTS.md", 40_000)

    audit = codex_instruction_sources.audit_codex_project_instruction_sources(tmp_path)

    assert [
        item["selected_by_documented_precedence"] for item in audit["instruction_files"]
    ] == [False, True]
    assert audit["instruction_files"][1]["exceeds_byte_limit_if_selected"] is True
    assert audit["selected_chain_bounds"]["upper_bound_bytes"] == 40_000
    assert audit["selected_chain_bounds"]["could_exceed_byte_limit"] is True

    markdown = analyze_project.format_markdown(
        analyze_project.ProjectAnalysis(codex_project_instruction_audit=audit)
    )
    assert "Selected-chain bounds: `0` to `40000` bytes (not-verified)" in markdown
    assert "documented selected-chain upper bound exceeds the byte limit" in markdown


def test_markdown_rendering_escapes_untrusted_path_metadata() -> None:
    hostile_path = "nested/evil`\n# injected.md"
    audit = {
        "candidate_count": 1,
        "candidate_metadata_total_bytes": 1,
        "byte_limit": 32_768,
        "runtime_attestation": "not-verified",
        "selected_chain_bounds": {
            "lower_bound_bytes": 0,
            "upper_bound_bytes": 1,
            "status": "not-verified",
            "documented_precedence": [
                "AGENTS.override.md",
                "AGENTS.md",
                "configured-fallbacks",
            ],
        },
        "instruction_files": [
            {"path": hostile_path, "class": "fallback", "file_bytes": 1}
        ],
        "diagnostics": [{"code": "outside-root-symlink", "path": hostile_path}],
    }
    analysis = analyze_project.ProjectAnalysis(
        agents_files=[hostile_path],
        codex_project_instruction_audit=audit,
    )

    markdown = analyze_project.format_markdown(analysis)

    assert "\n# injected.md" not in markdown
    assert "``nested/evil`\\u000a# injected.md``" in markdown
