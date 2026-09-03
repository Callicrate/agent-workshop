#!/usr/bin/env python3
"""Validate a Databricks serving rollout contract without echoing its values."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from logical_types import matches_logical_type


MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_ISSUES = 40
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "assets" / "endpoint-config-schema.json"
COMPARISON_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}
NULL_OPERATORS = {"is_null", "not_null"}
ORDERING_OPERATORS = {"<", "<=", ">", ">="}
NUMERIC_LOGICAL_TYPES = {"integer", "long", "float", "double", "decimal"}
REQUIRED_CREATOR_GRANTS = {"USE_CATALOG", "USE_SCHEMA", "EXECUTE_MODEL"}
ROUTE_OPTIMIZED_SUFFIXES = (
    ".serving.cloud.databricks.com",
    ".serving.gcp.databricks.com",
    ".serving.azuredatabricks.net",
)
DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|//|\\\\)")


def _reject_non_finite_json(_: str) -> Any:
    raise ValueError("non-finite JSON number")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def issue_path(parts: Sequence[Any]) -> str:
    """Return a bounded structural path, never a configuration value."""

    return ".".join(str(part) for part in parts) or "<root>"


def _https_url(value: Any, *, origin_only: bool) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    if origin_only and parsed.path.rstrip("/"):
        return False
    decoded_path = unquote(parsed.path)
    path_parts = decoded_path.split("/")
    if any(part in {".", ".."} for part in path_parts):
        return False
    if not origin_only and any(not part for part in path_parts[1:]):
        return False
    return "\\" not in decoded_path and "\x00" not in decoded_path


def _normalized_relative_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or DRIVE_OR_UNC.match(value)
    ):
        return False
    parts = value.split("/")
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _names(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    return [item.get("name") for item in items if isinstance(item, Mapping)]


def _value_matches_logical_type(value: Any, logical_type: str) -> bool:
    return matches_logical_type(value, logical_type)


def _duplicate_name_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    collections = (
        (
            config.get("deployment", {}).get("served_entities"),
            "deployment.served_entities",
        ),
        (config.get("deployment", {}).get("routes"), "deployment.routes"),
        (
            config.get("model_handoff", {}).get("signature", {}).get("inputs"),
            "model_handoff.signature.inputs",
        ),
        (
            config.get("model_handoff", {}).get("signature", {}).get("outputs"),
            "model_handoff.signature.outputs",
        ),
        (
            config.get("model_handoff", {}).get("feature_schema", {}).get("features"),
            "model_handoff.feature_schema.features",
        ),
        (
            config.get("model_handoff", {})
            .get("threshold_artifact", {})
            .get("decisions"),
            "model_handoff.threshold_artifact.decisions",
        ),
    )
    for items, path in collections:
        names = _names(items)
        if len(names) != len(set(names)):
            issues.append(("duplicate_name", path))
    return issues


def _url_and_target_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    endpoint = config["endpoint"]
    manifest = config["target_manifest"]
    issues: list[tuple[str, str]] = []
    for path, value, origin_only in (
        ("endpoint.workspace_host", endpoint["workspace_host"], True),
        ("endpoint.target_url", endpoint["target_url"], False),
        ("target_manifest.workspace_host", manifest["workspace_host"], True),
        ("target_manifest.target_url", manifest["target_url"], False),
    ):
        if not _https_url(value, origin_only=origin_only):
            issues.append(("invalid_https_url", path))
    for field in ("workspace_host", "target_url"):
        if endpoint[field].rstrip("/") != manifest[field].rstrip("/"):
            issues.append(("target_manifest_mismatch", f"target_manifest.{field}"))
    if endpoint["name"] != manifest["endpoint_name"]:
        issues.append(("target_manifest_mismatch", "target_manifest.endpoint_name"))
    expected_suffix = f"/serving-endpoints/{endpoint['name']}/invocations"
    if not urlsplit(endpoint["target_url"]).path.rstrip("/").endswith(expected_suffix):
        issues.append(("target_url_endpoint_mismatch", "endpoint.target_url"))
    target_host = (urlsplit(endpoint["target_url"]).hostname or "").casefold()
    workspace_host = (urlsplit(endpoint["workspace_host"]).hostname or "").casefold()
    if endpoint["route_optimized"]:
        if target_host == workspace_host or not target_host.endswith(
            ROUTE_OPTIMIZED_SUFFIXES
        ):
            issues.append(("invalid_route_optimized_target", "endpoint.target_url"))
    elif target_host != workspace_host:
        issues.append(("workspace_target_host_mismatch", "endpoint.target_url"))
    return issues


def _handoff_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    handoff = config["model_handoff"]
    manifest = config["target_manifest"]
    deployment = config["deployment"]
    issues: list[tuple[str, str]] = []
    model_full_name = (
        f"{handoff['catalog']}.{handoff['schema']}.{handoff['model_name']}"
    )
    if model_full_name != manifest["model_full_name"]:
        issues.append(("model_handoff_mismatch", "target_manifest.model_full_name"))
    if handoff["served_entity_name"] != manifest["served_entity_name"]:
        issues.append(("model_handoff_mismatch", "target_manifest.served_entity_name"))
    entities = deployment["served_entities"]
    target_entities = [
        item for item in entities if item["name"] == handoff["served_entity_name"]
    ]
    if len(target_entities) != 1:
        issues.append(("model_handoff_entity_missing", "deployment.served_entities"))
    else:
        target = target_entities[0]
        if target["entity_name"] != model_full_name or str(
            target["entity_version"]
        ) != str(handoff["model_version"]):
            issues.append(
                ("model_handoff_entity_mismatch", "deployment.served_entities")
            )

    signature_input_fields = handoff["signature"]["inputs"]
    feature_fields = handoff["feature_schema"]["features"]
    feature_names = [item["name"] for item in feature_fields]
    example_columns = handoff["input_example"]["columns"]
    if signature_input_fields != feature_fields:
        issues.append(
            ("signature_feature_schema_mismatch", "model_handoff.signature.inputs")
        )
    if signature_input_fields != config["request_contract"]["input_schema"]:
        issues.append(
            (
                "request_signature_contract_mismatch",
                "request_contract.input_schema",
            )
        )
    if example_columns != feature_names:
        issues.append(
            (
                "input_example_feature_schema_mismatch",
                "model_handoff.input_example.columns",
            )
        )
    return issues


def _deployment_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    deployment = config["deployment"]
    routes = deployment["routes"]
    entity_names = {item["name"] for item in deployment["served_entities"]}
    issues: list[tuple[str, str]] = []
    for index, entity in enumerate(deployment["served_entities"]):
        path = f"deployment.served_entities.{index}"
        mode = entity["scaling_mode"]
        if (
            mode == "provisioned_concurrency"
            and entity["min_provisioned_concurrency"]
            > entity["max_provisioned_concurrency"]
        ):
            issues.append(("scaling_range_not_ordered", path))
        if (
            mode == "provisioned_throughput"
            and entity["min_provisioned_throughput"]
            > entity["max_provisioned_throughput"]
        ):
            issues.append(("scaling_range_not_ordered", path))
    if sum(route["traffic_percentage"] for route in routes) != 100:
        issues.append(("traffic_total_not_100", "deployment.routes"))
    for index, route in enumerate(routes):
        path = f"deployment.routes.{index}"
        if route["name"] != route["served_entity_name"]:
            issues.append(("route_name_entity_mismatch", path))
        if route["served_entity_name"] not in entity_names:
            issues.append(("route_entity_missing", path))
        if route["traffic_percentage"] == 0:
            if not route["allow_zero_traffic"] or route["role"] != "fallback":
                issues.append(("zero_traffic_not_explicit_fallback", path))
        elif route["allow_zero_traffic"]:
            issues.append(("zero_traffic_flag_inconsistent", path))
    target_name = config["model_handoff"]["served_entity_name"]
    target_routes = [
        route for route in routes if route["served_entity_name"] == target_name
    ]
    if not target_routes:
        issues.append(("target_entity_unrouted", "deployment.routes"))
    elif not any(
        route["traffic_percentage"] > 0 for route in target_routes
    ) and not all(
        route["allow_zero_traffic"] and route["role"] == "fallback"
        for route in target_routes
    ):
        issues.append(("target_entity_zero_traffic", "deployment.routes"))
    return issues


def _output_contract_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    output = config["output_contract"]
    handoff = config["model_handoff"]
    issues: list[tuple[str, str]] = []
    required_fields = set(output["required_response_fields"])
    nullable_fields = set(output["nullable_response_fields"])
    if not nullable_fields.issubset(required_fields):
        issues.append(
            ("nullable_field_not_required", "output_contract.nullable_response_fields")
        )
    signature_output_fields = handoff["signature"]["outputs"]
    response_fields = output["response_schema"]
    signature_outputs = {item["name"]: item for item in signature_output_fields}
    response_outputs = {item["name"]: item for item in response_fields}
    if signature_output_fields != response_fields:
        issues.append(
            (
                "response_signature_declaration_mismatch",
                "output_contract.response_schema",
            )
        )
    if required_fields != set(response_outputs):
        issues.append(
            (
                "response_signature_contract_mismatch",
                "output_contract.required_response_fields",
            )
        )
    response_nullable = {
        name
        for name, declaration in response_outputs.items()
        if declaration["nullable"]
    }
    if nullable_fields != response_nullable:
        issues.append(
            (
                "response_nullability_mismatch",
                "output_contract.nullable_response_fields",
            )
        )

    score_ranges: dict[str, tuple[float, float]] = {}
    score_field_names = [item["field"] for item in output["score_fields"]]
    if len(score_field_names) != len(set(score_field_names)):
        issues.append(("duplicate_score_field", "output_contract.score_fields"))
    for index, score in enumerate(output["score_fields"]):
        minimum = score["minimum"]
        maximum = score["maximum"]
        if (
            not math.isfinite(minimum)
            or not math.isfinite(maximum)
            or minimum > maximum
        ):
            issues.append(
                ("invalid_score_range", f"output_contract.score_fields.{index}")
            )
        score_ranges[score["field"]] = (minimum, maximum)
        if score["field"] not in required_fields:
            issues.append(
                ("score_field_not_required", f"output_contract.score_fields.{index}")
            )
        declarations = (
            signature_outputs.get(score["field"]),
            response_outputs.get(score["field"]),
        )
        if any(
            declaration is not None
            and (
                declaration["logical_type"] not in NUMERIC_LOGICAL_TYPES
                or declaration["nullable"]
            )
            for declaration in declarations
        ):
            issues.append(
                (
                    "score_signature_incompatible",
                    f"output_contract.score_fields.{index}",
                )
            )

    label_fields = {item["field"]: item for item in output["label_fields"]}
    if len(label_fields) != len(output["label_fields"]):
        issues.append(("duplicate_label_field", "output_contract.label_fields"))
    if output["minimum_non_fallback_rate"] > 0 and not label_fields:
        issues.append(
            (
                "non_fallback_rate_requires_label",
                "output_contract.minimum_non_fallback_rate",
            )
        )
    for index, label in enumerate(output["label_fields"]):
        if label["field"] not in required_fields:
            issues.append(
                ("label_field_not_required", f"output_contract.label_fields.{index}")
            )
        declarations = (
            signature_outputs.get(label["field"]),
            response_outputs.get(label["field"]),
        )
        if any(
            declaration is not None
            and (declaration["logical_type"] != "string" or declaration["nullable"])
            for declaration in declarations
        ):
            issues.append(
                (
                    "label_signature_incompatible",
                    f"output_contract.label_fields.{index}",
                )
            )
        if set(label["fallback_labels"]) - set(label["allowed_labels"]):
            issues.append(
                ("fallback_label_not_allowed", f"output_contract.label_fields.{index}")
            )

    for index, assertion in enumerate(output["semantic_assertions"]):
        path = f"output_contract.semantic_assertions.{index}"
        field = assertion["field"]
        declaration = response_outputs.get(field) or signature_outputs.get(field)
        if field not in required_fields:
            issues.append(("assertion_field_not_required", path))
        if assertion["kind"] == "score_range":
            if field not in score_ranges:
                issues.append(("assertion_score_undeclared", path))
            if assertion["minimum"] > assertion["maximum"]:
                issues.append(("invalid_assertion_range", path))
            if declaration is not None and (
                declaration["logical_type"] not in NUMERIC_LOGICAL_TYPES
                or declaration["nullable"]
            ):
                issues.append(("assertion_type_incompatible", path))
        elif assertion["kind"] == "label_equals":
            label_contract = label_fields.get(field)
            if (
                label_contract is None
                or assertion["expected_label"] not in label_contract["allowed_labels"]
            ):
                issues.append(("assertion_label_undeclared", path))
            if declaration is not None and (
                declaration["logical_type"] != "string" or declaration["nullable"]
            ):
                issues.append(("assertion_type_incompatible", path))
        else:
            operator = assertion["operator"]
            has_value = "value" in assertion
            if operator in COMPARISON_OPERATORS and not has_value:
                issues.append(("predicate_value_required", path))
            if operator in NULL_OPERATORS and has_value:
                issues.append(("predicate_value_forbidden", path))
            if declaration is not None:
                if operator == "is_null" and not declaration["nullable"]:
                    issues.append(("predicate_nullability_incompatible", path))
                if operator in ORDERING_OPERATORS and (
                    declaration["logical_type"] not in NUMERIC_LOGICAL_TYPES
                    or not has_value
                    or not _value_matches_logical_type(
                        assertion.get("value"), declaration["logical_type"]
                    )
                ):
                    issues.append(("predicate_type_incompatible", path))
                if (
                    operator in {"==", "!="}
                    and has_value
                    and not (assertion["value"] is None and declaration["nullable"])
                    and not _value_matches_logical_type(
                        assertion["value"], declaration["logical_type"]
                    )
                ):
                    issues.append(("predicate_type_incompatible", path))

    for index, decision in enumerate(handoff["threshold_artifact"]["decisions"]):
        score_range = score_ranges.get(decision["field"])
        if score_range is None:
            issues.append(
                (
                    "threshold_score_undeclared",
                    f"model_handoff.threshold_artifact.decisions.{index}",
                )
            )
        elif not score_range[0] <= decision["value"] <= score_range[1]:
            issues.append(
                (
                    "threshold_outside_score_range",
                    f"model_handoff.threshold_artifact.decisions.{index}",
                )
            )
    return issues


def _telemetry_auth_recovery_issues(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    telemetry = config["telemetry"]
    if telemetry["required"] and (
        "TELEMETRY_FEATURE_INFERENCE_TABLE"
        not in telemetry["enabled_telemetry_features"]
    ):
        issues.append(
            (
                "required_inference_telemetry_missing",
                "telemetry.enabled_telemetry_features",
            )
        )
    if (
        telemetry["required"]
        and telemetry["inference_table_config"]["sampling_fraction"] == 0
    ):
        issues.append(
            (
                "required_telemetry_zero_sampling",
                "telemetry.inference_table_config.sampling_fraction",
            )
        )

    endpoint = config["endpoint"]
    auth = config["production_auth"]
    if auth["immutable_creator_identity"] != endpoint["creator_principal"]:
        issues.append(
            ("creator_identity_mismatch", "production_auth.immutable_creator_identity")
        )
    if not REQUIRED_CREATOR_GRANTS.issubset(auth["creator_uc_grants"]):
        issues.append(
            ("creator_uc_grants_incomplete", "production_auth.creator_uc_grants")
        )
    route_transport = auth.get("route_optimized_transport")
    if endpoint["route_optimized"]:
        target_host = urlsplit(endpoint["target_url"]).hostname or ""
        derived_route_id = target_host.split(".", 1)[0]
        if not isinstance(route_transport, Mapping):
            issues.append(
                (
                    "route_transport_required",
                    "production_auth.route_optimized_transport",
                )
            )
        else:
            authorization = route_transport["authorization_details"]
            if route_transport["route_id"] != derived_route_id:
                issues.append(
                    (
                        "route_id_target_mismatch",
                        "production_auth.route_optimized_transport.route_id",
                    )
                )
            if authorization["object_path"] != f"/serving-endpoints/{derived_route_id}":
                issues.append(
                    (
                        "authorization_details_target_mismatch",
                        "production_auth.route_optimized_transport.authorization_details.object_path",
                    )
                )
    elif route_transport is not None:
        issues.append(
            (
                "route_transport_forbidden",
                "production_auth.route_optimized_transport",
            )
        )
    if (
        config["ownership_mode"] == "dab_managed"
        and "dab_target" not in config["target_manifest"]
    ):
        issues.append(("dab_target_required", "target_manifest.dab_target"))

    recovery = config["recovery"]
    for field, value in (
        ("manifest_path", recovery["manifest_path"]),
        (
            "reverse_operation.artifact_path",
            recovery["reverse_operation"]["artifact_path"],
        ),
    ):
        if not _normalized_relative_path(value):
            issues.append(("invalid_recovery_path", f"recovery.{field}"))
    return issues


def validate_config(
    config: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """Return stable, value-free schema and semantic contract failures."""

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    schema_issues = [
        (f"schema_{error.validator}", issue_path(error.absolute_path))
        for error in validator.iter_errors(config)
    ]
    if schema_issues:
        return sorted(set(schema_issues))
    issues: list[tuple[str, str]] = []
    issues.extend(_duplicate_name_issues(config))
    issues.extend(_url_and_target_issues(config))
    issues.extend(_handoff_issues(config))
    issues.extend(_deployment_issues(config))
    issues.extend(_output_contract_issues(config))
    issues.extend(_telemetry_auth_recovery_issues(config))
    return sorted(set(issues))


def read_json(path: Path) -> Mapping[str, Any]:
    """Read one bounded strict JSON object without exposing values."""

    if path.suffix.casefold() != ".json":
        raise ValueError("invalid JSON contract path")
    with path.open("rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            raise ValueError("invalid JSON contract file")
        raw = handle.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("JSON contract exceeds the byte limit")
    value = json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_non_finite_json,
    )
    if not isinstance(value, Mapping):
        raise ValueError("contract root must be an object")
    return value


def load_validated_contract(path: Path) -> Mapping[str, Any]:
    """Load and validate one endpoint contract or raise a value-free error."""

    schema = read_json(SCHEMA_PATH)
    contract = read_json(path)
    issues = validate_config(contract, schema)
    if issues:
        raise ValueError("endpoint contract is invalid")
    return contract


def main(argv: Sequence[str] | None = None) -> int:
    """Validate one contract: 0 valid, 1 invalid contract, 2 read/parse, 3 validator."""

    parser = argparse.ArgumentParser(
        description="Validate a Databricks serving endpoint contract without printing values."
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="Endpoint contract JSON"
    )
    args = parser.parse_args(argv)
    try:
        schema = read_json(SCHEMA_PATH)
        contract = read_json(args.config)
        issues = validate_config(contract, schema)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print("INVALID config_read_or_parse", file=sys.stderr)
        return 2
    except SchemaError:
        print("INVALID validator_internal_error", file=sys.stderr)
        return 3
    if issues:
        for code, path in issues[:MAX_ISSUES]:
            print(f"INVALID {code} {path}", file=sys.stderr)
        return 1
    print("VALID endpoint_config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
