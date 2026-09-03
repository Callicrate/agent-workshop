from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import run_agentsmd_fixture_checks as fixture_checks


SKILL_ROOT = Path(__file__).resolve().parents[1]
HARNESS_TIMEOUT_SECONDS = 120
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")


def run_fixture_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "run_agentsmd_fixture_checks.py"),
            *arguments,
        ],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=HARNESS_TIMEOUT_SECONDS,
    )


def current_process_handle_count() -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not kernel32.GetProcessHandleCount(
        kernel32.GetCurrentProcess(), ctypes.byref(count)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return count.value


def windows_child_code(*, ready: Path, marker: Path, delay: float = 1) -> str:
    return (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        f"time.sleep({delay}); "
        f"Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )


def windows_parent_code(*, child_code: str, ready: Path) -> str:
    return (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"ready = Path({str(ready)!r}); "
        "deadline = time.monotonic() + 2; "
        'exec("while not ready.exists() and time.monotonic() < deadline:\\n'
        '    time.sleep(0.01)"); time.sleep(10)'
    )


def test_fixture_command_normal_completion() -> None:
    result = fixture_checks.run_with_timeout(
        [sys.executable, "-c", "print('fixture-complete')"]
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "fixture-complete"
    assert not result.stderr


def test_fixture_command_timeout_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(fixture_checks, "DEFAULT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(fixture_checks, "KILL_GRACE_SECONDS", 1)

    result = fixture_checks.run_with_timeout(
        [sys.executable, "-c", "import time; time.sleep(1)"]
    )

    assert result.returncode == 124
    assert "Timed out after 0.05s" in result.stderr


def test_fixture_timeout_terminates_spawned_children(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "child-survived.txt"
    ready = tmp_path / "child-ready.txt"
    ignore_break = (
        "import signal; signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        if os.name == "nt"
        else ""
    )
    child_code = (
        f"{ignore_break}import time; from pathlib import Path; "
        f"Path({str(ready)!r}).write_text('ready', encoding='utf-8'); "
        f"time.sleep(1); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"ready = Path({str(ready)!r}); "
        "deadline = time.monotonic() + 2; "
        'exec("while not ready.exists() and time.monotonic() < deadline:\\n'
        '    time.sleep(0.01)"); time.sleep(10)'
    )
    monkeypatch.setattr(fixture_checks, "DEFAULT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(fixture_checks, "KILL_GRACE_SECONDS", 1)

    result = fixture_checks.run_with_timeout([sys.executable, "-c", parent_code])
    time.sleep(1.25)

    assert result.returncode == 124
    assert ready.exists()
    assert not marker.exists()


@WINDOWS_ONLY
def test_windows_fixture_timeout_terminates_spawned_grandchildren(
    tmp_path: Path, monkeypatch
) -> None:
    ready = tmp_path / "grandchild-ready.txt"
    marker = tmp_path / "grandchild-survived.txt"
    grandchild_code = windows_child_code(ready=ready, marker=marker)
    child_code = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        "time.sleep(10)"
    )
    parent_code = windows_parent_code(child_code=child_code, ready=ready)
    monkeypatch.setattr(fixture_checks, "DEFAULT_TIMEOUT_SECONDS", 0.5)

    result = fixture_checks.run_with_timeout([sys.executable, "-c", parent_code])
    time.sleep(1.25)

    assert result.returncode == 124
    assert ready.exists()
    assert not marker.exists()


@WINDOWS_ONLY
def test_windows_job_assignment_failure_is_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "fixture-started.txt"

    def fail_assignment(job, proc) -> None:
        del job, proc
        raise OSError("injected assignment failure")

    monkeypatch.setattr(fixture_checks._WindowsJob, "assign", fail_assignment)
    before = current_process_handle_count()

    with pytest.raises(OSError, match="injected assignment failure"):
        fixture_checks.run_with_timeout(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            ]
        )

    assert not marker.exists()
    assert current_process_handle_count() == before


@WINDOWS_ONLY
def test_windows_job_creation_failure_does_not_start_fixture(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "fixture-started.txt"

    class FailingJob:
        def __init__(self) -> None:
            raise OSError("injected job creation failure")

    monkeypatch.setattr(fixture_checks, "_WindowsJob", FailingJob)

    with pytest.raises(OSError, match="injected job creation failure"):
        fixture_checks.run_with_timeout(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            ]
        )

    assert not marker.exists()


@WINDOWS_ONLY
def test_windows_popen_failure_closes_the_job(monkeypatch) -> None:
    events: list[str] = []

    class TrackingJob:
        def __init__(self) -> None:
            events.append("created")

        def close(self) -> None:
            events.append("closed")

    def fail_popen(*args, **kwargs):
        del args, kwargs
        raise OSError("injected process creation failure")

    monkeypatch.setattr(fixture_checks, "_WindowsJob", TrackingJob)
    monkeypatch.setattr(fixture_checks.subprocess, "Popen", fail_popen)

    with pytest.raises(OSError, match="injected process creation failure"):
        fixture_checks.run_with_timeout([sys.executable, "-c", "pass"])

    assert events == ["created", "closed"]


@WINDOWS_ONLY
def test_windows_repeated_timeouts_leave_no_descendants_or_handles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(fixture_checks, "DEFAULT_TIMEOUT_SECONDS", 0.3)
    before = current_process_handle_count()
    markers: list[Path] = []

    for cycle in range(12):
        ready = tmp_path / f"child-ready-{cycle}.txt"
        marker = tmp_path / f"child-survived-{cycle}.txt"
        markers.append(marker)
        child_code = windows_child_code(ready=ready, marker=marker, delay=0.6)
        parent_code = windows_parent_code(child_code=child_code, ready=ready)

        result = fixture_checks.run_with_timeout([sys.executable, "-c", parent_code])

        assert result.returncode == 124
        assert ready.exists()

    time.sleep(0.8)

    assert not any(marker.exists() for marker in markers)
    assert current_process_handle_count() == before


def test_existing_fixture_checks_still_run() -> None:
    result = run_fixture_cli()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS minimal-python structural" in result.stdout
    assert "PASS bad-stale-path semantic" in result.stdout


def test_fixture_manifest_is_the_bounded_case_source() -> None:
    cases = fixture_checks.load_fixture_manifest(
        SKILL_ROOT / "tests" / "fixtures" / fixture_checks.MANIFEST_FILENAME
    )

    assert len(cases) == 14
    assert len({case.case_id for case in cases}) == len(cases)
    assert cases[0].case_id == "minimal-python-structural"


def test_fixture_cli_selects_one_stable_case() -> None:
    result = run_fixture_cli("--fixture", "minimal-python-structural")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS minimal-python structural" in result.stdout
    assert "PASS minimal-python semantic" not in result.stdout


def test_fixture_cli_json_summary_is_deterministic_and_path_free() -> None:
    result = run_fixture_cli("--fixture", "minimal-python-structural", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "cases": [{"id": "minimal-python-structural", "status": "pass"}],
        "status": "pass",
        "summary": {"failed": 0, "passed": 1, "timed_out": 0, "total": 1},
    }
    assert "fixtures" not in result.stdout
    assert str(SKILL_ROOT) not in result.stdout


def test_fixture_cli_help_is_nonexecuting() -> None:
    result = run_fixture_cli("--help")

    assert result.returncode == 0
    assert "--fixture STABLE_NAME" in result.stdout
    assert "PASS " not in result.stdout
    assert not result.stderr


def test_fixture_cli_errors_are_value_free_and_json_safe() -> None:
    unknown_value = "unexpected-fixture-value"
    unknown = run_fixture_cli("--fixture", unknown_value)
    duplicate = run_fixture_cli(
        "--fixture", "minimal-python-structural", "--fixture", unknown_value
    )
    missing = run_fixture_cli("--fixture", "--json")
    json_unknown = run_fixture_cli("--json", "--fixture", unknown_value)

    for result in (unknown, duplicate, missing):
        assert result.returncode == 2
        assert unknown_value not in result.stdout + result.stderr
    assert json_unknown.returncode == 2
    payload = json.loads(json_unknown.stdout)
    assert payload["status"] == "error"
    assert payload["summary"] == {
        "failed": 0,
        "passed": 0,
        "timed_out": 0,
        "total": 0,
    }
    assert payload["cases"] == []
    assert payload["error"] == {"code": "unknown fixture"}
    assert unknown_value not in json_unknown.stdout + json_unknown.stderr


def test_template_hygiene_check_still_runs() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "check_agentsmd_templates.py"),
            str(SKILL_ROOT),
        ],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=HARNESS_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "template hygiene check passed" in result.stdout
