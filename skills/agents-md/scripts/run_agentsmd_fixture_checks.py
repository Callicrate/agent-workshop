#!/usr/bin/env python3
"""Run the manifest-backed agents-md validator fixture checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

if os.name == "nt":
    import ctypes
    from ctypes import wintypes


DEFAULT_TIMEOUT_SECONDS = 30
KILL_GRACE_SECONDS = 5
MANIFEST_FILENAME = "fixture-manifest.json"
MAX_FIXTURE_CASES = 64
_VALIDATORS = {"validate", "semantic"}
_JSON_STATUSES = {"pass", "fail"}
_CASE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


if os.name == "nt":
    _CREATE_SUSPENDED = 0x00000004
    _ERROR_NO_MORE_FILES = 18
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _RESUME_THREAD_FAILED = 0xFFFFFFFF

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    )
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    )
    _kernel32.Thread32Next.restype = wintypes.BOOL

    def _windows_error(api_name: str) -> OSError:
        error_code = ctypes.get_last_error()
        message = ctypes.FormatError(error_code).strip()
        return OSError(error_code, f"{api_name} failed: {message}")

    def _close_windows_handle(handle: int) -> None:
        if not _kernel32.CloseHandle(handle):
            raise _windows_error("CloseHandle")

    class _WindowsJob:
        """A non-inheritable fixture job that owns its complete process tree."""

        def __init__(self) -> None:
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise _windows_error("CreateJobObjectW")
            self._handle: int | None = handle
            limits = _JobObjectExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = _windows_error("SetInformationJobObject")
                self.close()
                raise error

        def assign(self, proc: subprocess.Popen[str]) -> None:
            if self._handle is None:
                raise RuntimeError("fixture job is closed")
            process_handle = int(getattr(proc, "_handle"))
            if not _kernel32.AssignProcessToJobObject(self._handle, process_handle):
                raise _windows_error("AssignProcessToJobObject")

        def terminate(self, exit_code: int) -> None:
            if self._handle is None:
                raise RuntimeError("fixture job is closed")
            if not _kernel32.TerminateJobObject(self._handle, exit_code):
                raise _windows_error("TerminateJobObject")

        def close(self) -> None:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            _close_windows_handle(handle)

    def _primary_thread_id(process_id: int) -> int:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if snapshot == _INVALID_HANDLE_VALUE:
            raise _windows_error("CreateToolhelp32Snapshot")
        thread_ids: list[int] = []
        try:
            entry = _ThreadEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            if not _kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                raise _windows_error("Thread32First")
            while True:
                if entry.th32OwnerProcessID == process_id:
                    thread_ids.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                if _kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                    continue
                if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
                    raise _windows_error("Thread32Next")
                break
        finally:
            _close_windows_handle(snapshot)
        if len(thread_ids) != 1:
            raise RuntimeError("suspended fixture process has no unique primary thread")
        return thread_ids[0]

    def _resume_primary_thread(process_id: int) -> None:
        thread_id = _primary_thread_id(process_id)
        thread_handle = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_id)
        if not thread_handle:
            raise _windows_error("OpenThread")
        try:
            previous_count = _kernel32.ResumeThread(thread_handle)
            if previous_count == _RESUME_THREAD_FAILED:
                raise _windows_error("ResumeThread")
            if previous_count != 1:
                raise RuntimeError("fixture primary thread was not singly suspended")
        finally:
            _close_windows_handle(thread_handle)


@dataclass(frozen=True)
class FixtureCase:
    """One explicit validator subprocess contract from the fixture manifest."""

    case_id: str
    label: str
    validator: str
    fixture: str
    repo_root: bool
    arguments: tuple[str, ...]
    expected_exit: int
    expected_json_status: str | None = None


class FixtureArgumentError(ValueError):
    """An argument error whose safe classification can be rendered by the CLI."""


class FixtureArgumentParser(argparse.ArgumentParser):
    """Avoid echoing user-supplied values in command-line errors."""

    def error(self, message: str) -> None:
        raise FixtureArgumentError(message)


class SingleFixtureAction(argparse.Action):
    """Accept exactly one fixture selection without reflecting its value."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        del option_string
        if getattr(namespace, self.dest, None) is not None:
            parser.error("duplicate fixture selection")
        setattr(namespace, self.dest, values)


