from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import validate_agentsmd
from bounded_io import DEFAULT_TEXT_FILE_BYTE_LIMIT


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate_agentsmd.py"


def write_agentsmd(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "AGENTS.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_standard_validation_rejects_unresolved_placeholders(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        """# Example

## Context

- [Project Name]
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard")

    assert any(violation.code == "placeholder" for violation in violations)


def test_create_standard_validation_rejects_h1_and_context_only(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        """# Example

## Context

This repository exists.
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard", intent="create")

    assert any(violation.code == "missing-required-section" for violation in violations)


def test_update_standard_validation_preserves_h1_and_context_only_structure(
    tmp_path: Path,
) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        """# Example

## Context

This repository exists.
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard", intent="update")

    assert not violations


def test_update_standard_still_rejects_placeholders_and_generic_filler(
    tmp_path: Path,
) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        """# Example

## Context

TODO: replace this.

## Notes

- Follow best practices.
- Edit <path> with {{COMMAND}}.
<!-- template comment -->
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard", intent="update")
    codes = {violation.code for violation in violations}

    assert "placeholder" in codes
    assert "generic-filler" in codes
    assert "template-comment" in codes


def test_length_budget_emits_warning_not_error_shape(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        "# Example\n\n## Context\n\n"
        + "\n".join(f"- Line {index}" for index in range(700)),
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard", intent="review")

    assert any(
        violation.code == "length-budget" and violation.severity == "warning"
        for violation in violations
    )


def test_existing_good_fixture_passes_create_standard() -> None:
    fixture = Path(__file__).parent / "fixtures" / "minimal-python" / "AGENTS.md"

    violations = validate_agentsmd.validate(fixture, mode="standard", intent="create")

    assert not violations


def test_bad_placeholder_fixture_fails_with_clear_codes() -> None:
    fixture = Path(__file__).parent / "fixtures" / "bad-placeholders" / "AGENTS.md"

    violations = validate_agentsmd.validate(fixture, mode="standard", intent="update")

    assert any(violation.code == "placeholder" for violation in violations)


@pytest.mark.parametrize(
    "command",
    [
        "docker compose down -v",
        "aws s3 rm s3://example-bucket/data --recursive",
        "rm -fr build",
        "git clean -xfd",
    ],
)
def test_unsafe_command_detector_flags_destructive_variants(
    tmp_path: Path, command: str
) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        f"""# Example

## Context

Destructive command example:

```bash
{command}
```
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard")

    assert any(violation.code == "unsafe-command" for violation in violations)


def test_unsafe_command_detector_allows_benign_make_target(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(
        tmp_path,
        """# Example

## Context

Local build command:

```bash
make target
```
""",
    )

    violations = validate_agentsmd.validate(agentsmd, mode="standard")

    assert not any(violation.code == "unsafe-command" for violation in violations)


def run_validate_cli(
    repo: Path, agentsmd: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--repo-root",
            str(repo),
            "--agents-file",
            str(agentsmd),
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_cli_rejects_outside_agents_file_with_json_only_error(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = "OUTSIDE_AGENTS_SECRET_MARKER"
    outside = tmp_path / f"{marker}.md"
    outside.write_text(f"# {marker}\n", encoding="utf-8")

    result = run_validate_cli(repo, outside)

    assert result.returncode == 2
    assert result.stderr == ""
    assert marker not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["findings"][0]["code"] == "agents-file-escape"
    assert payload["findings"][0]["path"] == "agents-file"


def test_validate_json_argument_errors_are_value_free(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(tmp_path, "# Example\n\n## Context\n")
    marker = "INVALID_MODE_MARKER"
    result = run_validate_cli(tmp_path, agentsmd, "--mode", marker)

    assert result.returncode == 2
    assert result.stderr == ""
    assert marker not in result.stdout
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-arguments"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"# Example\n\n## Context\n\n\xff", "agents-file-invalid-utf8"),
        (b"# Example\x00\n\n## Context\n", "agents-file-binary"),
    ],
)
def test_validate_cli_rejects_malformed_text_without_traceback(
    tmp_path: Path,
    payload: bytes,
    code: str,
) -> None:
    agentsmd = tmp_path / "AGENTS.md"
    agentsmd.write_bytes(payload)

    result = run_validate_cli(tmp_path, agentsmd)

    assert result.returncode == 2
    assert result.stderr == ""
    assert "Traceback" not in result.stdout
    assert json.loads(result.stdout)["findings"][0]["code"] == code


def test_validate_cli_rejects_outside_evidence_before_read(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agentsmd = write_agentsmd(repo, "# Example\n\n## Context\n")
    marker = "OUTSIDE_VALIDATE_EVIDENCE_MARKER"
    evidence = tmp_path / f"{marker}.md"
    evidence.write_bytes(b"\xff" + marker.encode())

    result = run_validate_cli(repo, agentsmd, "--evidence", str(evidence))

    assert result.returncode == 2
    assert result.stderr == ""
    assert marker not in result.stdout
    assert json.loads(result.stdout)["findings"][0]["code"] == "evidence-escape"


def test_validate_cli_accepts_byte_limit_boundary(tmp_path: Path) -> None:
    prefix = b"# Example\n\n## Context\n\n"
    agentsmd = tmp_path / "AGENTS.md"
    agentsmd.write_bytes(prefix + b"x" * (DEFAULT_TEXT_FILE_BYTE_LIMIT - len(prefix)))

    result = run_validate_cli(tmp_path, agentsmd)

    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "pass"


def test_validate_cli_rejects_byte_limit_plus_one(tmp_path: Path) -> None:
    agentsmd = tmp_path / "AGENTS.md"
    agentsmd.write_bytes(b"x" * (DEFAULT_TEXT_FILE_BYTE_LIMIT + 1))

    result = run_validate_cli(tmp_path, agentsmd)

    assert result.returncode == 2
    assert json.loads(result.stdout)["findings"][0]["code"] == "agents-file-too-large"


def test_validate_cli_rejects_hardlink_alias_when_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\n## Context\n", encoding="utf-8")
    agentsmd = repo / "AGENTS.md"
    try:
        os.link(outside, agentsmd)
    except OSError as exc:
        pytest.skip(f"Hardlink creation is unavailable: {exc}")

    result = run_validate_cli(repo, agentsmd)

    assert result.returncode == 2
    assert json.loads(result.stdout)["findings"][0]["code"] == "agents-file-hardlink"


@pytest.mark.skipif(os.name != "nt", reason="Windows path case behavior")
def test_validate_cli_accepts_repo_root_case_variant(tmp_path: Path) -> None:
    agentsmd = write_agentsmd(tmp_path, "# Example\n\n## Context\n")

    result = run_validate_cli(Path(str(tmp_path).swapcase()), agentsmd)

    assert result.returncode == 0
