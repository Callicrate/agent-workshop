#!/usr/bin/env python3
"""Read a Databricks Jobs 2.2 run through the CLI without changing it."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ctypes import wintypes

MAX_PAGES: Final = 100
MAX_TASKS: Final = 10_000
MAX_ITERATIONS: Final = 10_000
MAX_REPAIR_HISTORY_ITEMS: Final = 10_000
MAX_REPAIR_TASK_RUN_IDS: Final = 10_000
MAX_STDOUT_BYTES: Final = 25 * 1024 * 1024
MAX_STDERR_BYTES: Final = 64 * 1024
MAX_MESSAGE_CHARS: Final = 2_048
MAX_REDACTION_WINDOW_CHARS: Final = MAX_MESSAGE_CHARS + 4_096
MAX_RUN_ID: Final = 9_223_372_036_854_775_807
CALL_TIMEOUT_SECONDS: Final = 30.0
OVERALL_TIMEOUT_SECONDS: Final = 120.0
READ_CHUNK_BYTES: Final = 64 * 1024

CURRENT_STATUS_STATES: Final = frozenset(
    {"BLOCKED", "PENDING", "QUEUED", "RUNNING", "TERMINATING", "TERMINATED", "WAITING"}
)
LEGACY_LIFECYCLE_STATES: Final = frozenset(
    {
        "PENDING",
        "RUNNING",
        "TERMINATING",
        "TERMINATED",
        "SKIPPED",
        "INTERNAL_ERROR",
        "BLOCKED",
        "WAITING_FOR_RETRY",
        "QUEUED",
    }
)
TERMINATION_CODES: Final = frozenset(
    {
        "SUCCESS",
        "CANCELED",
        "DRIVER_ERROR",
        "CLUSTER_ERROR",
        "CLUSTER_TERMINATED_BY_USER",
        "REPOSITORY_CHECKOUT_FAILED",
        "INVALID_CLUSTER_REQUEST",
        "WORKSPACE_RUN_LIMIT_EXCEEDED",
        "FEATURE_DISABLED",
        "CLUSTER_REQUEST_LIMIT_EXCEEDED",
        "STORAGE_ACCESS_ERROR",
        "RUN_EXECUTION_ERROR",
        "UNAUTHORIZED_ERROR",
        "LIBRARY_INSTALLATION_ERROR",
        "MAX_CONCURRENT_RUNS_EXCEEDED",
        "MAX_SPARK_CONTEXTS_EXCEEDED",
        "RESOURCE_NOT_FOUND",
        "INVALID_RUN_CONFIGURATION",
        "INTERNAL_ERROR",
        "CLOUD_FAILURE",
        "MAX_JOB_QUEUE_SIZE_EXCEEDED",
        "SKIPPED",
        "USER_CANCELED",
        "BUDGET_POLICY_LIMIT_EXCEEDED",
        "DISABLED",
        "SUCCESS_WITH_FAILURES",
        "BREAKING_CHANGE",
    }
)
LEGACY_RESULT_STATES: Final = frozenset(
    {
        "SUCCESS",
        "FAILED",
        "TIMEDOUT",
        "CANCELED",
        "MAXIMUM_CONCURRENT_RUNS_REACHED",
        "UPSTREAM_CANCELED",
        "UPSTREAM_FAILED",
        "EXCLUDED",
        "EVICTED",
        "SUCCESS_WITH_FAILURES",
        "UPSTREAM_EVICTED",
        "DISABLED",
    }
)
RUN_ID_PATTERN: Final = re.compile(r"^[1-9][0-9]{0,18}$")
PROFILE_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PAGE_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9._~+/=-]{1,4096}$")

REDACTION_PATTERNS: Final = (
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*"
            r"(bearer|basic|dpop)\s+[^\s,;\"']*"
        ),
        r"\1: \2 [REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(bearer|basic|dpop)\s+[A-Za-z0-9._~+/=-]*"),
        r"\1 [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"
            r"\s*[:=]\s*)[\"']?[^\s,;\"']+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\b(dapi)[A-Za-z0-9_-]*"), r"\1[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_.-]*"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"(?i)([?&](?:access_token|api[_-]?key|token|secret)=)[^&\s]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^@/\s]+@"), r"\1[REDACTED]@"),
)


class MonitorError(Exception):
    """A safe, structured helper failure."""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = redact(message)
        self.detail = redact(detail)


class InputError(MonitorError):
    """Input errors have a distinct process exit status."""


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    profile: str


@dataclass(frozen=True)
class RunState:
    status: str
    termination: str
    source: str
    state_message: str
    is_terminal: bool
    is_success: bool
    outcome_complete: bool
    mixed_state_conflict: bool


class _WindowsJobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _WindowsIoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _WindowsJobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _WindowsJobBasicLimitInformation),
        ("io_info", _WindowsIoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class WindowsJob:
    """A Windows process-tree boundary that kills descendants when closed."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        try:
            limits = _WindowsJobExtendedLimitInformation()
            limits.basic_limit_information.limit_flags = (
                self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                handle,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.AssignProcessToJobObject(
                handle, wintypes.HANDLE(process._handle)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.CloseHandle(handle)
            self._handle = None
            raise

    def close(self) -> None:
        """Close once. Kill-on-close applies only while the job has live processes."""

        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def redact(value: object) -> str:
    """Return a bounded, newline-safe diagnostic with credentials removed."""

    text = str(value).replace("\r", " ").replace("\n", " ")[:MAX_REDACTION_WINDOW_CHARS]
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:MAX_MESSAGE_CHARS]


def emit_json(payload: Mapping[str, Any]) -> None:
    """Print the intended command result, the one intentional CLI print path."""

    print(json.dumps(payload, indent=2, sort_keys=True))


def usage() -> str:
    return (
        "Usage: bash scripts/check_run_status.sh <run_id> <profile>\n"
        '   or: TOOL_INPUT=\'{"run_id":"12345","profile":"dev"}\' '
        "bash scripts/check_run_status.sh"
    )


def validate_request(run_id_value: object, profile_value: object) -> RunRequest:
    """Validate user-controlled values before any CLI process can start."""

    run_id = str(run_id_value).strip() if run_id_value is not None else ""
    profile = str(profile_value).strip() if profile_value is not None else ""
    if not normalized_run_id(run_id):
        raise InputError(
            "invalid_run_id",
            "run_id must be a decimal int64 Databricks run ID from 1 through 9223372036854775807",
        )
    if not PROFILE_PATTERN.fullmatch(profile):
        raise InputError(
            "invalid_profile",
            "profile must be a nonempty named Databricks profile using letters, numbers, dot, underscore, or hyphen",
        )
    return RunRequest(run_id=run_id, profile=profile)


def parse_request(argv: Sequence[str], tool_input: str | None) -> RunRequest | None:
    """Parse exactly one explicit input mode; never substitute DEFAULT."""

    arguments = list(argv)
    if len(arguments) == 1 and arguments[0] in {"-h", "--help"}:
        return None
    if arguments:
        if len(arguments) != 2:
            raise InputError(
                "invalid_arguments",
                "pass exactly <run_id> <profile>, or provide both fields in TOOL_INPUT",
            )
        return validate_request(arguments[0], arguments[1])

    raw_input = (tool_input or "").strip()
    if not raw_input:
        raise InputError(
            "missing_input",
            "run_id and an explicit profile are required as positional arguments or in TOOL_INPUT",
        )
    try:
        parsed = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        raise InputError(
            "invalid_tool_input", "TOOL_INPUT must be a JSON object"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise InputError("invalid_tool_input", "TOOL_INPUT must be a JSON object")
    return validate_request(parsed.get("run_id"), parsed.get("profile"))


def _read_stream(
    stream: Any, limit: int, buffer: bytearray, overflow: threading.Event
) -> None:
    """Drain one pipe incrementally so neither child pipe can deadlock the other."""

    reader = getattr(stream, "read1", stream.read)
    while True:
        chunk = reader(READ_CHUNK_BYTES)
        if not chunk:
            return
        remaining = limit - len(buffer)
        if remaining <= 0 or len(chunk) > remaining:
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            # Leave this pipe open until the parent process tree is killed.
            # Closing it here can make a wrapper exit before taskkill/killpg
            # receives a chance to contain its descendants.
            overflow.set()
            return
        buffer.extend(chunk)


def _wait_after_signal(process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Wait for a child without masking the original monitoring failure."""

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _create_windows_job(process: subprocess.Popen[bytes]) -> WindowsJob | None:
    """Attach a child to a kill-on-close job when the host permits it."""

    if os.name != "nt":
        return None
    try:
        return WindowsJob(process)
    except OSError:
        return None


def _terminate_process(
    process: subprocess.Popen[bytes], windows_job: WindowsJob | None
) -> None:
    """End a timed-out or overflowing CLI process tree on every supported host."""

    if os.name == "nt":
        if windows_job is not None:
            windows_job.close()
        # CREATE_NEW_PROCESS_GROUP plus taskkill /T contains child processes
        # created by a CLI wrapper. The PID is created by this helper, not input.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if _wait_after_signal(process, 1):
            return
        process.terminate()
        if _wait_after_signal(process, 1):
            return
        process.kill()
        _wait_after_signal(process, 1)
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    if _wait_after_signal(process, 1):
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
    _wait_after_signal(process, 1)


def resolve_databricks_cli() -> str:
    """Find a direct CLI executable and refuse Windows command wrappers.

    Windows delegates .cmd/.bat files through cmd.exe even when subprocess uses
    shell=False. That interpreter can reinterpret an opaque page token, so this
    helper requires databricks.exe on Windows instead of a command wrapper.
    """

    if os.name == "nt":
        direct_executable = shutil.which("databricks.exe")
        if direct_executable and Path(direct_executable).suffix.lower() == ".exe":
            return direct_executable
        resolved = shutil.which("databricks")
        if resolved and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
            raise MonitorError(
                "unsafe_cli_wrapper",
                "resolved Databricks CLI is a .cmd or .bat wrapper; install or expose databricks.exe",
            )
        raise MonitorError(
            "dependency_unavailable", "databricks.exe was not found on PATH"
        )

    resolved = shutil.which("databricks")
    if not resolved:
        raise MonitorError(
            "dependency_unavailable", "databricks CLI was not found on PATH"
        )
    if Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        raise MonitorError(
            "unsafe_cli_wrapper", "resolved Databricks CLI is an unsafe command wrapper"
        )
    return resolved


def run_cli(
    request: RunRequest, page_token: str | None, deadline: float
) -> Mapping[str, Any]:
    """Run one allow-listed structured Jobs command with bounded pipes and time."""

    cli = resolve_databricks_cli()
    argv = [
        cli,
        "jobs",
        "get-run",
        request.run_id,
        "--profile",
        request.profile,
        "--include-history",
        "-o",
        "json",
    ]
    if page_token is not None:
        argv.extend(["--page-token", page_token])

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MonitorError(
            "deadline_exceeded", "run-status collection exceeded its overall deadline"
        )
    call_deadline = time.monotonic() + min(CALL_TIMEOUT_SECONDS, remaining)
    try:
        process_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(argv, **process_options)
    except OSError as exc:
        raise MonitorError(
            "cli_start_failed", "could not start the Databricks CLI", str(exc)
        ) from exc

    assert process.stdout is not None
    assert process.stderr is not None
    windows_job = _create_windows_job(process)
    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()
    readers = [
        threading.Thread(
            target=_read_stream,
            args=(process.stdout, MAX_STDOUT_BYTES, stdout, stdout_overflow),
            daemon=True,
        ),
        threading.Thread(
            target=_read_stream,
            args=(process.stderr, MAX_STDERR_BYTES, stderr, stderr_overflow),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    failure_code = ""
    while process.poll() is None:
        if stdout_overflow.is_set():
            failure_code = "stdout_limit_exceeded"
            break
        if stderr_overflow.is_set():
            failure_code = "stderr_limit_exceeded"
            break
        if time.monotonic() >= call_deadline:
            failure_code = (
                "cli_timeout" if call_deadline < deadline else "deadline_exceeded"
            )
            break
        time.sleep(0.01)
    if failure_code or stdout_overflow.is_set() or stderr_overflow.is_set():
        _terminate_process(process, windows_job)
        windows_job = None
    else:
        process.wait()
    for reader in readers:
        reader.join(timeout=1)
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()
    if windows_job is not None:
        windows_job.close()

    if stdout_overflow.is_set():
        raise MonitorError(
            "stdout_limit_exceeded",
            "Databricks CLI stdout exceeded the 25 MiB safety limit",
        )
    if stderr_overflow.is_set():
        raise MonitorError(
            "stderr_limit_exceeded",
            "Databricks CLI stderr exceeded the 64 KiB safety limit",
        )
    if failure_code:
        raise MonitorError(
            failure_code,
            "Databricks CLI did not complete within the configured time limit",
        )
    if time.monotonic() > deadline:
        raise MonitorError(
            "deadline_exceeded", "run-status collection exceeded its overall deadline"
        )
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace") or stdout.decode(
            "utf-8", errors="replace"
        )
        raise MonitorError(
            "cli_failed", "Databricks CLI returned a nonzero exit status", detail
        )
    try:
        parsed = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MonitorError(
            "invalid_cli_json", "Databricks CLI returned invalid JSON"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise MonitorError(
            "invalid_cli_schema", "Databricks CLI response must be a JSON object"
        )
    return parsed


def normalized_run_id(value: object) -> str:
    """Normalize a JSON run ID while rejecting booleans and malformed values."""

    if isinstance(value, bool):
        return ""
    candidate = str(value).strip()
    if not RUN_ID_PATTERN.fullmatch(candidate):
        return ""
    return candidate if int(candidate) <= MAX_RUN_ID else ""


def optional_string(value: object) -> str:
    """Bound untrusted text retained from a CLI response."""

    return redact(value) if isinstance(value, str) else ""


def bounded_code(value: object, field: str) -> str:
    """Accept only compact documented enum-like values before exposing them."""

    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 128:
        raise MonitorError("invalid_cli_schema", f"{field} must be a short string")
    return value


def output_id(value: object) -> int | None:
    """Keep optional numeric identifiers in the normalized output bounded and typed."""

    normalized = normalized_run_id(value)
    return int(normalized) if normalized else None


def safe_task_key(value: object) -> str:
    """Retain a bounded redacted task key without accepting an arbitrary payload."""

    if not isinstance(value, str):
        return ""
    return redact(value)[:256]


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MonitorError("invalid_cli_schema", f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise MonitorError("invalid_cli_schema", f"{field} must be an array")
    return value


def _legacy_conflicts(legacy: object, status: str, termination: str) -> bool:
    """Report mixed old/new disagreement without allowing legacy fields to win."""

    if not isinstance(legacy, Mapping):
        return False
    legacy_state = legacy.get("life_cycle_state")
    legacy_result = legacy.get("result_state")
    return bool(
        (isinstance(legacy_state, str) and legacy_state and legacy_state != status)
        or (
            isinstance(legacy_result, str)
            and legacy_result
            and termination
            and legacy_result != termination
        )
    )


def interpret_root_state(response: Mapping[str, Any]) -> RunState:
    """Use Jobs 2.2 status first, falling back only when it is absent."""

    has_current_status = "status" in response
    status_value = response.get("status")
    legacy_value = response.get("state")
    if has_current_status:
        status = _mapping(status_value, "status")
        state = status.get("state")
        if not isinstance(state, str) or state not in CURRENT_STATUS_STATES:
            raise MonitorError(
                "unknown_status", "response status.state is unknown or missing"
            )
        details_value = status.get("termination_details")
        details = (
            {}
            if details_value is None
            else _mapping(details_value, "status.termination_details")
        )
        termination = bounded_code(
            details.get("code", ""), "status.termination_details.code"
        )
        if termination and termination not in TERMINATION_CODES:
            raise MonitorError(
                "unknown_termination", "response has an unknown termination code"
            )
        if state == "TERMINATED" and not termination:
            raise MonitorError(
                "unknown_termination",
                "terminated response has an unknown or missing termination code",
            )
        return RunState(
            status=state,
            termination=termination,
            source="status",
            state_message=optional_string(details.get("message")),
            is_terminal=state == "TERMINATED",
            is_success=state == "TERMINATED" and termination == "SUCCESS",
            outcome_complete=state == "TERMINATED" and termination in TERMINATION_CODES,
            mixed_state_conflict=_legacy_conflicts(legacy_value, state, termination),
        )

    legacy = _mapping(legacy_value, "state")
    lifecycle = legacy.get("life_cycle_state")
    result = bounded_code(legacy.get("result_state", ""), "legacy state.result_state")
    if not isinstance(lifecycle, str) or lifecycle not in LEGACY_LIFECYCLE_STATES:
        raise MonitorError(
            "unknown_status", "legacy state.life_cycle_state is unknown or missing"
        )
    if result and result not in LEGACY_RESULT_STATES:
        raise MonitorError(
            "unknown_termination", "legacy response has an unknown result state"
        )
    if lifecycle == "TERMINATED" and not result:
        raise MonitorError(
            "unknown_termination",
            "legacy terminated response has an unknown or missing result state",
        )
    legacy_terminal = lifecycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}
    return RunState(
        status=lifecycle,
        termination=result,
        source="legacy_state",
        state_message=optional_string(legacy.get("state_message")),
        is_terminal=legacy_terminal,
        is_success=lifecycle == "TERMINATED" and result == "SUCCESS",
        outcome_complete=legacy_terminal,
        mixed_state_conflict=False,
    )


def task_state(item: Mapping[str, Any]) -> tuple[str, str, str, str, bool]:
    """Project a task or ForEach iteration state without trusting it as root state."""

    status_value = item.get("status")
    legacy_value = item.get("state")
    if isinstance(status_value, Mapping):
        state = status_value.get("state")
        details = status_value.get("termination_details")
        details_mapping = details if isinstance(details, Mapping) else {}
        code = details_mapping.get("code", "")
        code_text = code if isinstance(code, str) and len(code) <= 128 else ""
        return (
            state if isinstance(state, str) and len(state) <= 128 else "",
            code_text,
            "status",
            optional_string(details_mapping.get("message")),
            state == "TERMINATED"
            and code_text in TERMINATION_CODES
            and code_text != "SUCCESS",
        )
    if isinstance(legacy_value, Mapping):
        lifecycle = legacy_value.get("life_cycle_state")
        result = legacy_value.get("result_state", "")
        result_text = result if isinstance(result, str) else ""
        return (
            lifecycle if isinstance(lifecycle, str) and len(lifecycle) <= 128 else "",
            result_text,
            "legacy_state",
            optional_string(legacy_value.get("state_message")),
            lifecycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}
            and result_text not in {"", "SUCCESS"},
        )
    return "", "", "unknown", "", False


def slim_run_item(item: object, field: str, index: int) -> dict[str, Any]:
    """Validate and retain only task/iteration fields needed by the summary."""

    mapping = _mapping(item, f"{field}[{index}]")
    run_id = normalized_run_id(mapping.get("run_id"))
    if not run_id:
        raise MonitorError(
            "invalid_cli_schema",
            f"{field}[{index}].run_id must be a positive numeric ID",
        )
    attempt_number = mapping.get("attempt_number")
    if attempt_number is not None and (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
    ):
        raise MonitorError(
            "invalid_cli_schema",
            f"{field}[{index}].attempt_number must be a nonnegative integer",
        )
    status, termination, source, message, failed = task_state(mapping)
    raw_task_key = mapping.get("task_key")
    return {
        "task_key": safe_task_key(raw_task_key),
        "run_id": run_id,
        "attempt_number": attempt_number if isinstance(attempt_number, int) else -1,
        "status": status,
        "termination": termination,
        "source": source,
        "state_message": message,
        "failed": failed,
        "index": index,
    }


def repair_rank_by_run_id(
    history: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, int]]:
    """Build chronological repair ordering without exposing repair/config payloads."""

    rank: dict[str, tuple[int, int]] = {}
    for history_index, item in enumerate(history):
        repair = _mapping(item, f"repair_history[{history_index}]")
        run_ids = _list(
            repair.get("task_run_ids", []),
            f"repair_history[{history_index}].task_run_ids",
        )
        for task_index, run_id_value in enumerate(run_ids):
            run_id = normalized_run_id(run_id_value)
            if not run_id:
                raise MonitorError(
                    "invalid_cli_schema",
                    f"repair_history[{history_index}].task_run_ids[{task_index}] must be a positive numeric ID",
                )
            rank[run_id] = (history_index, task_index)
    return rank


def current_task_attempts(
    tasks: Sequence[dict[str, Any]], repair_rank: Mapping[str, tuple[int, int]]
) -> dict[str, dict[str, Any]]:
    """Select one current root-task attempt per task key, repairs included."""

    current: dict[str, dict[str, Any]] = {}
    for task in tasks:
        key = task["task_key"] or f"run:{task['run_id']}"
        repair_position = repair_rank.get(task["run_id"], (-1, -1))
        chronology = (
            repair_position[0],
            task["attempt_number"],
            repair_position[1],
            task["index"],
        )
        prior = current.get(key)
        if prior is None or chronology > prior["_chronology"]:
            selected = dict(task)
            selected["_chronology"] = chronology
            current[key] = selected
    return current


def failed_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a privacy-safe stable shape for failures only."""

    return [
        {
            "task_key": item["task_key"] or None,
            "run_id": int(item["run_id"]),
            "result_state": item["termination"],
            "state_message": item["state_message"],
            "status": item["status"],
            "termination": item["termination"],
            "source": item["source"],
        }
        for item in items
        if item["failed"]
    ]


def duration_seconds(response: Mapping[str, Any]) -> int:
    """Prefer validated Jobs 2.2 run_duration, then conservatively use timestamps."""

    run_duration = response.get("run_duration")
    if (
        isinstance(run_duration, int)
        and not isinstance(run_duration, bool)
        and run_duration >= 0
    ):
        return round(run_duration / 1000)
    start = response.get("start_time")
    end = response.get("end_time")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start >= 0
        and end >= start
    ):
        return round((end - start) / 1000)
    return 0


def next_token(response: Mapping[str, Any]) -> str | None:
    """Validate coherent Jobs 2.2 pagination without echoing page tokens."""

    has_more = response.get("has_more")
    if has_more is not None and not isinstance(has_more, bool):
        raise MonitorError(
            "invalid_cli_schema", "has_more must be a boolean when present"
        )
    token_value = response.get("next_page_token")
    if token_value is None or token_value == "":
        token = None
    elif isinstance(token_value, str) and PAGE_TOKEN_PATTERN.fullmatch(token_value):
        token = token_value
    else:
        raise MonitorError("invalid_pagination", "next_page_token is invalid")
    if has_more is True and token is None:
        raise MonitorError(
            "invalid_pagination", "has_more was true but next_page_token was absent"
        )
    if has_more is False and token is not None:
        raise MonitorError(
            "invalid_pagination", "has_more was false but next_page_token was present"
        )
    return token


def collect_run(request: RunRequest) -> dict[str, Any]:
    """Collect bounded pages and return a complete, normalized read-only snapshot."""

    deadline = time.monotonic() + OVERALL_TIMEOUT_SECONDS
    page_token: str | None = None
    seen_tokens: set[str] = set()
    all_tasks: list[dict[str, Any]] = []
    all_iterations: list[dict[str, Any]] = []
    all_repair_history: list[Mapping[str, Any]] = []
    repair_task_run_id_count = 0
    first_response: Mapping[str, Any] | None = None
    root_state: RunState | None = None
    for page_number in range(1, MAX_PAGES + 1):
        response = run_cli(request, page_token, deadline)
        if normalized_run_id(response.get("run_id")) != request.run_id:
            raise MonitorError(
                "run_identity_mismatch",
                "Databricks CLI response did not match the requested run ID",
            )
        if first_response is None:
            first_response = response
            root_state = interpret_root_state(response)
        tasks = _list(response.get("tasks", []), "tasks")
        iterations = _list(response.get("iterations", []), "iterations")
        repair_history = _list(response.get("repair_history", []), "repair_history")
        if len(all_tasks) + len(tasks) > MAX_TASKS:
            raise MonitorError(
                "pagination_limit_exceeded",
                "task aggregation exceeded the 10,000-item safety limit",
            )
        if len(all_iterations) + len(iterations) > MAX_ITERATIONS:
            raise MonitorError(
                "pagination_limit_exceeded",
                "iteration aggregation exceeded the 10,000-item safety limit",
            )
        if len(all_repair_history) + len(repair_history) > MAX_REPAIR_HISTORY_ITEMS:
            raise MonitorError(
                "pagination_limit_exceeded",
                "repair-history aggregation exceeded the 10,000-item safety limit",
            )
        task_offset = len(all_tasks)
        iteration_offset = len(all_iterations)
        repair_offset = len(all_repair_history)
        all_tasks.extend(
            slim_run_item(item, "tasks", task_offset + index)
            for index, item in enumerate(tasks)
        )
        all_iterations.extend(
            slim_run_item(item, "iterations", iteration_offset + index)
            for index, item in enumerate(iterations)
        )
        for history_index, history_item in enumerate(repair_history):
            repair = _mapping(
                history_item, f"repair_history[{repair_offset + history_index}]"
            )
            raw_run_ids = _list(
                repair.get("task_run_ids", []),
                f"repair_history[{repair_offset + history_index}].task_run_ids",
            )
            normalized_run_ids: list[str] = []
            for run_id_index, run_id_value in enumerate(raw_run_ids):
                run_id = normalized_run_id(run_id_value)
                if not run_id:
                    raise MonitorError(
                        "invalid_cli_schema",
                        f"repair_history[{repair_offset + history_index}].task_run_ids[{run_id_index}] must be a positive numeric ID",
                    )
                normalized_run_ids.append(run_id)
            repair_task_run_id_count += len(normalized_run_ids)
            if repair_task_run_id_count > MAX_REPAIR_TASK_RUN_IDS:
                raise MonitorError(
                    "pagination_limit_exceeded",
                    "repair-history task-run IDs exceeded the 10,000-item safety limit",
                )
            all_repair_history.append({"task_run_ids": normalized_run_ids})
        token = next_token(response)
        if token is None:
            break
        if token in seen_tokens:
            raise MonitorError(
                "pagination_cycle",
                "Databricks CLI pagination returned a repeated page token",
            )
        seen_tokens.add(token)
        if page_number == MAX_PAGES:
            raise MonitorError(
                "pagination_limit_exceeded",
                "Databricks CLI pagination exceeded the 100-page safety limit",
            )
        page_token = token
    else:  # pragma: no cover - the loop always breaks or raises.
        raise MonitorError(
            "pagination_limit_exceeded",
            "Databricks CLI pagination exceeded the safety limit",
        )

    assert first_response is not None
    assert root_state is not None
    current = current_task_attempts(
        all_tasks, repair_rank_by_run_id(all_repair_history)
    )
    current_items = list(current.values())
    return {
        "run_id": int(request.run_id),
        "job_id": output_id(first_response.get("job_id")),
        "life_cycle_state": root_state.status,
        "result_state": root_state.termination,
        "state_message": root_state.state_message,
        "duration_seconds": duration_seconds(first_response),
        "is_terminal": root_state.is_terminal,
        "is_success": root_state.is_success,
        "failed_task_runs": failed_items(current_items),
        "run_page_url": optional_string(first_response.get("run_page_url")),
        "status": root_state.status,
        "termination": root_state.termination,
        "source": root_state.source,
        "mixed_state_conflict": root_state.mixed_state_conflict,
        "pages": page_number,
        "tasks_complete": True,
        "outcome_complete": root_state.outcome_complete,
        "task_run_ids": {
            key: int(item["run_id"]) for key, item in sorted(current.items())
        },
        "failed_iteration_runs": failed_items(all_iterations),
    }


def error_payload(error: MonitorError) -> dict[str, Any]:
    """Keep all runtime failures structured, bounded, and explicitly incomplete."""

    payload: dict[str, Any] = {
        "error": {"code": error.code, "message": error.message},
        "tasks_complete": False,
        "outcome_complete": False,
        "is_success": False,
        "failed_task_runs": [],
        "failed_iteration_runs": [],
    }
    if error.detail:
        payload["error"]["detail"] = error.detail
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Run the public command and return documented process status codes."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        request = parse_request(arguments, os.environ.get("TOOL_INPUT"))
        if request is None:
            print(usage())
            return 0
        emit_json(collect_run(request))
        return 0
    except InputError as error:
        emit_json(error_payload(error))
        return 2
    except MonitorError as error:
        emit_json(error_payload(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
