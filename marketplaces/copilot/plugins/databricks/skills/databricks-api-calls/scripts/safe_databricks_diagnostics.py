"""Bounded, privacy-safe support for local Databricks CLI helpers."""

from __future__ import annotations

import os
import json
import re
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

MAX_DIAGNOSTIC_CHARS = 256
MAX_CAPTURED_CHARS = 1024
MAX_REDACTION_DEPTH = 8
MAX_REDACTION_NODES = 256
MAX_RUNTIME_FILE_BYTES = 16 * 1024 * 1024
MAX_CLI_STDOUT_BYTES = 25 * 1024 * 1024
MAX_CLI_STDERR_BYTES = 64 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
SAFE_STATES = frozenset(
    {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
)
SENSITIVE_KEY_PARTS = (
    "access_token",
    "access_key",
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "external_link",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "signature",
    "temp_credential",
    "temporary_credential",
    "token",
)
RUNTIME_ONLY_KEY_PARTS = SENSITIVE_KEY_PARTS + (
    "content",
    "message",
    "messages",
    "query",
    "sql",
    "statement",
)
GIT_REPOSITORY_SELECTION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)([^\s,;\"']+)")
_AUTH_SCHEME_PATTERN = re.compile(r"(?i)((?:basic|dpop)\s+)([^\s,;\"']+)")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|"
    r"password|refresh[_-]?token|token)\s*[:=]\s*([^\s,;\"']+)"
)
_URL_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|"
    r"password|refresh[_-]?token|token|signature)=[^&#\s]+)"
)
_JWT_PATTERN = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
_DATABRICKS_TOKEN_PATTERN = re.compile(r"\bdapi[a-zA-Z0-9_-]{8,}\b")


class RuntimeBoundaryError(RuntimeError):
    """Raised without paths or payloads when a private-runtime boundary fails."""


@dataclass(frozen=True)
class BoundedProcessResult:
    """Bounded process outcome with raw bytes retained only below configured caps."""

    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_limited: bool


def normalized_key(value: object) -> str:
    """Return a comparison-only form of a possible JSON key."""
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def key_has_part(key: object, parts: tuple[str, ...]) -> bool:
    """Return whether a key is governed by one of the policy terms."""
    normalized = normalized_key(key)
    return any(part in normalized for part in parts)


def is_runtime_only_key(key: object) -> bool:
    """Return whether inline source must not retain the keyed value."""
    return key_has_part(key, RUNTIME_ONLY_KEY_PARTS)


def has_token_pattern(value: object) -> bool:
    """Return whether a scalar contains a recognizable credential pattern."""
    if not isinstance(value, str):
        return False
    return any(
        pattern.search(value)
        for pattern in (
            _BEARER_PATTERN,
            _AUTH_SCHEME_PATTERN,
            _SECRET_ASSIGNMENT_PATTERN,
            _URL_SECRET_PATTERN,
            _JWT_PATTERN,
            _DATABRICKS_TOKEN_PATTERN,
        )
    )


