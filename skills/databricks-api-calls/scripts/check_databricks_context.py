"""Report a strictly allowlisted, redacted local Databricks context."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
from pathlib import Path
from typing import Any

from safe_databricks_diagnostics import (
    MAX_CLI_STDERR_BYTES,
    RuntimeBoundaryError,
    process_output_metadata,
    read_private_runtime_file,
    run_bounded_command,
    safe_host,
    safe_identifier,
    safe_profile_name,
)

COMMAND_TIMEOUT_SECONDS = 30
MAX_CONTEXT_STDOUT_BYTES = 64 * 1024
MAX_AUTH_DESCRIBE_JSON_BYTES = 16 * 1024
EFFECTIVE_CONTEXT_RECEIPT_VERSION = 1
SECRET_ENV_VARS = (
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_CONFIG_FILE",
)


def run_command(
    command: list[str],
    parse_current_user_response: bool = False,
) -> dict[str, Any]:
    """Run a local command and retain metadata, never its raw output."""
    try:
        result = run_bounded_command(
            command,
            COMMAND_TIMEOUT_SECONDS,
            stdout_limit=MAX_CONTEXT_STDOUT_BYTES,
            stderr_limit=MAX_CLI_STDERR_BYTES,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "command not found", "returncode": None}
    except OSError:
        return {"ok": False, "error": "command unavailable", "returncode": None}
    if result.timed_out:
        return {"ok": False, "error": "timeout", "returncode": None}
    if result.output_limited:
        return {
            "ok": False,
            "error": "command output exceeded safe limit",
            "returncode": result.returncode,
            "stdout": process_output_metadata(result.stdout),
            "stderr": process_output_metadata(result.stderr),
        }

    report: dict[str, Any] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": process_output_metadata(result.stdout),
        "stderr": process_output_metadata(result.stderr),
    }
    if report["ok"] and parse_current_user_response:
        report["principal"] = parse_current_user(
            result.stdout.decode("utf-8", errors="replace")
        )
    return report


def config_path_from_env() -> Path:
    """Return the Databricks config path without requiring it to exist."""
    configured = os.environ.get("DATABRICKS_CONFIG_FILE")
    return (
        Path(configured).expanduser() if configured else Path.home() / ".databrickscfg"
    )


def read_profile_config(config_path: Path) -> tuple[dict[str, dict[str, Any]], bool]:
    """Read profile metadata without exposing raw config or parse errors."""
    if not config_path.exists():
        return {}, True
    parser = configparser.ConfigParser(interpolation=None)
    try:
        raw = read_private_runtime_file(config_path, "profile_config")
        parser.read_string(raw.decode("utf-8"))
    except (RuntimeBoundaryError, UnicodeDecodeError, configparser.Error):
        return {}, False

    profiles: dict[str, dict[str, Any]] = {}
    for profile_name in parser.sections():
        section = parser[profile_name]
        profiles[profile_name] = {
            "host": safe_host(section.get("host")),
            "has_token": bool(section.get("token")),
            "has_client_id": bool(section.get("client_id")),
            "has_client_secret": bool(section.get("client_secret")),
            "has_auth_type": bool(section.get("auth_type")),
        }
    return profiles, True


def env_presence() -> dict[str, bool]:
    """Return only boolean presence for Databricks-related environment variables."""
    return {env_var: bool(os.environ.get(env_var)) for env_var in SECRET_ENV_VARS}


def parse_current_user(raw_stdout: str) -> dict[str, Any]:
    """Parse only explicit identity fields; malformed output never crosses this boundary."""
    try:
        payload = json.loads(raw_stdout)
    except (RecursionError, json.JSONDecodeError):
        return {"valid": False}
    if not isinstance(payload, dict):
        return {"valid": False}

    principal: dict[str, Any] = {"valid": True}
    user_name = safe_identifier(payload.get("userName"))
    identifier = safe_identifier(payload.get("id"))
    if user_name is not None:
        principal["userName"] = user_name
    if identifier is not None:
        principal["id"] = identifier
    if isinstance(payload.get("active"), bool):
        principal["active"] = payload["active"]
    return principal


def _unique_json_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    """Build a JSON object only when every key occurs once."""
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def receipt_profile_name(profile_name: object) -> str | None:
    """Return an explicit profile only when it can be emitted unchanged."""
    if (
        not isinstance(profile_name, str)
        or safe_profile_name(profile_name) != profile_name
    ):
        return None
    return profile_name


def unverified_effective_context_receipt(
    profile_name: object, error: str = "unverified effective context"
) -> dict[str, Any]:
    """Build the stable failure form without retaining raw CLI output."""
    return {
        "version": EFFECTIVE_CONTEXT_RECEIPT_VERSION,
        "ok": False,
        "profile": safe_profile_name(profile_name),
        "error": error,
    }


def parse_effective_context(
    raw_stdout: bytes, selected_profile: str
) -> tuple[str, str] | None:
    """Return the exact effective profile and safe host from describe JSON."""
    receipt_profile = receipt_profile_name(selected_profile)
    if receipt_profile is None:
        return None
    if len(raw_stdout) > MAX_AUTH_DESCRIBE_JSON_BYTES:
        return None
    try:
        payload = json.loads(
            raw_stdout.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (RecursionError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    details = payload.get("details")
    if not isinstance(details, dict):
        return None
    configuration = details.get("configuration")
    if not isinstance(configuration, dict):
        return None
    profile = configuration.get("profile")
    if not isinstance(profile, dict):
        return None
    effective_profile = profile.get("value")
    effective_host = safe_host(details.get("host"))
    if effective_profile != selected_profile or effective_host is None:
        return None
    return receipt_profile, effective_host


def effective_context_receipt(profile_name: str) -> dict[str, Any]:
    """Prove one explicitly selected CLI profile's effective host without raw output."""
    receipt = unverified_effective_context_receipt(profile_name)
    if receipt_profile_name(profile_name) is None:
        return unverified_effective_context_receipt(
            profile_name, "unsupported profile name"
        )
    try:
        result = run_bounded_command(
            [
                "databricks",
                "auth",
                "describe",
                "--profile",
                profile_name,
                "-o",
                "json",
            ],
            COMMAND_TIMEOUT_SECONDS,
            stdout_limit=MAX_CONTEXT_STDOUT_BYTES,
            stderr_limit=MAX_CLI_STDERR_BYTES,
        )
    except (FileNotFoundError, OSError):
        return receipt
    if result.timed_out or result.output_limited or result.returncode != 0:
        return receipt
    parsed = parse_effective_context(result.stdout, profile_name)
    if parsed is None:
        return receipt
    effective_profile, effective_host = parsed
    return {
        "version": EFFECTIVE_CONTEXT_RECEIPT_VERSION,
        "ok": True,
        "profile": effective_profile,
        "host": effective_host,
    }