def _read_temp_file(handle: Any) -> str:
    handle.flush()
    handle.seek(0)
    return handle.read()


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Kill the POSIX fixture process group."""
    if proc.poll() is not None:
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass


def _close_windows_process_handle(proc: subprocess.Popen[str]) -> None:
    process_handle = getattr(proc, "_handle", None)
    if process_handle is not None:
        process_handle.Close()


def _start_windows_process(
    command: list[str], stdout_file: Any, stderr_file: Any, job: Any
) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        command,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED,
    )
    assigned = False
    try:
        job.assign(proc)
        assigned = True
        _resume_primary_thread(proc.pid)
    except BaseException:
        try:
            if assigned:
                job.terminate(1)
            else:
                proc.kill()
            proc.wait(timeout=KILL_GRACE_SECONDS)
        finally:
            _close_windows_process_handle(proc)
        raise
    return proc


def _run_windows_with_timeout(
    command: list[str], stdout_file: Any, stderr_file: Any
) -> subprocess.CompletedProcess[str]:
    job = _WindowsJob()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = _start_windows_process(command, stdout_file, stderr_file, job)
        timed_out = False
        try:
            proc.wait(timeout=DEFAULT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            job.terminate(124)
            proc.wait(timeout=KILL_GRACE_SECONDS)

        stdout = _read_temp_file(stdout_file)
        stderr = _read_temp_file(stderr_file)
        if timed_out:
            stderr = (stderr or "") + f"\nTimed out after {DEFAULT_TIMEOUT_SECONDS}s"
            return subprocess.CompletedProcess(command, 124, stdout, stderr)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    finally:
        try:
            job.close()
        finally:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.wait(timeout=KILL_GRACE_SECONDS)
                finally:
                    _close_windows_process_handle(proc)


def _run_posix_with_timeout(
    command: list[str], stdout_file: Any, stderr_file: Any
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        command,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        proc.wait(timeout=DEFAULT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(proc)
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    stdout = _read_temp_file(stdout_file)
    stderr = _read_temp_file(stderr_file)
    if timed_out:
        stderr = (stderr or "") + f"\nTimed out after {DEFAULT_TIMEOUT_SECONDS}s"
        return subprocess.CompletedProcess(command, 124, stdout, stderr)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def run_with_timeout(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one fixture command with a finite timeout and captured output."""
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file,
    ):
        if os.name == "nt":
            return _run_windows_with_timeout(command, stdout_file, stderr_file)
        return _run_posix_with_timeout(command, stdout_file, stderr_file)


def _manifest_error() -> ValueError:
    return ValueError("invalid fixture manifest")


