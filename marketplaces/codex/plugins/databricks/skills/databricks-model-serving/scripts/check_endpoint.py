#!/usr/bin/env python3
"""Check Databricks model serving endpoint health and semantic readiness.

The doctor deliberately keeps request and response content out of its output. It
reports bounded shapes, counts, configured limits, and assertion results only.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import itertools
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from logical_types import matches_logical_type


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4
DEFAULT_EVENT_LIMIT = 10
DEFAULT_HTTP_TIMEOUT_SECONDS = 60
DEFAULT_RETRY_TIMEOUT_SECONDS = 60
DEFAULT_WAIT_READY_SECONDS = 600
DEFAULT_POLL_INTERVAL_SECONDS = 15

# These local caps intentionally sit well below Databricks' 16 MB custom-model
# request limit so the diagnostic never allocates a production-sized fixture.
MAX_SAMPLE_BYTES = 1_000_000
MAX_JSONL_TOTAL_BYTES = MAX_SAMPLE_BYTES
MAX_JSONL_RECORD_BYTES = 100_000
MAX_SAMPLE_RECORDS = 100
MAX_RESPONSE_RECORDS = 100
MAX_RESPONSE_DEPTH = 16
MAX_RESPONSE_NODES = 5_000
MAX_RESPONSE_OBJECT_ITEMS = 100
MAX_RESPONSE_STRING_BYTES = 32_768
MAX_RESPONSE_TOTAL_STRING_BYTES = 250_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 20_000
MAX_JSON_STRING_BYTES = 32_768
MAX_JSON_TOTAL_STRING_BYTES = 250_000
MAX_OBJECT_DEPTH = 16
MAX_OBJECT_NODES = 5_000
MAX_OBJECT_ITEMS = 100
MAX_TEXT_CHARS = 256
MAX_WAIT_READY_SECONDS = 3_600
MAX_POLL_INTERVAL_SECONDS = 300
MAX_HTTP_TIMEOUT_SECONDS = 600
MAX_RETRY_TIMEOUT_SECONDS = 600

REQUEST_PAYLOAD_KEYS = {
    "dataframe_records",
    "dataframe_split",
    "instances",
    "inputs",
    "messages",
    "prompt",
    "input",
}
DEFAULT_FALLBACK_LABELS = {"NO_OPINION", "MODEL_SCORED"}
ALL_TELEMETRY_FEATURES = {
    "TELEMETRY_FEATURE_LOGS",
    "TELEMETRY_FEATURE_TRACES",
    "TELEMETRY_FEATURE_METRICS",
    "TELEMETRY_FEATURE_INFERENCE_TABLE",
}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(authorization|credential|password|secret|token|api[_-]?key|cookie)"
)
BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/-]+")
BASIC_PATTERN = re.compile(r"(?i)(basic\s+)[a-z0-9+/=._-]+")
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)((?:[\"']?(?:api[_-]?key|token|secret|password|authorization)[\"']?)\s*[=:]\s*[\"']?)[^\s,;\"'}]+"
)
ALLOWED_ERROR_CODES = {
    "BAD_REQUEST",
    "FORBIDDEN",
    "INTERNAL_ERROR",
    "INVALID_PARAMETER_VALUE",
    "MALFORMED_REQUEST",
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "REQUEST_LIMIT_EXCEEDED",
    "RESOURCE_DOES_NOT_EXIST",
    "TEMPORARILY_UNAVAILABLE",
    "TIMEOUT",
    "UNAUTHENTICATED",
    "UNAUTHORIZED",
}


@dataclass
class HealthCheck:
    """A safe, JSON-ready endpoint diagnostic result."""

    name: str
    passed: bool
    message: str
    details: dict[str, Any] | None = None
    warning: bool = False


@dataclass(frozen=True)
class FixtureBinding:
    """A fixture bound to one versioned row-oriented request contract."""

    request_shape: str
    input_row_count: int
    response_cardinality: str


@dataclass(frozen=True)
class RawResponseProjection:
    """Bounded transient response records plus value-free projection metadata."""

    records: tuple[dict[str, Any], ...]
    exact_record_count: int | None
    shape_kind: str
    top_level_field_count: int | None
    failure_kinds: tuple[str, ...]
    overflow: bool


@dataclass
class _ConversionBudget:
    """Mutable limits for safely projecting arbitrary SDK values."""

    nodes: int = 0
    seen: set[int] = field(default_factory=set)


class SampleInputError(ValueError):
    """A fixture is syntactically valid input but outside doctor limits."""


def redact_text(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Redact credential-like text and cap it before it reaches output."""
    redacted = BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = BASIC_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = KEY_VALUE_SECRET_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = redacted.replace("\r", " ").replace("\n", " ")
    if len(redacted) > limit:
        return f"{redacted[:limit]}…[truncated]"
    return redacted


def safe_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """Return a redacted, bounded scalar representation without custom reprs."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return redact_text(value, limit)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else "[nonfinite]"
    return f"[{type(value).__name__}]"


def safe_identifier(value: Any, limit: int = 96) -> str:
    """Keep report field names bounded and redacted."""
    return safe_text(value, limit)


def exception_metadata(exc: BaseException) -> dict[str, Any]:
    """Project exceptions without retaining their potentially sensitive text."""
    details: dict[str, Any] = {"type": type(exc).__name__}
    for attribute in ("status_code", "http_status_code", "status"):
        candidate = getattr(exc, attribute, None)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 100 <= candidate <= 599
        ):
            details["status_code"] = candidate
            break
    for attribute in ("error_code", "code"):
        candidate = getattr(exc, attribute, None)
        if isinstance(candidate, str) and candidate in ALLOWED_ERROR_CODES:
            details["error_code"] = candidate
            break
    return details


def object_to_dict(
    value: Any,
    *,
    max_depth: int = MAX_OBJECT_DEPTH,
    max_nodes: int = MAX_OBJECT_NODES,
    max_items: int = MAX_OBJECT_ITEMS,
) -> Any:
    """Safely convert SDK objects while bounding depth, fanout, and cycles."""
    budget = _ConversionBudget()

    def convert(current: Any, depth: int) -> Any:
        budget.nodes += 1
        if budget.nodes > max_nodes:
            return "[truncated: node budget]"
        if depth > max_depth:
            return "[truncated: depth budget]"
        if current is None or isinstance(current, bool | int):
            return current
        if isinstance(current, float):
            return current if math.isfinite(current) else "[nonfinite]"
        if isinstance(current, str):
            return redact_text(current)

        trackable = (
            isinstance(current, (dict, list, tuple))
            or hasattr(current, "as_dict")
            or hasattr(current, "to_dict")
        )
        if trackable:
            marker = id(current)
            if marker in budget.seen:
                return "[truncated: cycle]"
            budget.seen.add(marker)

        if isinstance(current, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(current.items()):
                if index >= max_items:
                    result["_truncated"] = "item budget"
                    break
                safe_key = safe_identifier(key)
                if SENSITIVE_KEY_PATTERN.search(safe_key):
                    result[safe_key] = "[REDACTED]"
                else:
                    result[safe_key] = convert(item, depth + 1)
            return result
        if isinstance(current, list | tuple):
            result_list = [
                convert(item, depth + 1)
                for item in itertools.islice(current, max_items)
            ]
            if len(current) > max_items:
                result_list.append("[truncated: item budget]")
            return result_list
        if hasattr(current, "as_dict"):
            try:
                return convert(current.as_dict(), depth + 1)
            except (
                Exception
            ) as exc:  # SDK conversion errors are never rendered verbatim.
                return {"_conversion_error": exception_metadata(exc)}
        if hasattr(current, "to_dict"):
            try:
                return convert(current.to_dict(), depth + 1)
            except (
                Exception
            ) as exc:  # SDK conversion errors are never rendered verbatim.
                return {"_conversion_error": exception_metadata(exc)}
        if hasattr(current, "value"):
            try:
                return convert(current.value, depth + 1)
            except Exception as exc:
                return {"_conversion_error": exception_metadata(exc)}
        return f"[{type(current).__name__}]"

    return convert(value, 0)


def get_endpoint(endpoint_name: str, client: Any) -> Any:
    """Fetch a serving endpoint from the Databricks client."""
    return client.serving_endpoints.get(endpoint_name)


def get_endpoint_config(endpoint: Any) -> Any | None:
    """Return the endpoint config when present."""
    return getattr(endpoint, "config", None)


def get_endpoint_state(endpoint: Any) -> str:
    """Return the endpoint ready state as a stable, bounded string."""
    state = getattr(endpoint, "state", None)
    ready = getattr(state, "ready", None) if state else None
    return safe_text(object_to_dict(ready) if ready is not None else "unknown", 64)


def enum_text(value: Any) -> str:
    """Return one bounded enum/scalar value without invoking custom reprs."""

    candidate = getattr(value, "value", value)
    return safe_text(candidate, 64)


def comparison_text(value: Any) -> str | None:
    """Return exact bounded SDK scalar text for in-memory comparisons only."""

    candidate = getattr(value, "value", value)
    if isinstance(candidate, str) and len(candidate) <= 2048:
        return candidate
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return str(candidate)
    return None


def get_config_update_state(endpoint: Any) -> str:
    """Return the endpoint config-update state from the same endpoint snapshot."""

    state = getattr(endpoint, "state", None)
    return enum_text(getattr(state, "config_update", None))


def field_token(field_path: str) -> str:
    """Return a stable opaque token for a declared response field path."""

    return hashlib.sha256(field_path.encode("utf-8")).hexdigest()[:16]


def get_served_entities(endpoint: Any) -> Iterable[Any]:
    """Return configured served entities without copying an unbounded iterable."""
    config = get_endpoint_config(endpoint)
    return getattr(config, "served_entities", None) or ()


def get_traffic_routes(endpoint: Any) -> Iterable[Any]:
    """Return configured traffic routes without copying an unbounded iterable."""
    config = get_endpoint_config(endpoint)
    traffic_config = getattr(config, "traffic_config", None)
    return getattr(traffic_config, "routes", None) or ()


def safe_number(value: Any) -> int | float | None:
    """Return finite non-bool numbers, otherwise omit the value."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        return None
    return value


