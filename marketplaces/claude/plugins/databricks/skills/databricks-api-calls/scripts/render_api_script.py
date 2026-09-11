"""Render privacy-safe, reusable Databricks API helper scripts."""

from __future__ import annotations

import argparse
import json
import math
import os
import py_compile
import re
from pathlib import Path
from pprint import pformat
from string import Template
from tempfile import TemporaryDirectory
from typing import Any

from safe_databricks_diagnostics import (
    RuntimeBoundaryError,
    assert_private_git_target,
    contains_sensitive_inline_data,
    generated_runtime_support,
    has_token_pattern,
)

JsonBody = dict[str, Any] | list[Any]
MAX_POLL_DEADLINE_SECONDS = 3600.0

CLI_HELPER_TEMPLATE = Template(
    """import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
$extra_imports

CLI_TIMEOUT_SECONDS = 120.0
PROFILE = $profile
$runtime_support


def emit_diagnostic(event, **fields):
    payload = {"event": event}
    payload.update(fields)
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


def emit_response_summary(response):
    print(json.dumps(response_summary(response), sort_keys=True))


def load_runtime_text(path_value, kind):
    try:
        raw = read_private_runtime_file(path_value, kind)
    except RuntimeBoundaryError:
        emit_diagnostic("runtime_file_unavailable", kind=kind)
        raise SystemExit(2)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        emit_diagnostic("runtime_file_invalid", kind=kind)
        raise SystemExit(2)


def load_runtime_json(path_value):
    raw = load_runtime_text(path_value, "request_body")
    try:
        body = json.loads(raw)
    except (RecursionError, json.JSONDecodeError):
        emit_diagnostic("runtime_file_invalid", kind="request_body")
        raise SystemExit(2)
    if not isinstance(body, (dict, list)):
        emit_diagnostic("runtime_file_invalid", kind="request_body")
        raise SystemExit(2)
    return body


def cli_json(*args, timeout_seconds=CLI_TIMEOUT_SECONDS):
    try:
        timeout = min(CLI_TIMEOUT_SECONDS, float(timeout_seconds))
    except (TypeError, ValueError):
        emit_diagnostic("invalid_cli_timeout")
        raise SystemExit(2)
    if timeout <= 0:
        emit_diagnostic("cli_deadline_exhausted")
        raise SystemExit(1)

    cmd = ["databricks", "--profile", PROFILE, *args]
    try:
        result = run_bounded_command(cmd, timeout)
    except FileNotFoundError as exc:
        emit_diagnostic("databricks_cli_not_found")
        raise SystemExit(1) from exc
    except OSError as exc:
        emit_diagnostic("databricks_cli_unavailable")
        raise SystemExit(1) from exc
    if result["timed_out"]:
        emit_diagnostic("databricks_cli_timed_out")
        raise SystemExit(1)
    if result["output_limited"]:
        emit_diagnostic(
            "databricks_cli_output_exceeded",
            stderr=_process_output_metadata(result["stderr"]),
            stdout=_process_output_metadata(result["stdout"]),
        )
        raise SystemExit(1)

    if result["returncode"] != 0:
        emit_diagnostic(
            "databricks_cli_failed",
            exit_code=result["returncode"],
            stderr=_process_output_metadata(result["stderr"]),
            stdout=_process_output_metadata(result["stdout"]),
        )
        raise SystemExit(result["returncode"] if result["returncode"] > 0 else 1)

    try:
        return json.loads(result["stdout"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        emit_diagnostic(
            "databricks_cli_invalid_json",
            stdout=_process_output_metadata(result["stdout"]),
        )
        raise SystemExit(1) from exc


def cli_post_json(path, body, timeout_seconds=CLI_TIMEOUT_SECONDS):
    try:
        with private_json_file(body) as body_path:
            return cli_json("api", "post", path, "--json", f"@{body_path}", timeout_seconds=timeout_seconds)
    except (RecursionError, RuntimeBoundaryError, TypeError, ValueError):
        emit_diagnostic("private_runtime_failure")
        raise SystemExit(2)
"""
)