def collect_profile_context(
    profile_name: str,
    profile_config: dict[str, Any],
    databricks_found: bool,
) -> dict[str, Any]:
    """Collect allowlisted host, credential-presence, and principal context for one profile."""
    profile_report: dict[str, Any] = {
        "profile": safe_profile_name(profile_name),
        "configured": bool(profile_config),
        "host": profile_config.get("host"),
        "credential_source": "CLI profile" if profile_config else "unknown",
        "config_has_token": bool(profile_config.get("has_token")),
        "config_has_client_id": bool(profile_config.get("has_client_id")),
        "config_has_client_secret": bool(profile_config.get("has_client_secret")),
        "config_has_auth_type": bool(profile_config.get("has_auth_type")),
    }
    if receipt_profile_name(profile_name) is None:
        profile_report["effective_context"] = unverified_effective_context_receipt(
            profile_name, "unsupported profile name"
        )
        profile_report["current_user"] = {
            "ok": False,
            "error": "unsupported profile name",
        }
        return profile_report
    if not databricks_found:
        profile_report["effective_context"] = unverified_effective_context_receipt(
            profile_name
        )
        profile_report["current_user"] = {
            "ok": False,
            "error": "databricks CLI not found",
        }
        return profile_report

    profile_report["effective_context"] = effective_context_receipt(profile_name)

    current_user_result = run_command(
        ["databricks", "current-user", "me", "--profile", profile_name, "-o", "json"],
        parse_current_user_response=True,
    )
    user_report: dict[str, Any] = {
        "ok": current_user_result["ok"],
        "returncode": current_user_result["returncode"],
    }
    if current_user_result["ok"]:
        user_report["principal"] = current_user_result.get(
            "principal", {"valid": False}
        )
    else:
        user_report["error"] = "databricks current-user command failed"
    profile_report["current_user"] = user_report
    return profile_report


def build_report(profile_names: list[str]) -> dict[str, Any]:
    """Build a privacy-safe, explicitly allowlisted Databricks context report."""
    databricks_found = bool(shutil.which("databricks"))
    config_path = config_path_from_env()
    profile_configs, config_safe_to_read = read_profile_config(config_path)
    # Describe receives only an explicit profile; config discovery never chooses DEFAULT.
    selected_profiles = profile_names
    return {
        "commands": {"databricks": databricks_found},
        "config": {
            "exists": config_path.exists(),
            "safe_to_read": config_safe_to_read,
            "profiles_found": [
                safe_profile_name(profile) for profile in sorted(profile_configs)
            ],
        },
        "environment": env_presence(),
        "profiles": [
            collect_profile_context(
                profile_name, profile_configs.get(profile_name, {}), databricks_found
            )
            for profile_name in selected_profiles
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Report redacted local Databricks context"
    )
    parser.add_argument(
        "--profiles",
        nargs="*",
        default=[],
        help="Exact profile names to inspect. Does not select a default profile.",
    )
    return parser


def main() -> int:
    """Print the allowlisted context report as JSON."""
    args = build_parser().parse_args()
    print(json.dumps(build_report(args.profiles), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