def safe_integer(value: Any) -> int | None:
    """Return an exact non-bool integer, otherwise omit the value."""

    return value if isinstance(value, int) and not isinstance(value, bool) else None


def endpoint_summary(
    endpoint: Any, profile: str | None, host: str | None
) -> dict[str, Any]:
    """Build a compact endpoint manifest with no model output content."""
    served_entities = []
    for entity in itertools.islice(get_served_entities(endpoint), MAX_OBJECT_ITEMS):
        served_entities.append(
            {
                "name": safe_text(
                    getattr(entity, "name", None)
                    or getattr(entity, "served_entity_name", None)
                ),
                "entity_name": safe_text(getattr(entity, "entity_name", None)),
                "entity_version": safe_text(getattr(entity, "entity_version", None)),
                "workload_size": safe_text(getattr(entity, "workload_size", None)),
                "scale_to_zero_enabled": bool(
                    getattr(entity, "scale_to_zero_enabled", False)
                ),
            }
        )
    routes = []
    for route in itertools.islice(get_traffic_routes(endpoint), MAX_OBJECT_ITEMS):
        routes.append(
            {
                "served_entity_name": safe_text(
                    getattr(route, "served_entity_name", None)
                    or getattr(route, "served_model_name", None)
                ),
                "traffic_percentage": safe_number(
                    getattr(route, "traffic_percentage", None)
                ),
            }
        )
    return {
        "profile": safe_text(profile) if profile else None,
        "workspace_host": safe_text(host) if host else None,
        "endpoint_name": safe_text(getattr(endpoint, "name", None)),
        "state": get_endpoint_state(endpoint),
        "served_entities": served_entities,
        "traffic_routes": routes,
    }


def _call_endpoint_event_method(method: Callable[..., Any], endpoint_name: str) -> Any:
    """Choose one supported SDK signature before dispatch; never retry a call."""
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return method(name=endpoint_name)
    if "name" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return method(name=endpoint_name)
    return method(endpoint_name)


def event_excerpt(event: Any) -> dict[str, Any]:
    """Classify an event structurally without materializing any event values."""
    if isinstance(event, dict):
        return {"kind": "object"}
    if isinstance(event, list | tuple):
        return {"kind": "array"}
    if event is None:
        return {"kind": "null"}
    if isinstance(event, bool | int | float | str):
        return {"kind": "scalar"}
    return {"kind": "sdk_object"}


def list_endpoint_events(client: Any, endpoint_name: str, limit: int) -> dict[str, Any]:
    """Collect at most ``limit`` redacted endpoint event excerpts."""
    service = client.serving_endpoints
    method = getattr(service, "list_events", None) or getattr(service, "events", None)
    metadata = {
        "limit": limit,
        "available": False,
        "event_count": 0,
        "truncated": False,
    }
    if method is None:
        return {**metadata, "reason": "SDK does not expose endpoint events"}
    try:
        bounded_events = list(
            itertools.islice(
                _call_endpoint_event_method(method, endpoint_name), limit + 1
            )
        )
        metadata["available"] = True
        metadata["truncated"] = len(bounded_events) > limit
        selected = bounded_events[:limit]
        metadata["event_count"] = len(selected)
        event_kind_counts: dict[str, int] = {}
        for event in selected:
            kind = event_excerpt(event)["kind"]
            event_kind_counts[kind] = event_kind_counts.get(kind, 0) + 1
        metadata["event_kind_counts"] = event_kind_counts
        return metadata
    except Exception as exc:
        return {
            **metadata,
            "reason": "Endpoint events could not be collected",
            "error": exception_metadata(exc),
        }


def wait_for_ready(
    endpoint_name: str,
    client: Any,
    timeout_seconds: int,
    poll_interval_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> HealthCheck:
    """Poll until READY without starting another request after the deadline."""
    _positive_int(timeout_seconds, "timeout_seconds", MAX_WAIT_READY_SECONDS)
    _positive_int(
        poll_interval_seconds, "poll_interval_seconds", MAX_POLL_INTERVAL_SECONDS
    )
    deadline = monotonic() + timeout_seconds
    observed_states: list[str] = []
    last_state = "unknown"
    last_error: dict[str, Any] | None = None
    while monotonic() < deadline:
        try:
            endpoint = get_endpoint(endpoint_name, client)
            last_state = get_endpoint_state(endpoint)
            observed_states.append(last_state)
            if last_state == "READY":
                return HealthCheck(
                    name="wait_ready",
                    passed=True,
                    message="Endpoint reached READY state",
                    details={
                        "observed_state_count": len(observed_states),
                        "last_state": last_state,
                    },
                )
        except Exception as exc:
            last_state = "error"
            observed_states.append(last_state)
            last_error = exception_metadata(exc)
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(float(poll_interval_seconds), remaining))
    details: dict[str, Any] = {
        "observed_state_count": len(observed_states),
        "last_state": last_state,
        "timeout_seconds": timeout_seconds,
    }
    if last_error is not None:
        details["last_error"] = last_error
    return HealthCheck(
        name="wait_ready",
        passed=False,
        message="Endpoint did not reach READY before the readiness deadline",
        details=details,
    )


def failed_check(name: str, message: str, exc: BaseException) -> HealthCheck:
    """Create a failed check without retaining exception text."""
    return HealthCheck(
        name=name,
        passed=False,
        message=message,
        details={"error": exception_metadata(exc)},
    )


def _bounded_items(value: Iterable[Any]) -> list[Any]:
    """Materialize one endpoint collection only up to the diagnostic bound."""

    return list(itertools.islice(value, MAX_OBJECT_ITEMS + 1))


def _scaling_mode(entity: Any) -> str:
    present = {
        "workload_size": getattr(entity, "workload_size", None) is not None,
        "provisioned_concurrency": any(
            getattr(entity, name, None) is not None
            for name in ("min_provisioned_concurrency", "max_provisioned_concurrency")
        ),
        "provisioned_throughput": any(
            getattr(entity, name, None) is not None
            for name in ("min_provisioned_throughput", "max_provisioned_throughput")
        ),
        "provisioned_model_units": getattr(entity, "provisioned_model_units", None)
        is not None,
    }
    selected = [name for name, enabled in present.items() if enabled]
    return selected[0] if len(selected) == 1 else "unknown"


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _actual_entity(
    entity: Any,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    bool | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    bool | None,
]:
    name = getattr(entity, "name", None) or getattr(entity, "served_entity_name", None)
    return (
        comparison_text(name) or "",
        comparison_text(getattr(entity, "entity_name", None)) or "",
        comparison_text(getattr(entity, "entity_version", None)) or "",
        _scaling_mode(entity),
        comparison_text(getattr(entity, "workload_size", None)) or "",
        comparison_text(getattr(entity, "workload_type", None)) or "",
        _optional_bool(getattr(entity, "scale_to_zero_enabled", None)),
        safe_integer(getattr(entity, "min_provisioned_concurrency", None)),
        safe_integer(getattr(entity, "max_provisioned_concurrency", None)),
        safe_integer(getattr(entity, "min_provisioned_throughput", None)),
        safe_integer(getattr(entity, "max_provisioned_throughput", None)),
        safe_integer(getattr(entity, "provisioned_model_units", None)),
        _optional_bool(getattr(entity, "burst_scaling_enabled", None)),
    )