GET_TEMPLATE = Template(
    '''"""Generated Databricks API GET script."""

$cli_helper

REQUEST_PATH = $path


def main() -> int:
    response = cli_json("api", "get", REQUEST_PATH)
    emit_response_summary(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
)

POST_TEMPLATE = Template(
    '''"""Generated Databricks API POST script."""

$cli_helper

REQUEST_PATH = $path
REQUEST_BODY = $body_literal
BODY_FILE = $body_file


def request_body():
    return REQUEST_BODY if REQUEST_BODY is not None else load_runtime_json(BODY_FILE)


def main() -> int:
    response = cli_post_json(REQUEST_PATH, request_body())
    emit_response_summary(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
)

SQL_TEMPLATE = Template(
    '''"""Generated Databricks SQL Statement Execution script."""

$cli_helper

WAREHOUSE_ID = $warehouse_id
STATEMENT_FILE = $statement_file
WAIT_TIMEOUT = $wait_timeout
POLL_SECONDS = 2.0
DEFAULT_POLL_DEADLINE_SECONDS = $poll_deadline_seconds


def positive_deadline(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("poll deadline must be a finite number of seconds") from exc
    if isinstance(value, bool) or not math.isfinite(seconds) or not 1 <= seconds <= $max_poll_deadline_seconds:
        raise argparse.ArgumentTypeError("poll deadline must be between 1 and $max_poll_deadline_seconds seconds")
    return seconds


def api_get(path, timeout_seconds):
    return cli_json("api", "get", path, timeout_seconds=timeout_seconds)


def api_post(path, body, timeout_seconds):
    return cli_post_json(path, body, timeout_seconds=timeout_seconds)


def remaining_seconds(deadline):
    return deadline - time.monotonic()


def run_sql(poll_deadline_seconds=DEFAULT_POLL_DEADLINE_SECONDS):
    if isinstance(poll_deadline_seconds, bool):
        emit_diagnostic("invalid_poll_deadline")
        return 2
    try:
        deadline_seconds = float(poll_deadline_seconds)
    except (TypeError, ValueError):
        emit_diagnostic("invalid_poll_deadline")
        return 2
    if not math.isfinite(deadline_seconds) or not 1 <= deadline_seconds <= $max_poll_deadline_seconds:
        emit_diagnostic("invalid_poll_deadline")
        return 2

    deadline = time.monotonic() + deadline_seconds
    request_body = {
        "warehouse_id": WAREHOUSE_ID,
        "statement": load_runtime_text(STATEMENT_FILE, "statement"),
        "wait_timeout": WAIT_TIMEOUT,
    }
    response = api_post("/api/2.0/sql/statements", request_body, remaining_seconds(deadline))
    identifier = statement_id(response)
    state = statement_state(response)
    if identifier is None:
        emit_diagnostic("sql_submission_missing_statement_id", state=state)
        return 1

    while state in {"PENDING", "RUNNING"}:
        remaining = remaining_seconds(deadline)
        if remaining <= 0:
            emit_diagnostic("sql_poll_deadline_exceeded", statement_id=identifier, state=state)
            return 1
        time.sleep(min(POLL_SECONDS, remaining))
        remaining = remaining_seconds(deadline)
        if remaining <= 0:
            emit_diagnostic("sql_poll_deadline_exceeded", statement_id=identifier, state=state)
            return 1
        response = api_get(f"/api/2.0/sql/statements/{identifier}", remaining)
        state = statement_state(response)

    if state != "SUCCEEDED":
        summary = response_summary(response)
        emit_diagnostic(
            "sql_statement_not_succeeded",
            statement_id=identifier,
            state=state,
            error_code=summary.get("error_code"),
        )
        return 1
    if result_is_incomplete(response):
        emit_diagnostic("sql_result_incomplete", statement_id=identifier, state=state)
        return 1

    print(json.dumps({"statement_id": identifier, "state": state}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute a Databricks SQL statement safely")
    parser.add_argument(
        "--poll-deadline-seconds",
        type=positive_deadline,
        default=DEFAULT_POLL_DEADLINE_SECONDS,
        help="Total monotonic submit-and-poll deadline; no cancellation is attempted on expiry.",
    )
    args = parser.parse_args()
    return run_sql(args.poll_deadline_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
'''
)


def reject_secret_like_text(value: str, field_name: str) -> str:
    """Reject an argument that would embed a recognizable secret in source."""
    normalized = value.strip()
    if not normalized or has_token_pattern(normalized):
        raise argparse.ArgumentTypeError(
            f"{field_name} must be non-empty and must not contain credential material."
        )
    return normalized


def profile_arg(value: str) -> str:
    """Validate a profile without constraining legitimate quoted profile names."""
    return reject_secret_like_text(value, "profile")


def api_path_arg(value: str) -> str:
    """Validate a Databricks REST path without permitting credential query strings."""
    normalized = reject_secret_like_text(value, "path")
    if not normalized.startswith("/api/"):
        raise argparse.ArgumentTypeError("API paths must start with /api/.")
    return normalized


def runtime_file_arg(value: str) -> str:
    """Validate a runtime path without reading its potentially sensitive contents."""
    return reject_secret_like_text(value, "runtime file path")


def warehouse_id_arg(value: str) -> str:
    """Validate a warehouse identifier before it is written to source."""
    return reject_secret_like_text(value, "warehouse ID")


def wait_timeout_arg(value: str) -> str:
    """Validate Statement API wait-timeout syntax documented by Databricks."""
    normalized = value.strip()
    if not re.fullmatch(r"(?:0|[5-9]|[1-4][0-9]|50)s", normalized):
        raise argparse.ArgumentTypeError(
            "wait timeout must be 0s or between 5s and 50s."
        )
    return normalized


def poll_deadline_arg(value: str) -> float:
    """Validate a finite monotonic SQL polling deadline."""
    if isinstance(value, bool):
        raise argparse.ArgumentTypeError(
            "poll deadline must be a finite number of seconds."
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "poll deadline must be a finite number of seconds."
        ) from exc
    if not math.isfinite(seconds) or not 1 <= seconds <= MAX_POLL_DEADLINE_SECONDS:
        raise argparse.ArgumentTypeError(
            f"poll deadline must be between 1 and {int(MAX_POLL_DEADLINE_SECONDS)} seconds."
        )
    return seconds


def parse_json_body(raw_body: str) -> JsonBody:
    """Parse a non-sensitive JSON request body for source embedding."""
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("Invalid JSON body.") from exc
    if not isinstance(body, (dict, list)):
        raise argparse.ArgumentTypeError("JSON body must be an object or array.")
    if contains_sensitive_inline_data(body):
        raise argparse.ArgumentTypeError(
            "Inline JSON may contain only numeric, boolean, and null values; use --body-file for text or credentials."
        )
    return body


def render_cli_helper(profile: str, extra_imports: str = "") -> str:
    """Render the standalone, redacted CLI boundary used by generated scripts."""
    return CLI_HELPER_TEMPLATE.substitute(
        profile=repr(profile),
        extra_imports=extra_imports,
        runtime_support=generated_runtime_support(),
    )


def render_get_script(profile: str, path: str) -> str:
    """Render a reusable GET helper with safe response summaries."""
    safe_profile = profile_arg(profile)
    safe_path = api_path_arg(path)
    return GET_TEMPLATE.substitute(
        cli_helper=render_cli_helper(safe_profile), path=repr(safe_path)
    )


def render_post_script(
    profile: str,
    path: str,
    body: JsonBody | None = None,
    body_file: str | None = None,
) -> str:
    """Render a POST helper with an inline-safe or runtime-file request body."""
    if (body is None) == (body_file is None):
        raise ValueError("Specify exactly one of body or body_file.")
    if body is not None and contains_sensitive_inline_data(body):
        raise ValueError("Sensitive request bodies must be loaded from a runtime file.")
    safe_profile = profile_arg(profile)
    safe_path = api_path_arg(path)
    safe_body_file = runtime_file_arg(body_file) if body_file is not None else None
    return POST_TEMPLATE.substitute(
        cli_helper=render_cli_helper(safe_profile),
        path=repr(safe_path),
        body_literal=pformat(body, width=88) if body is not None else "None",
        body_file=repr(safe_body_file),
    )


def render_sql_script(
    profile: str,
    warehouse_id: str,
    statement_file: str,
    wait_timeout: str,
    poll_deadline_seconds: float,
) -> str:
    """Render a bounded Statement API helper that reads SQL only at runtime."""
    safe_profile = profile_arg(profile)
    safe_warehouse_id = warehouse_id_arg(warehouse_id)
    safe_statement_file = runtime_file_arg(statement_file)
    safe_wait_timeout = wait_timeout_arg(wait_timeout)
    safe_deadline = poll_deadline_arg(str(poll_deadline_seconds))
    return SQL_TEMPLATE.substitute(
        cli_helper=render_cli_helper(safe_profile, extra_imports="import time"),
        warehouse_id=repr(safe_warehouse_id),
        statement_file=repr(safe_statement_file),
        wait_timeout=repr(safe_wait_timeout),
        poll_deadline_seconds=repr(safe_deadline),
        max_poll_deadline_seconds=repr(MAX_POLL_DEADLINE_SECONDS),
    )


def write_script(output_path: Path, content: str) -> None:
    """Write the rendered script without creating request-content sidecars."""
    output_path = assert_private_git_target(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except OSError as exc:
        raise RuntimeBoundaryError("Private output target is unavailable.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
        handle.write(content)


def compile_generated_script(path: Path) -> None:
    """Compile a generated script to confirm it is syntactically valid."""
    py_compile.compile(str(path), doraise=True)


def validate_templates() -> None:
    """Render each template and confirm the outputs compile."""
    with TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        get_path = temp_dir / "query_get.py"
        post_path = temp_dir / "query_post.py"
        sql_path = temp_dir / "query_sql.py"
        write_script(
            get_path, render_get_script("VALIDATION_PROFILE", "/api/2.0/clusters/list")
        )
        write_script(
            post_path,
            render_post_script(
                "VALIDATION_PROFILE", "/api/2.0/mlflow/runs/search", {"max_results": 5}
            ),
        )
        write_script(
            sql_path,
            render_sql_script(
                "VALIDATION_PROFILE",
                "warehouse-123",
                "private-statement.sql",
                "50s",
                300.0,
            ),
        )
        compile_generated_script(get_path)
        compile_generated_script(post_path)
        compile_generated_script(sql_path)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the deterministic renderer."""
    parser = argparse.ArgumentParser(
        description="Render privacy-safe Databricks API scripts"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="Render a GET request script")
    get_parser.add_argument("--profile", required=True, type=profile_arg)
    get_parser.add_argument("--path", required=True, type=api_path_arg)
    get_parser.add_argument("--output", required=True)

    post_parser = subparsers.add_parser("post", help="Render a POST request script")
    post_parser.add_argument("--profile", required=True, type=profile_arg)
    post_parser.add_argument("--path", required=True, type=api_path_arg)
    body_group = post_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body")
    body_group.add_argument("--body-file", type=runtime_file_arg)
    post_parser.add_argument("--output", required=True)

    sql_parser = subparsers.add_parser(
        "sql", help="Render a bounded SQL statement execution script"
    )
    sql_parser.add_argument("--profile", required=True, type=profile_arg)
    sql_parser.add_argument("--warehouse-id", required=True, type=warehouse_id_arg)
    sql_parser.add_argument("--statement-file", required=True, type=runtime_file_arg)
    sql_parser.add_argument("--wait-timeout", default="50s", type=wait_timeout_arg)
    sql_parser.add_argument(
        "--poll-deadline-seconds", default=300.0, type=poll_deadline_arg
    )
    sql_parser.add_argument("--output", required=True)

    subparsers.add_parser("validate", help="Validate rendered template syntax")
    return parser


def main() -> int:
    """Render the selected helper without reading runtime input files."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "validate":
        validate_templates()
        print("Validated rendered templates")
        return 0

    output_path = Path(args.output).resolve()
    if args.command == "get":
        content = render_get_script(args.profile, args.path)
    elif args.command == "post":
        try:
            body = parse_json_body(args.body) if args.body is not None else None
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
        content = render_post_script(
            args.profile, args.path, body=body, body_file=args.body_file
        )
    else:
        content = render_sql_script(
            args.profile,
            args.warehouse_id,
            args.statement_file,
            args.wait_timeout,
            args.poll_deadline_seconds,
        )
    try:
        write_script(output_path, content)
    except RuntimeBoundaryError:
        parser.error("Output target failed the private-runtime boundary.")
    print("Wrote generated script")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