def redact_text(value: object, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """Return capped one-line text with recognizable credentials removed."""
    text = re.sub(r"[\r\n\t]+", " ", str(value))
    text = _BEARER_PATTERN.sub(r"\1<redacted>", text)
    text = _AUTH_SCHEME_PATTERN.sub(r"\1<redacted>", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub("<redacted>", text)
    text = _URL_SECRET_PATTERN.sub("<redacted>", text)
    text = _JWT_PATTERN.sub("<redacted>", text)
    text = _DATABRICKS_TOKEN_PATTERN.sub("<redacted>", text)
    return f"{text[:limit]}<truncated>" if len(text) > limit else text


def redact_value(value: Any) -> Any:
    """Redact recursively with deterministic depth, node, cycle, and string bounds."""
    budget = {"nodes": MAX_REDACTION_NODES}
    seen: set[int] = set()

    def visit(current: Any, depth: int) -> Any:
        if budget["nodes"] <= 0:
            return {"redacted": True, "truncated": "node_budget"}
        budget["nodes"] -= 1
        if depth >= MAX_REDACTION_DEPTH:
            return {"redacted": True, "truncated": "depth_budget"}
        if isinstance(current, (dict, list)):
            marker = id(current)
            if marker in seen:
                return {"redacted": True, "truncated": "cycle"}
            seen.add(marker)
        if isinstance(current, dict):
            redacted: dict[str, Any] = {}
            for key, nested in current.items():
                if budget["nodes"] <= 0:
                    redacted["<truncated>"] = {
                        "redacted": True,
                        "truncated": "node_budget",
                    }
                    break
                redacted[redact_text(key)] = (
                    "<redacted>"
                    if is_runtime_only_key(key)
                    else visit(nested, depth + 1)
                )
            return redacted
        if isinstance(current, list):
            redacted_list: list[Any] = []
            for item in current:
                if budget["nodes"] <= 0:
                    redacted_list.append({"redacted": True, "truncated": "node_budget"})
                    break
                redacted_list.append(visit(item, depth + 1))
            return redacted_list
        return redact_text(current) if isinstance(current, str) else current

    return visit(value, 0)


def contains_sensitive_inline_data(value: Any) -> bool:
    """Return whether an inline source literal would retain sensitive data."""
    if isinstance(value, dict):
        return any(
            is_runtime_only_key(key) or contains_sensitive_inline_data(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(contains_sensitive_inline_data(item) for item in value)
    return isinstance(value, str)


def process_output_metadata(value: str | bytes) -> dict[str, int | bool]:
    """Describe captured output without exposing it or allocating a string copy."""
    count = len(value)
    return {
        "present": bool(count),
        "captured_chars": min(count, MAX_CAPTURED_CHARS),
        "truncated": count > MAX_CAPTURED_CHARS,
    }


def safe_identifier(value: object) -> str | None:
    """Return a bounded identifier only when it cannot carry free-form data."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 200
        or not re.fullmatch(r"[A-Za-z0-9_.@-]+", candidate)
    ):
        return None
    return candidate


def safe_state(value: object) -> str:
    """Return an allowlisted statement state."""
    return value if isinstance(value, str) and value in SAFE_STATES else "UNKNOWN"


def safe_error_code(value: object) -> str | None:
    """Return a machine-readable error code, never a human message."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9_]{1,128}", value):
        return None
    return value


def statement_state(response: object) -> str:
    """Extract only the allowlisted state from a Statement API response."""
    if not isinstance(response, dict):
        return "UNKNOWN"
    status = response.get("status")
    return safe_state(status.get("state")) if isinstance(status, dict) else "UNKNOWN"


def statement_id(response: object) -> str | None:
    """Extract a safe statement ID for structural diagnostics."""
    return (
        safe_identifier(response.get("statement_id"))
        if isinstance(response, dict)
        else None
    )


def response_summary(response: object) -> dict[str, str]:
    """Return only explicit, safe response fields for stdout/stderr."""
    summary: dict[str, str] = {"response_type": type(response).__name__}
    if not isinstance(response, dict):
        return summary
    identifier = statement_id(response)
    if identifier is not None:
        summary["statement_id"] = identifier
    state = statement_state(response)
    if state != "UNKNOWN":
        summary["state"] = state
    error_code = safe_error_code(response.get("error_code"))
    status = response.get("status")
    if (
        error_code is None
        and isinstance(status, dict)
        and isinstance(status.get("error"), dict)
    ):
        error_code = safe_error_code(status["error"].get("error_code"))
    if error_code is not None:
        summary["error_code"] = error_code
    return summary


def result_is_incomplete(response: object) -> bool:
    """Fail closed unless a Statement API result is explicitly complete and inline."""
    if not isinstance(response, dict):
        return True
    manifest = response.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("truncated") is not False:
        return True
    result = response.get("result")
    if not isinstance(result, dict):
        return True
    for mapping in (response, result):
        for key in mapping:
            normalized = normalized_key(key)
            if (
                normalized.startswith("next_chunk")
                or "continuation" in normalized
                or "external_link" in normalized
            ):
                return True
    return False


def safe_profile_name(value: object) -> str:
    """Return a bounded profile label without retaining token-looking content."""
    if not isinstance(value, str) or not value.strip() or has_token_pattern(value):
        return "<redacted-profile>"
    return redact_text(value.strip(), limit=80)


def safe_host(value: object) -> str | None:
    """Return a host-only HTTPS endpoint or omit malformed context entirely."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return f"https://{parsed.hostname}{port}"


def safe_git_env() -> dict[str, str]:
    """Return Git environment with repository/config redirection removed."""
    env = os.environ.copy()
    for key in GIT_REPOSITORY_SELECTION_ENV:
        env.pop(key, None)
    for key in tuple(env):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def _nearest_existing_directory(path: Path) -> Path:
    candidate = path if path.exists() and path.is_dir() else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    """Return whether a directory entry can redirect a lexical path elsewhere."""
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def assert_no_reparse_components(path: Path) -> None:
    """Reject every existing symlink/reparse component before Git or private I/O."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            entry = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeBoundaryError(
                "Private path components could not be verified."
            ) from exc
        if _is_symlink_or_reparse(entry):
            raise RuntimeBoundaryError(
                "Private paths may not traverse symlink or reparse components."
            )


def _has_git_ancestor(path: Path) -> bool:
    candidate = path
    while candidate != candidate.parent:
        if (candidate / ".git").exists():
            return True
        candidate = candidate.parent
    return (candidate / ".git").exists()


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=safe_git_env(),
    )


def _git_worktree_for_target(path: Path) -> Path | None:
    cwd = _nearest_existing_directory(path)
    has_ancestor = _has_git_ancestor(cwd)
    try:
        result = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.") from exc
        return None
    if result.returncode != 0:
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.")
        return None
    try:
        worktree = Path(result.stdout.strip()).resolve()
    except OSError as exc:
        raise RuntimeBoundaryError("Git protection could not be verified.") from exc
    if not result.stdout.strip() or not path.is_relative_to(worktree):
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.")
        return None
    return worktree


def assert_private_git_target(path_value: str | Path) -> Path:
    """Reject paths Git would retain; this is point-in-time, not a TOCTOU proof."""
    target = Path(os.path.abspath(Path(path_value).expanduser()))
    assert_no_reparse_components(target)
    worktree = _git_worktree_for_target(target)
    if worktree is None:
        return target
    relative = str(target.relative_to(worktree))
    try:
        tracked = _run_git(worktree, ["ls-files", "--error-unmatch", "--", relative])
        ignored = _run_git(worktree, ["check-ignore", "-q", "--", relative])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeBoundaryError("Git protection could not be verified.") from exc
    if tracked.returncode not in (0, 1) or ignored.returncode not in (0, 1):
        raise RuntimeBoundaryError("Git protection could not be verified.")
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise RuntimeBoundaryError(
            "Private runtime data must be outside Git or ignored and untracked."
        )
    return target


def read_private_runtime_file(path_value: str | Path, kind: str) -> bytes:
    """Read a regular private file with a pre-allocation cap and stable-file check."""
    path = assert_private_git_target(path_value)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeBoundaryError(f"{kind} file is unavailable.") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > MAX_RUNTIME_FILE_BYTES
    ):
        raise RuntimeBoundaryError(f"{kind} file is invalid.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeBoundaryError(f"{kind} file is unavailable.") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_RUNTIME_FILE_BYTES:
            raise RuntimeBoundaryError(f"{kind} file is invalid.")
        chunks: list[bytes] = []
        remaining = MAX_RUNTIME_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeBoundaryError(f"{kind} file is unavailable.") from exc
    finally:
        os.close(descriptor)
    if (
        len(payload) == 0
        or len(payload) > MAX_RUNTIME_FILE_BYTES
        or (before.st_dev, before.st_ino, before.st_size)
        != (opened.st_dev, opened.st_ino, opened.st_size)
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise RuntimeBoundaryError(f"{kind} file changed or is invalid.")
    return payload


@contextmanager
def private_json_file(body: object) -> Iterator[Path]:
    """Materialize a bounded 0600 JSON body for CLI `--json @file` transport."""
    try:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise RuntimeBoundaryError("Request body is invalid.") from exc
    if not encoded or len(encoded) > MAX_RUNTIME_FILE_BYTES:
        raise RuntimeBoundaryError("Request body is invalid.")
    temp_dir = Path(tempfile.gettempdir())
    assert_private_git_target(temp_dir / "databricks-api-runtime-probe.json")
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix="databricks-api-", suffix=".json", dir=temp_dir
        )
    except OSError as exc:
        raise RuntimeBoundaryError("Private runtime file is unavailable.") from exc
    path = Path(raw_path)
    try:
        assert_no_reparse_components(path)
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
        except OSError as exc:
            raise RuntimeBoundaryError("Private runtime file is unavailable.") from exc
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeBoundaryError("Private runtime cleanup failed.") from exc


def _drain_bounded(
    pipe: Any,
    limit: int,
    chunks: list[bytes],
    overflow: threading.Event,
    process: subprocess.Popen[bytes],
) -> None:
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            return
        used = sum(len(item) for item in chunks)
        if used < limit:
            chunks.append(chunk[: limit - used])
        if used + len(chunk) > limit:
            overflow.set()
            try:
                process.terminate()
            except OSError:
                pass


def run_bounded_command(
    command: list[str],
    timeout_seconds: float,
    stdout_limit: int = MAX_CLI_STDOUT_BYTES,
    stderr_limit: int = MAX_CLI_STDERR_BYTES,
) -> BoundedProcessResult:
    """Run direct argv with bounded concurrent drains and prompt termination on overflow."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    overflow = threading.Event()
    readers = (
        threading.Thread(
            target=_drain_bounded,
            args=(process.stdout, stdout_limit, stdout_chunks, overflow, process),
        ),
        threading.Thread(
            target=_drain_bounded,
            args=(process.stderr, stderr_limit, stderr_chunks, overflow, process),
        ),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
    return BoundedProcessResult(
        returncode=process.returncode if process.returncode is not None else 1,
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        timed_out=timed_out,
        output_limited=overflow.is_set(),
    )


def generated_runtime_support() -> str:
    """Return the standalone policy embedded in generated API helper scripts."""
    return r"""MAX_DIAGNOSTIC_CHARS = 256
MAX_CAPTURED_CHARS = 1024
MAX_RUNTIME_FILE_BYTES = 16 * 1024 * 1024
MAX_CLI_STDOUT_BYTES = 25 * 1024 * 1024
MAX_CLI_STDERR_BYTES = 64 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
SAFE_STATES = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
SENSITIVE_KEY_PARTS = (
    "access_token", "access_key", "api_key", "authorization", "client_secret", "credential", "external_link",
    "password", "private_key", "refresh_token", "secret", "signature", "temp_credential",
    "temporary_credential", "token",
)
RUNTIME_ONLY_KEY_PARTS = SENSITIVE_KEY_PARTS + ("content", "message", "messages", "query", "sql", "statement")
GIT_REPOSITORY_SELECTION_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)([^\s,;\"']+)")
_AUTH_SCHEME_PATTERN = re.compile(r"(?i)((?:basic|dpop)\s+)([^\s,;\"']+)")
_SECRET_ASSIGNMENT_PATTERN = re.compile(r"(?i)\b(?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|password|refresh[_-]?token|token)\s*[:=]\s*([^\s,;\"']+)")
_URL_SECRET_PATTERN = re.compile(r"(?i)([?&](?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|password|refresh[_-]?token|token|signature)=[^&#\s]+)")
_JWT_PATTERN = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
_DATABRICKS_TOKEN_PATTERN = re.compile(r"\bdapi[a-zA-Z0-9_-]{8,}\b")


class RuntimeBoundaryError(RuntimeError):
    pass


def _normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _key_has_part(key, parts):
    return any(part in _normalized_key(key) for part in parts)


def _process_output_metadata(value):
    count = len(value)
    return {"present": bool(count), "captured_chars": min(count, MAX_CAPTURED_CHARS), "truncated": count > MAX_CAPTURED_CHARS}


def _safe_identifier(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 200 or not re.fullmatch(r"[A-Za-z0-9_.@-]+", candidate):
        return None
    return candidate


def _safe_state(value):
    return value if isinstance(value, str) and value in SAFE_STATES else "UNKNOWN"


def _safe_error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z0-9_]{1,128}", value) else None


def statement_state(response):
    if not isinstance(response, dict) or not isinstance(response.get("status"), dict):
        return "UNKNOWN"
    return _safe_state(response["status"].get("state"))


def statement_id(response):
    return _safe_identifier(response.get("statement_id")) if isinstance(response, dict) else None


def response_summary(response):
    summary = {"response_type": type(response).__name__}
    if not isinstance(response, dict):
        return summary
    identifier = statement_id(response)
    if identifier is not None:
        summary["statement_id"] = identifier
    state = statement_state(response)
    if state != "UNKNOWN":
        summary["state"] = state
    error_code = _safe_error_code(response.get("error_code"))
    status = response.get("status")
    if error_code is None and isinstance(status, dict) and isinstance(status.get("error"), dict):
        error_code = _safe_error_code(status["error"].get("error_code"))
    if error_code is not None:
        summary["error_code"] = error_code
    return summary


def result_is_incomplete(response):
    if not isinstance(response, dict):
        return True
    manifest = response.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("truncated") is not False:
        return True
    result = response.get("result")
    if not isinstance(result, dict):
        return True
    for mapping in (response, result):
        for key in mapping:
            normalized = _normalized_key(key)
            if normalized.startswith("next_chunk") or "continuation" in normalized or "external_link" in normalized:
                return True
    return False


def _safe_git_env():
    env = os.environ.copy()
    for key in GIT_REPOSITORY_SELECTION_ENV:
        env.pop(key, None)
    for key in tuple(env):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def _nearest_existing_directory(path):
    candidate = path if path.exists() and path.is_dir() else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _is_symlink_or_reparse(stat_result):
    return stat.S_ISLNK(stat_result.st_mode) or bool(getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def assert_no_reparse_components(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            entry = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeBoundaryError("Private path components could not be verified.") from exc
        if _is_symlink_or_reparse(entry):
            raise RuntimeBoundaryError("Private paths may not traverse symlink or reparse components.")


def _has_git_ancestor(path):
    candidate = path
    while candidate != candidate.parent:
        if (candidate / ".git").exists():
            return True
        candidate = candidate.parent
    return (candidate / ".git").exists()


def _run_git(cwd, args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=False, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=10, env=_safe_git_env())


def assert_private_git_target(path_value):
    target = Path(os.path.abspath(Path(path_value).expanduser()))
    assert_no_reparse_components(target)
    cwd = _nearest_existing_directory(target)
    has_ancestor = _has_git_ancestor(cwd)
    try:
        root = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.") from exc
        return target
    if root.returncode != 0:
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.")
        return target
    try:
        worktree = Path(root.stdout.strip()).resolve()
    except OSError as exc:
        raise RuntimeBoundaryError("Git protection could not be verified.") from exc
    if not root.stdout.strip() or not target.is_relative_to(worktree):
        if has_ancestor:
            raise RuntimeBoundaryError("Git protection could not be verified.")
        return target
    relative = str(target.relative_to(worktree))
    try:
        tracked = _run_git(worktree, ["ls-files", "--error-unmatch", "--", relative])
        ignored = _run_git(worktree, ["check-ignore", "-q", "--", relative])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeBoundaryError("Git protection could not be verified.") from exc
    if tracked.returncode not in (0, 1) or ignored.returncode not in (0, 1):
        raise RuntimeBoundaryError("Git protection could not be verified.")
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise RuntimeBoundaryError("Private runtime data must be outside Git or ignored and untracked.")
    return target


def read_private_runtime_file(path_value, kind):
    path = assert_private_git_target(path_value)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeBoundaryError("Runtime file is unavailable.") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > MAX_RUNTIME_FILE_BYTES:
        raise RuntimeBoundaryError("Runtime file is invalid.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeBoundaryError("Runtime file is unavailable.") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_RUNTIME_FILE_BYTES:
            raise RuntimeBoundaryError("Runtime file is invalid.")
        chunks, remaining = [], MAX_RUNTIME_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload, after = b"".join(chunks), os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeBoundaryError("Runtime file is unavailable.") from exc
    finally:
        os.close(descriptor)
    if len(payload) == 0 or len(payload) > MAX_RUNTIME_FILE_BYTES or (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size) or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeBoundaryError("Runtime file changed or is invalid.")
    return payload


@contextmanager
def private_json_file(body):
    try:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise RuntimeBoundaryError("Request body is invalid.") from exc
    if not encoded or len(encoded) > MAX_RUNTIME_FILE_BYTES:
        raise RuntimeBoundaryError("Request body is invalid.")
    temp_dir = Path(tempfile.gettempdir())
    assert_private_git_target(temp_dir / "databricks-api-runtime-probe.json")
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix="databricks-api-", suffix=".json", dir=temp_dir)
    except OSError as exc:
        raise RuntimeBoundaryError("Private runtime file is unavailable.") from exc
    path = Path(raw_path)
    try:
        assert_no_reparse_components(path)
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
        except OSError as exc:
            raise RuntimeBoundaryError("Private runtime file is unavailable.") from exc
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeBoundaryError("Private runtime cleanup failed.") from exc


def _drain_bounded(pipe, limit, chunks, overflow, process):
    used = 0
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            return
        if used < limit:
            chunks.append(chunk[:limit - used])
        used += len(chunk)
        if used > limit:
            overflow.set()
            try:
                process.terminate()
            except OSError:
                pass


def run_bounded_command(command, timeout_seconds, stdout_limit=MAX_CLI_STDOUT_BYTES, stderr_limit=MAX_CLI_STDERR_BYTES):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    stdout_chunks, stderr_chunks, overflow = [], [], threading.Event()
    readers = (
        threading.Thread(target=_drain_bounded, args=(process.stdout, stdout_limit, stdout_chunks, overflow, process)),
        threading.Thread(target=_drain_bounded, args=(process.stderr, stderr_limit, stderr_chunks, overflow, process)),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
    return {"returncode": process.returncode if process.returncode is not None else 1, "stdout": b"".join(stdout_chunks), "stderr": b"".join(stderr_chunks), "timed_out": timed_out, "output_limited": overflow.is_set()}
"""
