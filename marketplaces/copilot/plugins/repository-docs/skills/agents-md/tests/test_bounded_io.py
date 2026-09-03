from __future__ import annotations

from pathlib import Path

import pytest

import bounded_io


def test_reader_rejects_file_replaced_between_stat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("# Original\n", encoding="utf-8")
    original_open = bounded_io._open_no_follow
    replaced = False

    def replacing_open(path: Path) -> int:
        nonlocal replaced
        if not replaced:
            replaced = True
            target.unlink()
            target.write_text("# Replacement\n", encoding="utf-8")
        return original_open(path)

    monkeypatch.setattr(bounded_io, "_open_no_follow", replacing_open)

    with pytest.raises(bounded_io.InputFileError, match="changed") as exc_info:
        bounded_io.read_repo_text(tmp_path, target, label="agents-file")

    assert exc_info.value.code == "agents-file-changed"