def load_fixture_manifest(manifest_path: Path) -> tuple[FixtureCase, ...]:
    """Load the bounded, explicit case list used for execution and selection."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _manifest_error() from exc

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise _manifest_error()
    items = payload.get("fixtures")
    if not isinstance(items, list) or not items or len(items) > MAX_FIXTURE_CASES:
        raise _manifest_error()

    cases: list[FixtureCase] = []
    case_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise _manifest_error()
        case_id = item.get("id")
        label = item.get("label")
        validator = item.get("validator")
        fixture = item.get("fixture")
        repo_root = item.get("repo_root")
        arguments = item.get("arguments")
        expected_exit = item.get("expected_exit")
        expected_json_status = item.get("expected_json_status")
        if (
            not isinstance(case_id, str)
            or not _CASE_ID_PATTERN.fullmatch(case_id)
            or case_id in case_ids
            or not isinstance(label, str)
            or not label
            or validator not in _VALIDATORS
            or not isinstance(fixture, str)
            or not fixture
            or "/" in fixture
            or "\\" in fixture
            or fixture in {".", ".."}
            or not isinstance(repo_root, bool)
            or not isinstance(arguments, list)
            or not all(isinstance(argument, str) for argument in arguments)
            or any(
                argument in {"--agents-file", "--repo-root"}
                or argument.startswith("--agents-file=")
                or argument.startswith("--repo-root=")
                for argument in arguments
            )
            or expected_exit not in {0, 1}
            or (
                expected_json_status is not None
                and expected_json_status not in _JSON_STATUSES
            )
        ):
            raise _manifest_error()
        case_ids.add(case_id)
        cases.append(
            FixtureCase(
                case_id=case_id,
                label=label,
                validator=validator,
                fixture=fixture,
                repo_root=repo_root,
                arguments=tuple(arguments),
                expected_exit=expected_exit,
                expected_json_status=expected_json_status,
            )
        )
    return tuple(cases)


def build_case_command(case: FixtureCase, skill_dir: Path) -> list[str]:
    """Build a command only from an explicit, validated manifest entry."""
    fixtures_dir = skill_dir / "tests" / "fixtures"
    fixture_dir = fixtures_dir / case.fixture
    script_name = {
        "validate": "validate_agentsmd.py",
        "semantic": "semantic_check_agentsmd.py",
    }[case.validator]
    command = [sys.executable, str(skill_dir / "scripts" / script_name)]
    if case.repo_root:
        command.extend(["--repo-root", str(fixture_dir)])
    command.extend(["--agents-file", str(fixture_dir / "AGENTS.md")])
    command.extend(case.arguments)
    return command


def _case_result(
    case: FixtureCase,
) -> tuple[bool, str, subprocess.CompletedProcess[str]]:
    result = run_with_timeout(
        build_case_command(case, Path(__file__).resolve().parents[1])
    )
    if result.returncode == 124:
        return False, "timeout", result
    if result.returncode != case.expected_exit:
        return False, "fail", result
    if case.expected_json_status is not None:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, "fail", result
        if payload.get("status") != case.expected_json_status:
            return False, "fail", result
    return True, "pass", result


def run_case(case: FixtureCase, *, json_mode: bool = False) -> dict[str, str]:
    """Run and report one selected manifest case."""
    if not json_mode:
        print(f"START {case.label}")
    passed, status, result = _case_result(case)
    if not json_mode:
        if passed:
            print(f"PASS {case.label}")
        elif status == "timeout":
            print(f"TIMEOUT {case.label}")
        else:
            print(
                f"FAIL {case.label}: expected exit {case.expected_exit}, "
                f"got {result.returncode}"
            )
        if not passed:
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip())
    return {"id": case.case_id, "status": status}


def build_parser() -> FixtureArgumentParser:
    parser = FixtureArgumentParser(
        description="Run bounded agents-md validator fixture checks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a deterministic JSON summary only",
    )
    parser.add_argument(
        "--fixture",
        action=SingleFixtureAction,
        metavar="STABLE_NAME",
        help="run one manifest fixture case by stable name",
    )
    return parser


def _summary(status: str, case_results: Sequence[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": status,
        "summary": {
            "total": len(case_results),
            "passed": sum(item["status"] == "pass" for item in case_results),
            "failed": sum(item["status"] == "fail" for item in case_results),
            "timed_out": sum(item["status"] == "timeout" for item in case_results),
        },
        "cases": list(case_results),
    }


def _emit_error(*, json_mode: bool, code: str) -> int:
    if json_mode:
        payload = _summary("error", [])
        payload["error"] = {"code": code}
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    else:
        print(f"error: {code}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in arguments
    parser = build_parser()
    try:
        parsed = parser.parse_args(arguments)
    except FixtureArgumentError as exc:
        code = (
            "duplicate fixture selection"
            if str(exc) == "duplicate fixture selection"
            else "invalid fixture runner arguments"
        )
        return _emit_error(json_mode=json_requested, code=code)

    try:
        cases = load_fixture_manifest(
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / MANIFEST_FILENAME
        )
    except ValueError:
        return _emit_error(json_mode=parsed.json, code="invalid fixture manifest")

    selected_cases = cases
    if parsed.fixture is not None:
        selected_cases = tuple(case for case in cases if case.case_id == parsed.fixture)
        if not selected_cases:
            return _emit_error(json_mode=parsed.json, code="unknown fixture")

    results = [run_case(case, json_mode=parsed.json) for case in selected_cases]
    overall_status = (
        "pass" if all(item["status"] == "pass" for item in results) else "fail"
    )
    if parsed.json:
        print(
            json.dumps(
                _summary(overall_status, results), separators=(",", ":"), sort_keys=True
            )
        )
    return 0 if overall_status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