def _actual_route(route: Any) -> tuple[str, str, int | None]:
    served_name = getattr(route, "served_entity_name", None) or getattr(
        route, "served_model_name", None
    )
    route_name = getattr(route, "name", None) or served_name
    return (
        comparison_text(route_name) or "",
        comparison_text(served_name) or "",
        safe_integer(getattr(route, "traffic_percentage", None)),
    )


def _attribute_map(value: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(value, name, None) for name in names}


def _actual_telemetry(endpoint: Any) -> dict[str, Any] | None:
    config = get_endpoint_config(endpoint)
    telemetry = getattr(endpoint, "telemetry_config", None) or getattr(
        config, "telemetry_config", None
    )
    if telemetry is None:
        return None
    tables = getattr(telemetry, "table_names", None)
    inference = getattr(telemetry, "inference_table_config", None)
    features = getattr(telemetry, "enabled_telemetry_features", None) or ()
    normalized_features = {
        comparison_text(item) or "" for item in features
    } or ALL_TELEMETRY_FEATURES
    return {
        "table_names": {
            name: comparison_text(value)
            for name, value in _attribute_map(
                tables, ("logs_table", "metrics_table", "traces_table")
            ).items()
        },
        "inference_table_config": {
            "sampling_fraction": safe_number(
                getattr(inference, "sampling_fraction", None)
            )
        },
        "enabled_telemetry_features": sorted(normalized_features),
    }


def check_endpoint_snapshot(endpoint: Any, contract: Mapping[str, Any]) -> HealthCheck:
    """Validate exact expected state from one immutable post-wait SDK snapshot."""

    failures: list[str] = []
    expected_endpoint = contract["endpoint"]
    expected_deployment = contract["deployment"]
    state = get_endpoint_state(endpoint)
    config_update = get_config_update_state(endpoint)
    if state != "READY":
        failures.append("endpoint_not_ready")
    if config_update != "NOT_UPDATING":
        failures.append("config_update_not_settled")
    if getattr(endpoint, "pending_config", None) is not None:
        failures.append("pending_config_present")
    if comparison_text(getattr(endpoint, "name", None)) != expected_endpoint["name"]:
        failures.append("endpoint_name_mismatch")

    config = get_endpoint_config(endpoint)
    config_version = safe_integer(
        getattr(config, "config_version", None)
        if config is not None
        else getattr(endpoint, "config_version", None)
    )
    if config_version != expected_endpoint["expected_config_version"]:
        failures.append("config_version_mismatch")

    actual_entities = _bounded_items(get_served_entities(endpoint))
    if len(actual_entities) > MAX_OBJECT_ITEMS:
        failures.append("served_entity_bound_exceeded")
        actual_entities = actual_entities[:MAX_OBJECT_ITEMS]
    expected_entities = [
        (
            item["name"],
            item["entity_name"],
            str(item["entity_version"]),
            item["scaling_mode"],
            item.get("workload_size", ""),
            item["workload_type"],
            item["scale_to_zero_enabled"],
            item.get("min_provisioned_concurrency"),
            item.get("max_provisioned_concurrency"),
            item.get("min_provisioned_throughput"),
            item.get("max_provisioned_throughput"),
            item.get("provisioned_model_units"),
            item.get("burst_scaling_enabled"),
        )
        for item in expected_deployment["served_entities"]
    ]
    if Counter(map(_actual_entity, actual_entities)) != Counter(expected_entities):
        failures.append("served_entity_manifest_mismatch")

    actual_routes = _bounded_items(get_traffic_routes(endpoint))
    if len(actual_routes) > MAX_OBJECT_ITEMS:
        failures.append("traffic_route_bound_exceeded")
        actual_routes = actual_routes[:MAX_OBJECT_ITEMS]
    expected_routes = [
        (item["name"], item["served_entity_name"], item["traffic_percentage"])
        for item in expected_deployment["routes"]
    ]
    if Counter(map(_actual_route, actual_routes)) != Counter(expected_routes):
        failures.append("traffic_manifest_mismatch")

    expected_telemetry = contract["telemetry"]
    actual_telemetry = _actual_telemetry(endpoint)
    enabled_telemetry_keys = {
        "required",
        "mode",
        "table_names",
        "inference_table_config",
        "enabled_telemetry_features",
    }
    disabled_telemetry_keys = {"required", "mode"}
    telemetry_contract_valid = True
    if expected_telemetry.get("required") is False:
        telemetry_contract_valid = (
            set(expected_telemetry) == disabled_telemetry_keys
            and expected_telemetry.get("mode") == "disabled"
        )
    elif expected_telemetry.get("required") is True:
        telemetry_contract_valid = (
            set(expected_telemetry) == enabled_telemetry_keys
            and expected_telemetry.get("mode") == "unity_ai_gateway"
        )
    else:
        telemetry_contract_valid = False
    if not telemetry_contract_valid:
        failures.append("telemetry_contract_invalid")
    elif expected_telemetry["required"]:
        comparable = {
            "table_names": expected_telemetry["table_names"],
            "inference_table_config": expected_telemetry["inference_table_config"],
            "enabled_telemetry_features": sorted(
                expected_telemetry["enabled_telemetry_features"]
            ),
        }
        if actual_telemetry != comparable:
            failures.append("telemetry_manifest_mismatch")
    elif actual_telemetry is not None:
        sampling = actual_telemetry["inference_table_config"]["sampling_fraction"]
        if actual_telemetry["enabled_telemetry_features"] or (
            sampling is not None and sampling > 0
        ):
            failures.append("telemetry_enabled_but_expected_disabled")

    actual_route_optimized = _optional_bool(getattr(endpoint, "route_optimized", None))
    if actual_route_optimized != expected_endpoint["route_optimized"]:
        failures.append("route_optimized_state_mismatch")
    if expected_endpoint["route_optimized"]:
        actual_url = comparison_text(getattr(endpoint, "endpoint_url", None))
        try:
            actual_target = (
                normalize_endpoint_target(actual_url)
                if actual_url is not None
                else None
            )
        except ValueError:
            actual_target = None
        if actual_target != expected_endpoint["target_url"].rstrip("/"):
            failures.append("route_optimized_url_mismatch")
    elif comparison_text(getattr(endpoint, "endpoint_url", None)) not in {None, ""}:
        failures.append("unexpected_endpoint_url")
    creator = getattr(endpoint, "creator", None)
    if comparison_text(creator) != expected_endpoint["creator_principal"]:
        failures.append("creator_identity_mismatch")

    return HealthCheck(
        "expected_manifest",
        not failures,
        "Endpoint snapshot matches the expected rollout contract"
        if not failures
        else "Endpoint snapshot does not match the expected rollout contract",
        {
            "failure_count": len(failures),
            "failure_kinds": sorted(set(failures)),
            "state": state,
            "config_update": config_update,
            "config_version_matches": config_version
            == expected_endpoint["expected_config_version"],
            "served_entity_count": len(actual_entities),
            "traffic_route_count": len(actual_routes),
            "telemetry_expected": expected_telemetry.get("required") is True,
            "route_optimized_matches": actual_route_optimized
            == expected_endpoint["route_optimized"],
        },
    )


