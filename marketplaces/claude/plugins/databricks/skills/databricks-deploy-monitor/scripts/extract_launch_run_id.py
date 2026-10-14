#!/usr/bin/env python3
"""Extract one verified Databricks job run ID from DAB launch output.

This helper deliberately understands only the human-facing ``Run URL:`` line
emitted by the launch command.  It does not inspect Jobs list-runs output or
assume an undocumented ``bundle run -o json`` response schema.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


MAX_LAUNCH_OUTPUT_BYTES = 64 * 1024
MAX_RUN_URL_BYTES = 4 * 1024
MAX_INT64 = 9_223_372_036_854_775_807
RUN_URL_LINE = re.compile(r"^\s*Run URL:\s*(https://\S+)\s*$", re.MULTILINE)
HASH_RUN_PATH = re.compile(r"^job/([1-9][0-9]*)/run/([1-9][0-9]*)$")
CANONICAL_RUN_PATH = re.compile(r"^/jobs/([1-9][0-9]*)/runs/([1-9][0-9]*)$")
HASH_WORKSPACE_QUERY = re.compile(r"^o=([1-9][0-9]*)$")


@dataclass(frozen=True)
class IdentityError(Exception):
    """A concrete reason that launch output cannot identify one run."""

    code: str
    message: str


def _positive_int64(value: str, label: str) -> int:
    number = int(value)
    if number > MAX_INT64:
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            f"The Run URL {label} is outside the positive signed-64-bit range.",
        )
    return number


def _canonical_workspace_host(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise IdentityError(
            "monitoring_identity_invalid_workspace_host",
            "The expected workspace host is not a valid HTTPS origin.",
        ) from error
    try:
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as error:
        raise IdentityError(
            "monitoring_identity_invalid_workspace_host",
            "The expected workspace host is not a valid HTTPS origin.",
        ) from error
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or hostname is None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise IdentityError(
            "monitoring_identity_invalid_workspace_host",
            "The expected workspace host must be one HTTPS origin with no path, query, or fragment.",
        )
    return hostname.casefold()


def extract_launch_run_id(launch_output: str, workspace_host: str) -> int:
    """Return the sole positive run ID from a launch's matching Run URL line."""

    if len(launch_output.encode("utf-8")) > MAX_LAUNCH_OUTPUT_BYTES:
        raise IdentityError(
            "monitoring_identity_launch_output_too_large",
            "The captured launch output exceeds 65536 bytes; capture the launch Run URL line and retry extraction.",
        )

    expected_host = _canonical_workspace_host(workspace_host)
    candidates = RUN_URL_LINE.findall(launch_output)
    if not candidates:
        raise IdentityError(
            "monitoring_identity_missing",
            "No direct Run URL was captured from this launch; do not guess a run from job history.",
        )
    if len(candidates) != 1:
        raise IdentityError(
            "monitoring_identity_ambiguous",
            "The launch output has multiple Run URL lines; do not choose one or query the newest job run.",
        )

    run_url = candidates[0]
    if len(run_url.encode("utf-8")) > MAX_RUN_URL_BYTES:
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            "The captured Run URL exceeds 4096 bytes.",
        )
    try:
        parsed = urlsplit(run_url)
    except ValueError as error:
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            "The captured Run URL is malformed.",
        ) from error
    try:
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as error:
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            "The captured Run URL is malformed.",
        ) from error
    if (
        parsed.scheme != "https"
        or hostname is None
        or hostname.casefold() != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            "The launch Run URL is not an HTTPS run URL for the verified workspace host.",
        )

    hash_query = HASH_WORKSPACE_QUERY.fullmatch(parsed.query)
    match = None
    if parsed.path in ("", "/") and hash_query is not None:
        match = HASH_RUN_PATH.fullmatch(parsed.fragment)
        if match is not None:
            _positive_int64(hash_query.group(1), "workspace ID")
    elif not parsed.fragment and CANONICAL_RUN_PATH.fullmatch(parsed.path) is not None:
        if parsed.query and hash_query is None:
            match = None
        else:
            match = CANONICAL_RUN_PATH.fullmatch(parsed.path)
            if hash_query is not None:
                _positive_int64(hash_query.group(1), "workspace ID")
    if match is None:
        raise IdentityError(
            "monitoring_identity_invalid_run_url",
            "The Run URL must be the exact direct hash or canonical job-run form for the verified workspace.",
        )
    _positive_int64(match.group(1), "job ID")
    return _positive_int64(match.group(2), "run ID")


def _read_launch_output(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_LAUNCH_OUTPUT_BYTES + 1)
    except OSError as error:
        raise IdentityError(
            "monitoring_identity_launch_output_unavailable",
            "The captured launch output file cannot be read.",
        ) from error
    if len(content) > MAX_LAUNCH_OUTPUT_BYTES:
        raise IdentityError(
            "monitoring_identity_launch_output_too_large",
            "The captured launch output exceeds 65536 bytes; capture the launch Run URL line and retry extraction.",
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IdentityError(
            "monitoring_identity_launch_output_invalid",
            "The captured launch output is not valid UTF-8 text.",
        ) from error


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract one Databricks run ID from direct DAB launch output."
    )
    parser.add_argument(
        "--workspace-host",
        required=True,
        help="Verified HTTPS workspace origin, for example https://workspace.example",
    )
    parser.add_argument(
        "--launch-output-file",
        required=True,
        type=Path,
        help="UTF-8 capture of the same bundle run command's terminal output",
    )
    arguments = parser.parse_args(argv)
    try:
        launch_output = _read_launch_output(arguments.launch_output_file)
        run_id = extract_launch_run_id(launch_output, arguments.workspace_host)
        _emit({"ok": True, "run_id": run_id, "workspace_host": arguments.workspace_host})
        return 0
    except IdentityError as error:
        _emit({"error": {"code": error.code, "message": error.message}, "ok": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