def check_route_transport(contract: Mapping[str, Any]) -> HealthCheck:
    """Keep optimized transport unverified until an authorized SP probe exists."""

    if not contract["endpoint"]["route_optimized"]:
        return HealthCheck(
            "route_transport",
            True,
            "Route-optimized transport is not applicable",
            {"verification": "not_applicable"},
        )
    return HealthCheck(
        "route_transport",
        False,
        "Route-optimized service-principal transport is unverified",
        {
            "verification": "unverified",
            "required_probe": "authorized_service_principal_data_plane",
            "workspace_client_query_is_proof": False,
        },
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object keys before a fixture becomes a payload."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SampleInputError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise SampleInputError(f"JSON contains non-finite constant {safe_text(value, 32)}")


def strict_json_loads(text: str) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants."""
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_nonfinite_constant,
    )


def validate_json_structure(value: Any) -> None:
    """Enforce depth, node, and string budgets on a parsed JSON fixture."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    total_string_bytes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise SampleInputError("JSON exceeds the node budget")
        if depth > MAX_JSON_DEPTH:
            raise SampleInputError("JSON exceeds the depth budget")
        if isinstance(current, float) and not math.isfinite(current):
            raise SampleInputError("JSON contains a non-finite number")
        if isinstance(current, str):
            byte_length = len(current.encode("utf-8"))
            if byte_length > MAX_JSON_STRING_BYTES:
                raise SampleInputError("JSON string exceeds the string budget")
            total_string_bytes += byte_length
            if total_string_bytes > MAX_JSON_TOTAL_STRING_BYTES:
                raise SampleInputError("JSON exceeds the total string budget")
            continue
        if isinstance(current, dict):
            for key, item in current.items():
                key_bytes = len(key.encode("utf-8"))
                if key_bytes > MAX_JSON_STRING_BYTES:
                    raise SampleInputError("JSON object key exceeds the string budget")
                total_string_bytes += key_bytes
                if total_string_bytes > MAX_JSON_TOTAL_STRING_BYTES:
                    raise SampleInputError("JSON exceeds the total string budget")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def read_limited_bytes(path: str | Path, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes so an oversize fixture is rejected early."""
    with Path(path).open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise SampleInputError(f"Fixture exceeds the {limit}-byte limit")
    return data


def _records_payload(records: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    if not records:
        raise SampleInputError("JSONL fixture contains no records")
    if len(records) > MAX_SAMPLE_RECORDS:
        raise SampleInputError("Fixture exceeds the record-count limit")
    return {"dataframe_records": records}, len(records)


def load_jsonl_payload(path: str | Path) -> tuple[dict[str, Any], int]:
    """Stream strict JSONL records with byte and count limits before allocation."""
    records: list[dict[str, Any]] = []
    total_bytes = 0
    with Path(path).open("rb") as handle:
        while True:
            line = handle.readline(MAX_JSONL_RECORD_BYTES + 1)
            if not line:
                break
            total_bytes += len(line)
            if total_bytes > MAX_JSONL_TOTAL_BYTES:
                raise SampleInputError("JSONL fixture exceeds the total-byte limit")
            if len(line) > MAX_JSONL_RECORD_BYTES:
                raise SampleInputError("JSONL record exceeds the per-record byte limit")
            stripped = line.strip()
            if not stripped:
                continue
            try:
                decoded = stripped.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise SampleInputError("JSONL fixture is not strict UTF-8") from exc
            record = strict_json_loads(decoded)
            validate_json_structure(record)
            if not isinstance(record, dict):
                raise SampleInputError("Every JSONL record must be an object")
            if len(records) >= MAX_SAMPLE_RECORDS:
                raise SampleInputError("JSONL fixture exceeds the record-count limit")
            records.append(record)
    return _records_payload(records)


def _validate_direct_value(value: Any, *, depth: int = 0) -> None:
    """Bound each direct-query record, object, sequence, and scalar value."""
    if depth > MAX_JSON_DEPTH:
        raise SampleInputError("Direct query record exceeds the depth budget")
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SampleInputError("Direct query record contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_JSON_STRING_BYTES:
            raise SampleInputError(
                "Direct query record exceeds the scalar string budget"
            )
        return
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_ITEMS:
            raise SampleInputError("Direct query object exceeds the field budget")
        for key, item in value.items():
            if len(key.encode("utf-8")) > MAX_JSON_STRING_BYTES:
                raise SampleInputError(
                    "Direct query object key exceeds the string budget"
                )
            _validate_direct_value(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_OBJECT_ITEMS:
            raise SampleInputError("Direct query sequence exceeds the item budget")
        for item in value:
            _validate_direct_value(item, depth=depth + 1)
        return
    raise SampleInputError("Direct query record contains an unsupported value type")


def _validate_record_list(
    shape_name: str,
    value: Any,
    *,
    object_records_only: bool,
) -> int:
    """Validate a count-bounded array before a request-shaped payload is returned."""
    if not isinstance(value, list):
        raise SampleInputError(f"{shape_name} must be an array")
    if not value:
        raise SampleInputError(f"{shape_name} must contain at least one record")
    if len(value) > MAX_SAMPLE_RECORDS:
        raise SampleInputError(f"{shape_name} exceeds the record-count limit")
    for record in value:
        if object_records_only and not isinstance(record, dict):
            raise SampleInputError(f"Every {shape_name} record must be an object")
        _validate_direct_value(record)
    return len(value)


def _validate_dataframe_split(value: Any) -> int:
    """Validate the DataFrame split shape and its bounded scalar rows."""
    if not isinstance(value, dict):
        raise SampleInputError("dataframe_split must be an object")
    if len(value) > MAX_OBJECT_ITEMS:
        raise SampleInputError("dataframe_split exceeds the field budget")
    if "data" not in value:
        raise SampleInputError("dataframe_split must include data")
    for key, item in value.items():
        if key != "data":
            _validate_direct_value(item)
    columns = value.get("columns")
    if columns is not None:
        if (
            not isinstance(columns, list)
            or len(columns) > MAX_OBJECT_ITEMS
            or not all(isinstance(column, str) for column in columns)
        ):
            raise SampleInputError(
                "dataframe_split columns must be a bounded array of strings"
            )
    data = value["data"]
    if not isinstance(data, list):
        raise SampleInputError("dataframe_split data must be an array")
    if not data:
        raise SampleInputError("dataframe_split data must contain at least one row")
    if len(data) > MAX_SAMPLE_RECORDS:
        raise SampleInputError("dataframe_split exceeds the record-count limit")
    for row in data:
        if not isinstance(row, list):
            raise SampleInputError("Every dataframe_split row must be an array")
        _validate_direct_value(row)
    return len(data)


def validate_query_payload(payload: dict[str, Any]) -> int:
    """Validate every recognized direct query shape before returning its payload."""
    if len(payload) > MAX_OBJECT_ITEMS:
        raise SampleInputError("Direct query payload exceeds the field budget")
    recognized_shapes = {
        "dataframe_records",
        "dataframe_split",
        "instances",
        "inputs",
        "messages",
    }
    for key, value in payload.items():
        if key not in recognized_shapes:
            _validate_direct_value(value)
    record_counts: list[int] = []
    if "dataframe_records" in payload:
        record_counts.append(
            _validate_record_list(
                "dataframe_records",
                payload["dataframe_records"],
                object_records_only=True,
            )
        )
    if "dataframe_split" in payload:
        record_counts.append(_validate_dataframe_split(payload["dataframe_split"]))
    if "instances" in payload:
        record_counts.append(
            _validate_record_list(
                "instances", payload["instances"], object_records_only=False
            )
        )
    if "messages" in payload:
        record_counts.append(
            _validate_record_list(
                "messages", payload["messages"], object_records_only=True
            )
        )
    if "inputs" in payload:
        inputs = payload["inputs"]
        if isinstance(inputs, list):
            record_counts.append(
                _validate_record_list("inputs", inputs, object_records_only=False)
            )
        else:
            _validate_direct_value(inputs)
            record_counts.append(1)
    if sum(record_counts) > MAX_SAMPLE_RECORDS:
        raise SampleInputError(
            "Direct query payload exceeds the total record-count limit"
        )
    return sum(record_counts) if record_counts else 1


def infer_sample_count(payload: dict[str, Any]) -> int:
    """Return the count only after every recognized shape has been validated."""
    return validate_query_payload(payload)


def load_sample_payload(
    sample_json: str | None, sample_jsonl: str | None
) -> tuple[dict[str, Any] | None, int]:
    """Load a bounded strict-JSON serving fixture without logging its content."""
    if sample_json and sample_jsonl:
        raise SampleInputError("Use only one of --sample-json or --sample-jsonl")
    if sample_jsonl:
        return load_jsonl_payload(sample_jsonl)
    if not sample_json:
        return None, 0
    raw = read_limited_bytes(sample_json, MAX_SAMPLE_BYTES)
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SampleInputError("JSON fixture is not strict UTF-8") from exc
    data = strict_json_loads(decoded)
    validate_json_structure(data)
    if isinstance(data, list):
        if len(data) > MAX_SAMPLE_RECORDS or not all(
            isinstance(record, dict) for record in data
        ):
            raise SampleInputError(
                "JSON record array exceeds limits or contains a non-object record"
            )
        return _records_payload(data)
    if isinstance(data, dict):
        if "name" in data:
            raise SampleInputError("Fixture payload must not set the endpoint name")
        if REQUEST_PAYLOAD_KEYS.intersection(data):
            return data, validate_query_payload(data)
        return _records_payload([data])
    raise SampleInputError(
        "Sample JSON must be an object, an array of objects, or a Databricks query payload"
    )


def _fixture_value_matches_type(value: Any, logical_type: str) -> bool:
    return matches_logical_type(value, logical_type)


def _validate_fixture_value(value: Any, declaration: Mapping[str, Any]) -> None:
    if value is None:
        if not declaration["nullable"]:
            raise SampleInputError("Fixture contains a null for a non-nullable input")
        return
    if not _fixture_value_matches_type(value, declaration["logical_type"]):
        raise SampleInputError("Fixture value does not match the input signature")


def bind_fixture_payload(
    payload: dict[str, Any], contract: Mapping[str, Any]
) -> FixtureBinding:
    """Bind one fixture to the exact versioned DataFrame request signature."""

    request_contract = contract["request_contract"]
    signature = request_contract["input_schema"]
    field_names = [field["name"] for field in signature]
    allowed_shapes = set(request_contract["allowed_shapes"])
    present_shapes = [
        shape for shape in ("dataframe_records", "dataframe_split") if shape in payload
    ]
    opaque_shapes = REQUEST_PAYLOAD_KEYS.intersection(payload) - set(present_shapes)
    if opaque_shapes or len(present_shapes) != 1 or len(payload) != 1:
        raise SampleInputError("Fixture must use one closed DataFrame request shape")
    shape = present_shapes[0]
    if shape not in allowed_shapes:
        raise SampleInputError("Fixture request shape is not allowed by the contract")

    if shape == "dataframe_records":
        records = payload[shape]
        if not isinstance(records, list) or not records:
            raise SampleInputError("dataframe_records must contain records")
        expected_fields = set(field_names)
        for record in records:
            if not isinstance(record, dict) or set(record) != expected_fields:
                raise SampleInputError(
                    "dataframe_records fields do not exactly match the input signature"
                )
            for declaration in signature:
                _validate_fixture_value(record[declaration["name"]], declaration)
        row_count = len(records)
    else:
        split = payload[shape]
        if not isinstance(split, dict) or set(split) != {"columns", "data"}:
            raise SampleInputError("dataframe_split must contain only columns and data")
        if split["columns"] != field_names:
            raise SampleInputError(
                "dataframe_split columns do not match the input signature order"
            )
        rows = split["data"]
        if not isinstance(rows, list) or not rows:
            raise SampleInputError("dataframe_split must contain rows")
        for row in rows:
            if not isinstance(row, list) or len(row) != len(signature):
                raise SampleInputError("dataframe_split row width is invalid")
            for value, declaration in zip(row, signature, strict=True):
                _validate_fixture_value(value, declaration)
        row_count = len(rows)
    return FixtureBinding(
        request_shape=shape,
        input_row_count=row_count,
        response_cardinality=request_contract["response_cardinality"],
    )


def query_endpoint(endpoint_name: str, client: Any, payload: dict[str, Any]) -> Any:
    """Issue exactly one query with the current SDK's ``name=`` signature."""
    return client.serving_endpoints.query(name=endpoint_name, **payload)


def _raw_response_root(response: Any) -> tuple[Any, str | None]:
    """Obtain one transient SDK value without redaction or string rendering."""

    if response is None or isinstance(
        response, (bool, int, float, str, dict, list, tuple)
    ):
        return response, None
    for method_name in ("as_dict", "to_dict"):
        method = getattr(response, method_name, None)
        if callable(method):
            try:
                return method(), None
            except Exception:
                return None, "conversion_error"
    return None, "unsupported_type"


def _bounded_utf8_size(value: str, limit: int) -> int:
    """Count UTF-8 bytes only until the caller's failure threshold is crossed."""

    total = 0
    for character in value:
        total += len(character.encode("utf-8"))
        if total > limit:
            break
    return total


def _raw_response_failure_kinds(value: Any) -> tuple[tuple[str, ...], bool]:
    """Validate transient raw values with shared depth, node, and string budgets."""

    failures: set[str] = set()
    overflow_kinds = {
        "depth_limit",
        "node_limit",
        "object_item_limit",
        "sequence_item_limit",
        "string_limit",
        "string_total_limit",
    }
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    nodes = 0
    total_string_bytes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_RESPONSE_NODES:
            failures.add("node_limit")
            break
        if depth > MAX_RESPONSE_DEPTH:
            failures.add("depth_limit")
            continue
        if current is None or isinstance(current, (bool, int)):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                failures.add("nonfinite_number")
            continue
        if isinstance(current, str):
            remaining_total = max(
                0, MAX_RESPONSE_TOTAL_STRING_BYTES - total_string_bytes
            )
            size = _bounded_utf8_size(
                current, max(MAX_RESPONSE_STRING_BYTES, remaining_total)
            )
            if size > MAX_RESPONSE_STRING_BYTES:
                failures.add("string_limit")
            total_string_bytes += size
            if total_string_bytes > MAX_RESPONSE_TOTAL_STRING_BYTES:
                failures.add("string_total_limit")
            continue
        if isinstance(current, dict):
            marker = id(current)
            if marker in seen:
                failures.add("cycle")
                continue
            seen.add(marker)
            if len(current) > MAX_RESPONSE_OBJECT_ITEMS:
                failures.add("object_item_limit")
                continue
            for key, item in current.items():
                if not isinstance(key, str):
                    failures.add("unsupported_key_type")
                    continue
                remaining_total = max(
                    0, MAX_RESPONSE_TOTAL_STRING_BYTES - total_string_bytes
                )
                key_size = _bounded_utf8_size(
                    key, max(MAX_RESPONSE_STRING_BYTES, remaining_total)
                )
                if key_size > MAX_RESPONSE_STRING_BYTES:
                    failures.add("string_limit")
                total_string_bytes += key_size
                if total_string_bytes > MAX_RESPONSE_TOTAL_STRING_BYTES:
                    failures.add("string_total_limit")
                stack.append((item, depth + 1))
            continue
        if isinstance(current, (list, tuple)):
            marker = id(current)
            if marker in seen:
                failures.add("cycle")
                continue
            seen.add(marker)
            if len(current) > MAX_RESPONSE_RECORDS and depth == 0:
                failures.add("record_limit")
                continue
            if len(current) > MAX_RESPONSE_OBJECT_ITEMS:
                failures.add("sequence_item_limit")
                continue
            stack.extend((item, depth + 1) for item in current)
            continue
        failures.add("unsupported_type")
    ordered = tuple(sorted(failures))
    return ordered, bool(failures.intersection(overflow_kinds | {"record_limit"}))


def project_raw_response(response: Any) -> RawResponseProjection:
    """Build bounded raw records for semantics and separate value-free metadata."""

    root, root_failure = _raw_response_root(response)
    if root_failure is not None:
        return RawResponseProjection(
            (), None, "sdk_object", None, (root_failure,), False
        )
    shape_kind = (
        "null"
        if root is None
        else "object"
        if isinstance(root, dict)
        else "array"
        if isinstance(root, (list, tuple))
        else "scalar"
    )
    top_level_field_count = len(root) if isinstance(root, dict) else None
    selected: Any = root
    envelope_name: str | None = None
    if isinstance(root, dict):
        for key in ("predictions", "outputs", "choices", "data"):
            if key in root:
                selected = root[key]
                envelope_name = key
                break
    if selected is None or selected == "" or selected == [] or selected == {}:
        return RawResponseProjection(
            (), 0, shape_kind, top_level_field_count, (), False
        )
    sequence = selected if isinstance(selected, (list, tuple)) else [selected]
    exact_count = len(sequence)
    if isinstance(root, dict) and len(root) > MAX_RESPONSE_OBJECT_ITEMS:
        return RawResponseProjection(
            (),
            exact_count,
            shape_kind,
            top_level_field_count,
            ("object_item_limit",),
            True,
        )
    if exact_count > MAX_RESPONSE_RECORDS:
        return RawResponseProjection(
            (),
            exact_count,
            shape_kind,
            top_level_field_count,
            ("record_limit",),
            True,
        )
    records = tuple(
        item if isinstance(item, dict) else {(envelope_name or "value"): item}
        for item in sequence
    )
    failure_kinds, overflow = _raw_response_failure_kinds(records)
    return RawResponseProjection(
        records if not failure_kinds else (),
        exact_count,
        shape_kind,
        top_level_field_count,
        failure_kinds,
        overflow,
    )


def response_report_projection(projection: RawResponseProjection) -> dict[str, Any]:
    """Create report metadata without consulting transient raw response values."""

    return {
        "kind": projection.shape_kind,
        "top_level_field_count": projection.top_level_field_count,
        "record_count": projection.exact_record_count,
        "record_count_exact": projection.exact_record_count is not None,
        "overflow": projection.overflow,
        "failure_kinds": list(projection.failure_kinds),
    }


def nested_value(record: dict[str, Any], field_path: str) -> tuple[bool, Any]:
    """Resolve a dotted field path from a response record."""
    current: Any = record
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def normalized_labels(labels: list[str]) -> set[str]:
    """Normalize declared labels for case-insensitive comparison."""
    return {label.casefold() for label in labels}


def parse_score_range(value: str) -> tuple[str, float, float]:
    """Parse finite ``FIELD:MIN:MAX`` score assertions."""
    parts = value.split(":")
    if len(parts) != 3 or not parts[0]:
        raise ValueError("Score range must be FIELD:MIN:MAX")
    try:
        minimum = float(parts[1])
        maximum = float(parts[2])
    except ValueError as exc:
        raise ValueError("Score range bounds must be numeric") from exc
    if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum > maximum:
        raise ValueError("Score range bounds must be finite and ordered")
    return parts[0], minimum, maximum


def check_required_fields(
    records: list[dict[str, Any]], required_fields: list[str], nullable_fields: set[str]
) -> list[str]:
    """Return required-field assertion failures without response values."""
    failures: list[str] = []
    for record in records:
        for declared_field in required_fields:
            present, value = nested_value(record, declared_field)
            if not present:
                failures.append("required_field_missing")
            elif value is None and declared_field not in nullable_fields:
                failures.append("required_field_null")
    return failures


def check_response_schema(
    records: list[dict[str, Any]], response_schema: list[dict[str, Any]]
) -> list[str]:
    """Validate every response field against its reconciled logical type."""

    failures: list[str] = []
    for record in records:
        for declaration in response_schema:
            present, value = nested_value(record, declaration["name"])
            if not present:
                failures.append("response_schema_field_missing")
            elif value is None:
                if not declaration["nullable"]:
                    failures.append("response_schema_field_null")
            elif not _fixture_value_matches_type(value, declaration["logical_type"]):
                failures.append("response_schema_type_mismatch")
    return failures


def check_score_ranges(
    records: list[dict[str, Any]], score_ranges: list[tuple[str, float, float]]
) -> list[str]:
    """Return finite score-range assertion failures without response scores."""
    failures: list[str] = []
    for record in records:
        for field_name, minimum, maximum in score_ranges:
            present, value = nested_value(record, field_name)
            if not present:
                failures.append("score_field_missing")
            elif safe_number(value) is None:
                failures.append("score_not_finite_number")
            elif value < minimum or value > maximum:
                failures.append("score_outside_range")
    return failures


def check_label_rules(
    records: list[dict[str, Any]],
    label_fields: list[str],
    allowed_labels: set[str],
    forbidden_labels: set[str],
) -> list[str]:
    """Validate declared label fields without emitting model label values."""
    failures: list[str] = []
    for record in records:
        for field_name in label_fields:
            present, value = nested_value(record, field_name)
            if not present:
                failures.append("label_field_missing")
                continue
            if not isinstance(value, str):
                failures.append("label_not_string")
                continue
            normalized = value.casefold()
            if allowed_labels and normalized not in allowed_labels:
                failures.append("label_not_allowed")
            if normalized in forbidden_labels:
                failures.append("label_forbidden")
    return failures


def non_fallback_rate(
    records: list[dict[str, Any]], label_fields: list[str], fallback_labels: set[str]
) -> float:
    """Return the rate using only explicitly declared label fields."""
    if not records:
        return 0.0
    non_fallback_count = 0
    for record in records:
        labels = []
        for field_name in label_fields:
            present, value = nested_value(record, field_name)
            if present and isinstance(value, str):
                labels.append(value.casefold())
        if labels and not set(labels).intersection(fallback_labels):
            non_fallback_count += 1
    return non_fallback_count / len(records)


def check_identical_outputs(
    records: list[dict[str, Any]], allow_identical_outputs: bool
) -> list[str]:
    """Detect identical bounded records without including content in diagnostics."""
    if allow_identical_outputs or len(records) <= 1:
        return []
    fingerprints = {
        hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).digest()
        for record in records
    }
    return (
        ["all response records are identical across multiple samples"]
        if len(fingerprints) == 1
        else []
    )


def _predicate_matches(value: Any, operator: str, expected: Any) -> bool:
    """Evaluate one closed assertion operator without coercing values."""

    if operator == "is_null":
        return value is None
    if operator == "not_null":
        return value is not None
    if operator == "==":
        return type(value) is type(expected) and value == expected
    if operator == "!=":
        return type(value) is not type(expected) or value != expected
    if safe_number(value) is None or safe_number(expected) is None:
        return False
    operations: dict[str, Callable[[Any, Any], bool]] = {
        "<": lambda left, right: left < right,
        "<=": lambda left, right: left <= right,
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
    }
    return operations[operator](value, expected)


def check_contract_assertions(
    records: list[dict[str, Any]], assertions: list[dict[str, Any]]
) -> list[str]:
    """Evaluate closed label, range, and predicate assertions value-free."""

    failures: list[str] = []
    for record in records:
        for assertion in assertions:
            present, value = nested_value(record, assertion["field"])
            if not present:
                failures.append("assertion_field_missing")
                continue
            kind = assertion["kind"]
            if kind == "label_equals":
                if not isinstance(value, str) or value != assertion["expected_label"]:
                    failures.append("label_assertion_failed")
            elif kind == "score_range":
                number = safe_number(value)
                if (
                    number is None
                    or not assertion["minimum"] <= number <= assertion["maximum"]
                ):
                    failures.append("range_assertion_failed")
            elif not _predicate_matches(
                value, assertion["operator"], assertion.get("value")
            ):
                failures.append("predicate_assertion_failed")
    return failures


def check_contract_label_fields(
    records: list[dict[str, Any]], label_contracts: list[dict[str, Any]]
) -> list[str]:
    """Validate each label field against its own closed allowlist."""

    failures: list[str] = []
    for record in records:
        for contract in label_contracts:
            present, value = nested_value(record, contract["field"])
            if not present:
                failures.append("label_field_missing")
            elif not isinstance(value, str):
                failures.append("label_not_string")
            elif value not in contract["allowed_labels"]:
                failures.append("label_not_allowed")
    return failures


def check_semantic_response(
    endpoint_name: str,
    client: Any,
    payload: dict[str, Any],
    args: argparse.Namespace,
    output_contract: dict[str, Any] | None = None,
    fixture_binding: FixtureBinding | None = None,
) -> list[HealthCheck]:
    """Run one direct query and emit semantic assertions without raw output."""
    try:
        response = query_endpoint(endpoint_name, client, payload)
    except Exception as exc:
        return [failed_check("semantic_query", "Serving endpoint query failed", exc)]
    projection = project_raw_response(response)
    projection_ok = (
        projection.exact_record_count is not None
        and projection.exact_record_count > 0
        and not projection.failure_kinds
    )
    records = list(projection.records)
    checks = [
        HealthCheck(
            "semantic_query",
            projection_ok,
            "Endpoint query returned response records"
            if projection_ok
            else "Endpoint query response is empty, invalid, or exceeds semantic bounds",
            {"response_shape": response_report_projection(projection)},
        )
    ]
    if (
        fixture_binding is not None
        and fixture_binding.request_shape in {"dataframe_records", "dataframe_split"}
        and fixture_binding.response_cardinality == "one_output_per_input_row"
    ):
        observed_count = projection.exact_record_count
        cardinality_matches = (
            observed_count == fixture_binding.input_row_count
            and not projection.overflow
            and observed_count is not None
        )
        checks.append(
            HealthCheck(
                "response_cardinality",
                cardinality_matches,
                "Response cardinality matches the row-wise request contract"
                if cardinality_matches
                else "Response cardinality does not match the row-wise request contract",
                {
                    "request_shape": fixture_binding.request_shape,
                    "input_row_count": fixture_binding.input_row_count,
                    "response_record_count": observed_count,
                    "response_overflow": projection.overflow,
                    "policy": fixture_binding.response_cardinality,
                },
            )
        )
    if output_contract is None:
        required_fields = args.required_field
        nullable_fields = set(args.nullable_field)
        score_ranges = [parse_score_range(value) for value in args.score_range]
        label_fields = args.label_field
        forbidden_labels = normalized_labels(args.forbid_label)
        allowed_labels = normalized_labels(args.allowed_label)
        fallback_labels = normalized_labels(args.fallback_label)
        minimum_non_fallback_rate = args.expected_non_fallback_rate
        allow_identical_outputs = args.allow_identical_outputs
        assertions: list[dict[str, Any]] = []
    else:
        required_fields = output_contract["required_response_fields"]
        nullable_fields = set(output_contract["nullable_response_fields"])
        score_ranges = [
            (item["field"], item["minimum"], item["maximum"])
            for item in output_contract["score_fields"]
        ]
        label_fields = [item["field"] for item in output_contract["label_fields"]]
        allowed_labels = {
            label.casefold()
            for item in output_contract["label_fields"]
            for label in item["allowed_labels"]
        }
        fallback_labels = {
            label.casefold()
            for item in output_contract["label_fields"]
            for label in item["fallback_labels"]
        }
        forbidden_labels = set()
        minimum_non_fallback_rate = output_contract["minimum_non_fallback_rate"]
        allow_identical_outputs = output_contract["allow_identical_outputs"]
        assertions = output_contract["semantic_assertions"]
    if minimum_non_fallback_rate is not None and not fallback_labels:
        fallback_labels = {label.casefold() for label in DEFAULT_FALLBACK_LABELS}
    failures = []
    if projection.failure_kinds:
        failures.append("response_projection_invalid")
        if projection.overflow:
            failures.append("response_projection_overflow")
    elif not records:
        failures.append("response_records_empty")
    else:
        failures.extend(
            check_required_fields(records, required_fields, nullable_fields)
        )
        if output_contract is not None:
            failures.extend(
                check_response_schema(records, output_contract["response_schema"])
            )
        failures.extend(check_score_ranges(records, score_ranges))
        if output_contract is None:
            failures.extend(
                check_label_rules(
                    records, label_fields, allowed_labels, forbidden_labels
                )
            )
        else:
            failures.extend(
                check_contract_label_fields(records, output_contract["label_fields"])
            )
        failures.extend(check_contract_assertions(records, assertions))
        failures.extend(check_identical_outputs(records, allow_identical_outputs))
    observed_non_fallback_rate = None
    if minimum_non_fallback_rate is not None and records:
        observed_non_fallback_rate = non_fallback_rate(
            records, label_fields, fallback_labels
        )
        if observed_non_fallback_rate < minimum_non_fallback_rate:
            failures.append("observed non-fallback rate is below the declared minimum")
    checks.append(
        HealthCheck(
            "semantic_assertions",
            not failures,
            "Semantic assertions passed"
            if not failures
            else "Semantic assertions failed",
            {
                "failure_count": len(failures),
                "failure_kinds": sorted(set(failures)),
                "required_field_count": len(required_fields),
                "required_field_tokens": sorted(
                    field_token(item) for item in required_fields
                ),
                "response_schema_count": len(
                    output_contract["response_schema"]
                    if output_contract is not None
                    else []
                ),
                "nullable_field_count": len(nullable_fields),
                "score_range_count": len(score_ranges),
                "label_field_count": len(label_fields),
                "assertion_count": len(assertions),
                "allowed_label_count": len(allowed_labels),
                "forbidden_label_count": len(forbidden_labels),
                "fallback_label_count": len(fallback_labels),
                "expected_non_fallback_rate": minimum_non_fallback_rate,
                "observed_non_fallback_rate": observed_non_fallback_rate,
            },
        )
    )
    return checks


def run_health_checks(
    endpoint: Any,
    contract: Mapping[str, Any],
    wait_check: HealthCheck | None = None,
) -> list[HealthCheck]:
    """Run every rollout check against one post-wait endpoint snapshot."""

    checks = [wait_check] if wait_check is not None else []
    checks.append(check_endpoint_snapshot(endpoint, contract))
    checks.append(check_route_transport(contract))
    return checks


def check_to_dict(check: HealthCheck) -> dict[str, Any]:
    """Convert a check to a JSON-safe report record."""
    return {
        "name": safe_identifier(check.name),
        "passed": bool(check.passed),
        "message": safe_text(check.message),
        "details": object_to_dict(check.details) if check.details else None,
        "warning": bool(check.warning),
    }


def event_check(events: dict[str, Any], require_events: bool) -> HealthCheck:
    """Turn optional events into a warning or a required failure."""
    if events.get("available"):
        return HealthCheck(
            "endpoint_events",
            True,
            "Endpoint events collected",
            {
                "event_count": events.get("event_count", 0),
                "truncated": bool(events.get("truncated", False)),
            },
        )
    return HealthCheck(
        "endpoint_events",
        not require_events,
        "Endpoint events are unavailable",
        {"required": require_events, "error": events.get("error")},
        warning=not require_events,
    )


def build_report(
    endpoint_name: str,
    profile: str | None,
    host: str | None,
    endpoint: Any | None,
    checks: list[HealthCheck],
    events: dict[str, Any],
) -> dict[str, Any]:
    """Build schema-v4 diagnostics without response or fixture content."""
    warnings = [check.message for check in checks if check.warning]
    passed = all(check.passed for check in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": safe_text(profile) if profile else None,
        "workspace_host": safe_text(host) if host else None,
        "endpoint_name": safe_text(endpoint_name),
        "endpoint": endpoint_summary(endpoint, profile, host)
        if endpoint is not None
        else None,
        "events": object_to_dict(events),
        "checks": [check_to_dict(check) for check in checks],
        "warnings": [safe_text(warning) for warning in warnings],
        "passed": passed,
        "complete": passed and not warnings,
    }


def print_report(report: dict[str, Any]) -> bool:
    """Print a readable safe report and return required-check status."""
    print("\n" + "=" * 72)
    print("DATABRICKS SERVING ENDPOINT DOCTOR")
    print("=" * 72)
    print(f"Endpoint: {report['endpoint_name']}")
    print(f"Profile: {report.get('profile') or 'not supplied'}")
    print(f"Workspace host: {report.get('workspace_host') or 'unknown'}")
    endpoint = report.get("endpoint") or {}
    if endpoint:
        print(f"State: {endpoint.get('state')}")
        print(f"Served entity count: {len(endpoint.get('served_entities') or [])}")
        print(f"Traffic route count: {len(endpoint.get('traffic_routes') or [])}")
    events = report.get("events") or {}
    if events.get("available"):
        print(f"Recent events captured: {events.get('event_count', 0)}")
    else:
        print("Endpoint events unavailable")
    for check in report["checks"]:
        status = (
            "WARNING" if check["warning"] else "PASS" if check["passed"] else "FAIL"
        )
        print(f"\n{status}: {check['name']}")
        print(f"  {check['message']}")
    print("\n" + "=" * 72)
    if not report["passed"]:
        overall = "SOME REQUIRED CHECKS FAILED"
    elif report["warnings"]:
        overall = "CHECKS PASSED WITH WARNINGS"
    else:
        overall = "ALL CHECKS PASSED"
    print(f"OVERALL: {overall}")
    print("=" * 72 + "\n")
    return bool(report["passed"])


def _positive_int(value: int | None, name: str, maximum: int) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{name} must be a positive integer no greater than {maximum}")


def normalize_https_host(value: str) -> str:
    """Validate an expected workspace host without accepting credentials or paths."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "--workspace-host must be an https workspace origin without credentials or a path"
        )
    if parsed.path.rstrip("/"):
        raise ValueError("--workspace-host must not include a path")
    return f"https://{parsed.netloc}".rstrip("/")


def normalize_endpoint_target(value: str) -> str:
    """Normalize an SDK endpoint URL that may omit the HTTPS scheme."""

    candidate = value if "://" in value else f"https://{value}"
    parsed = urlsplit(candidate)
    decoded_path = unquote(parsed.path)
    path_parts = decoded_path.split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "\\" in decoded_path
        or "\x00" in decoded_path
        or any(part in {"", ".", ".."} for part in path_parts[1:])
    ):
        raise ValueError("endpoint target is not a safe HTTPS URL")
    return candidate.rstrip("/")


def validate_args(
    args: argparse.Namespace, has_fixture: bool, contract: Mapping[str, Any]
) -> None:
    """Validate every numeric and target contract before endpoint operations."""
    _positive_int(args.wait_ready, "--wait-ready", MAX_WAIT_READY_SECONDS)
    _positive_int(args.poll_interval, "--poll-interval", MAX_POLL_INTERVAL_SECONDS)
    _positive_int(args.event_limit, "--event-limit", MAX_OBJECT_ITEMS)
    _positive_int(
        args.http_timeout_seconds, "--http-timeout-seconds", MAX_HTTP_TIMEOUT_SECONDS
    )
    _positive_int(
        args.retry_timeout_seconds, "--retry-timeout-seconds", MAX_RETRY_TIMEOUT_SECONDS
    )
    if args.retry_timeout_seconds > args.http_timeout_seconds:
        raise ValueError(
            "--retry-timeout-seconds must not exceed --http-timeout-seconds"
        )
    for score_range in getattr(args, "score_range", []):
        parse_score_range(score_range)
    expected_non_fallback_rate = getattr(args, "expected_non_fallback_rate", None)
    if expected_non_fallback_rate is not None:
        rate = expected_non_fallback_rate
        if isinstance(rate, bool) or not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
            raise ValueError(
                "--expected-non-fallback-rate must be a finite value from 0 through 1"
            )
        if not getattr(args, "label_field", []):
            raise ValueError(
                "--expected-non-fallback-rate requires at least one --label-field"
            )
    nondefault_profile = (
        bool(args.profile) and args.profile.strip().casefold() != "default"
    )
    if has_fixture and not nondefault_profile and not args.workspace_host:
        raise ValueError(
            "A fixture requires a non-default --profile or an exact --workspace-host manifest before query"
        )
    if args.workspace_host:
        args.workspace_host = normalize_https_host(args.workspace_host)
    endpoint_contract = contract["endpoint"]
    manifest = contract["target_manifest"]
    if args.endpoint_name != endpoint_contract["name"]:
        raise ValueError("endpoint argument does not match the rollout contract")
    if args.version is not None and str(args.version) != str(
        contract["model_handoff"]["model_version"]
    ):
        raise ValueError("version argument does not match the rollout contract")
    if args.profile is not None and args.profile != manifest["profile"]:
        raise ValueError("profile argument does not match the rollout contract")
    if args.workspace_host is not None and args.workspace_host != normalize_https_host(
        endpoint_contract["workspace_host"]
    ):
        raise ValueError("workspace host argument does not match the rollout contract")
    legacy_assertions = (
        getattr(args, "required_field", [])
        or getattr(args, "nullable_field", [])
        or getattr(args, "score_range", [])
        or getattr(args, "label_field", [])
        or getattr(args, "allowed_label", [])
        or getattr(args, "fallback_label", [])
        or getattr(args, "forbid_label", [])
        or expected_non_fallback_rate is not None
        or getattr(args, "allow_identical_outputs", False)
    )
    if legacy_assertions:
        raise ValueError(
            "semantic assertions must come from the closed rollout contract"
        )


def load_contract(path: str | Path) -> dict[str, Any]:
    """Load the closed local contract without importing Databricks dependencies."""

    try:
        from jsonschema.exceptions import SchemaError
        from validate_endpoint_config import load_validated_contract
    except ImportError as exc:
        raise ValueError("endpoint contract validator is unavailable") from exc
    try:
        return dict(load_validated_contract(Path(path)))
    except (OSError, UnicodeError, ValueError, SchemaError) as exc:
        raise ValueError("endpoint contract could not be validated") from exc


def build_client(args: argparse.Namespace) -> Any:
    """Create an SDK client with explicit request and retry timeouts."""
    from databricks.sdk import WorkspaceClient

    kwargs: dict[str, Any] = {
        "http_timeout_seconds": args.http_timeout_seconds,
        "retry_timeout_seconds": args.retry_timeout_seconds,
    }
    if args.profile:
        kwargs["profile"] = args.profile
    return WorkspaceClient(**kwargs)


def configured_host(client: Any) -> str | None:
    """Read the configured host as a bounded string for manifest comparison."""
    host = getattr(getattr(client, "config", None), "host", None)
    return host if isinstance(host, str) else None


def validate_workspace_target(
    args: argparse.Namespace,
    actual_host: str | None,
    contract: Mapping[str, Any],
) -> None:
    """Reject any SDK client whose configured host differs from the contract."""

    expected = normalize_https_host(contract["endpoint"]["workspace_host"])
    if actual_host is None or normalize_https_host(actual_host) != expected:
        raise ValueError(
            "configured workspace host does not match the rollout contract"
        )
    if args.workspace_host and args.workspace_host != expected:
        raise ValueError("workspace-host argument does not match the rollout contract")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without importing Databricks dependencies."""
    parser = argparse.ArgumentParser(
        description="Check model serving endpoint health and semantic readiness"
    )
    parser.add_argument("endpoint_name", help="Name of the serving endpoint")
    parser.add_argument(
        "--contract",
        required=True,
        help="Closed endpoint rollout contract validated before any SDK call",
    )
    parser.add_argument("--version", help="Expected model version to verify")
    parser.add_argument("--profile", help="Explicit Databricks CLI profile")
    parser.add_argument(
        "--workspace-host",
        help="Exact https workspace origin required for a fixture without a non-default --profile",
    )
    parser.add_argument(
        "--http-timeout-seconds", type=int, default=DEFAULT_HTTP_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--retry-timeout-seconds", type=int, default=DEFAULT_RETRY_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--json", action="store_true", help="Output a structured, redacted JSON report"
    )
    parser.add_argument(
        "--sample-json",
        help="Bounded JSON request fixture; its content is never reported",
    )
    parser.add_argument(
        "--sample-jsonl",
        help="Bounded JSONL records fixture; its content is never reported",
    )
    parser.add_argument(
        "--wait-ready",
        nargs="?",
        const=DEFAULT_WAIT_READY_SECONDS,
        type=int,
        help="Poll until READY for a positive bounded number of seconds",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS
    )
    parser.add_argument("--event-limit", type=int, default=DEFAULT_EVENT_LIMIT)
    parser.add_argument(
        "--require-events",
        action="store_true",
        help="Make unavailable endpoint events a required failure",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the endpoint doctor: 0 pass, 1 required failure, 2 invalid arguments."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    has_fixture = bool(args.sample_json or args.sample_jsonl)
    try:
        contract = load_contract(args.contract)
        validate_args(args, has_fixture, contract)
        sample_payload, _sample_count = load_sample_payload(
            args.sample_json, args.sample_jsonl
        )
        fixture_binding = (
            bind_fixture_payload(sample_payload, contract)
            if sample_payload is not None
            else None
        )
    except SampleInputError:
        logger.error("Sample fixture is invalid or exceeds the doctor limits")
        return 2
    except ValueError:
        logger.error("Endpoint doctor arguments are invalid")
        return 2
    except OSError:
        logger.error("Sample fixture could not be read")
        return 1
    try:
        client = build_client(args)
    except ImportError:
        logger.error("databricks-sdk is not installed")
        return 1
    except Exception as exc:
        logger.error(
            "Databricks client could not be initialized: %s",
            exception_metadata(exc)["type"],
        )
        return 1
    host = configured_host(client)
    try:
        validate_workspace_target(args, host, contract)
    except ValueError:
        logger.error("Workspace target manifest is invalid")
        return 2
    wait_check = None
    if args.wait_ready is not None:
        wait_check = wait_for_ready(
            args.endpoint_name,
            client,
            args.wait_ready,
            args.poll_interval,
        )
    try:
        endpoint = get_endpoint(args.endpoint_name, client)
    except Exception as exc:
        endpoint = None
        checks = [wait_check] if wait_check is not None else []
        checks.append(
            failed_check(
                "expected_manifest",
                "Post-wait endpoint snapshot could not be retrieved",
                exc,
            )
        )
    else:
        checks = run_health_checks(endpoint, contract, wait_check)
    if sample_payload is not None:
        prequery_checks = [
            check
            for check in checks
            if check.name in {"expected_manifest", "route_transport"}
        ]
        if len(prequery_checks) == 2 and all(check.passed for check in prequery_checks):
            try:
                checks.extend(
                    check_semantic_response(
                        args.endpoint_name,
                        client,
                        sample_payload,
                        args,
                        contract["output_contract"],
                        fixture_binding,
                    )
                )
            except ValueError:
                checks.append(
                    HealthCheck(
                        "semantic_assertions",
                        False,
                        "Semantic assertion contract is invalid",
                    )
                )
        else:
            checks.append(
                HealthCheck(
                    "semantic_query",
                    False,
                    "Semantic query blocked because rollout transport was not proven",
                )
            )
    events = list_endpoint_events(client, args.endpoint_name, args.event_limit)
    checks.append(event_check(events, args.require_events))
    report = build_report(
        args.endpoint_name, args.profile, host, endpoint, checks, events
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
