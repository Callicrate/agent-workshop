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
INTEGER_LOGICAL_TYPES = {"integer", "long"}
REQUIRED_CREATOR_GRANTS = {"USE_CATALOG", "USE_SCHEMA", "EXECUTE_MODEL"}
ROUTE_OPTIMIZED_SUFFIXES = (
    ".serving.cloud.databricks.com",
    ".serving.gcp.databricks.com",
    ".serving.azuredatabricks.net",
)
DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|//|\\\\)")


class UnsupportedContractVersionError(ValueError):
    """The bounded contract is a known unsupported rollout version."""


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


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return isinstance(value, int) or math.isfinite(value)


def _predicate_interval(
    minimum: int | float,
    maximum: int | float,
    operator: str,
    value: int | float,
) -> tuple[int | float, bool, int | float, bool] | None:
    """Return the accepted numeric interval before domain discreteness checks."""

    if not all(_is_finite_number(item) for item in (minimum, maximum, value)):
        return None
    lower: int | float = minimum
    upper: int | float = maximum
    lower_inclusive = True
    upper_inclusive = True
    if operator == "<":
        if value < upper:
            upper = value
            upper_inclusive = False
        elif value == upper:
            upper_inclusive = False
    elif operator == "<=":
        if value < upper:
            upper = value
            upper_inclusive = True
    elif operator == ">":
        if value > lower:
            lower = value
            lower_inclusive = False
        elif value == lower:
            lower_inclusive = False
    elif operator == ">=":
        if value > lower:
            lower = value
            lower_inclusive = True
    elif operator == "==":
        lower = value
        upper = value
    else:
        return None
    return lower, lower_inclusive, upper, upper_inclusive


def _interval_contains(
    interval: tuple[int | float, bool, int | float, bool], value: int | float
) -> bool:
    lower, lower_inclusive, upper, upper_inclusive = interval
    return (value > lower or (lower_inclusive and value == lower)) and (
        value < upper or (upper_inclusive and value == upper)
    )


def _interval_has_domain_value(
    interval: tuple[int | float, bool, int | float, bool], logical_type: str
) -> bool:
    """Prove that one JSON value in the declared logical domain is accepted."""

    lower, lower_inclusive, upper, upper_inclusive = interval
    if lower > upper or (
        lower == upper and not (lower_inclusive and upper_inclusive)
    ):
        return False

    integer_candidate = math.ceil(lower)
    if integer_candidate == lower and not lower_inclusive:
        integer_candidate += 1
    if _interval_contains(interval, integer_candidate) and _value_matches_logical_type(
        integer_candidate, logical_type
    ):
        return True
    if logical_type in INTEGER_LOGICAL_TYPES:
        return False

    candidates: list[int | float] = []
    if lower_inclusive:
        candidates.append(lower)
    if upper_inclusive:
        candidates.append(upper)
    for boundary, direction in ((lower, math.inf), (upper, -math.inf)):
        try:
            converted = float(boundary)
        except OverflowError:
            continue
        candidates.extend((converted, math.nextafter(converted, direction)))
    for candidate in candidates:
        if (
            _interval_contains(interval, candidate)
            and _value_matches_logical_type(candidate, logical_type)
        ):
            return True
    return False


def _interval_has_accepted_value(
    interval: tuple[int | float, bool, int | float, bool],
    logical_type: str,
    excluded_points: list[int | float] | None = None,
) -> bool:
    """Prove a witness after removing exact finite numeric exclusions."""

    if not excluded_points:
        return _interval_has_domain_value(interval, logical_type)
    relevant_points = sorted(
        {
            point
            for point in excluded_points
            if _is_finite_number(point) and _interval_contains(interval, point)
        }
    )
    lower, lower_inclusive, upper, upper_inclusive = interval
    for point in relevant_points:
        if _interval_has_domain_value(
            (lower, lower_inclusive, point, False), logical_type
        ):
            return True
        lower = point
        lower_inclusive = False
    return _interval_has_domain_value(
        (lower, lower_inclusive, upper, upper_inclusive), logical_type
    )


def _intersect_numeric_intervals(
    left: tuple[int | float, bool, int | float, bool],
    right: tuple[int | float, bool, int | float, bool],
) -> tuple[int | float, bool, int | float, bool]:
    left_lower, left_lower_inclusive, left_upper, left_upper_inclusive = left
    right_lower, right_lower_inclusive, right_upper, right_upper_inclusive = right
    if left_lower > right_lower:
        lower, lower_inclusive = left_lower, left_lower_inclusive
    elif right_lower > left_lower:
        lower, lower_inclusive = right_lower, right_lower_inclusive
    else:
        lower = left_lower
        lower_inclusive = left_lower_inclusive and right_lower_inclusive
    if left_upper < right_upper:
        upper, upper_inclusive = left_upper, left_upper_inclusive
    elif right_upper < left_upper:
        upper, upper_inclusive = right_upper, right_upper_inclusive
    else:
        upper = left_upper
        upper_inclusive = left_upper_inclusive and right_upper_inclusive
    return lower, lower_inclusive, upper, upper_inclusive


def _threshold_predicate_satisfiable(
    minimum: int | float,
    maximum: int | float,
    logical_type: str,
    decision: Mapping[str, Any],
) -> bool:
    interval = _predicate_interval(
        minimum,
        maximum,
        decision["operator"],
        decision["value"],
    )
    return interval is not None and _interval_has_accepted_value(
        interval, logical_type
    )


def _threshold_predicates_intersect(
    minimum: int | float,
    maximum: int | float,
    logical_type: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_interval = _predicate_interval(
        minimum, maximum, left["operator"], left["value"]
    )
    right_interval = _predicate_interval(
        minimum, maximum, right["operator"], right["value"]
    )
    if left_interval is None or right_interval is None:
        return False
    return _interval_has_accepted_value(
        _intersect_numeric_intervals(left_interval, right_interval), logical_type
    )


def _numeric_fixture_domain(
    field: str,
    score_range: tuple[int | float, int | float],
    logical_type: str,
    assertions: list[Mapping[str, Any]],
) -> tuple[
    tuple[int | float, bool, int | float, bool],
    list[int | float],
    bool,
]:
    """Conjoin one score's numeric fixture assertions without cross-field inference."""

    minimum, maximum = score_range
    interval = (minimum, True, maximum, True)
    excluded_points: list[int | float] = []
    forced_null = False
    for assertion in assertions:
        if assertion["field"] != field:
            continue
        kind = assertion["kind"]
        constraint: tuple[int | float, bool, int | float, bool] | None = None
        if kind == "score_range":
            assertion_minimum = assertion["minimum"]
            assertion_maximum = assertion["maximum"]
            if _is_finite_number(assertion_minimum) and _is_finite_number(
                assertion_maximum
            ):
                constraint = (
                    assertion_minimum,
                    True,
                    assertion_maximum,
                    True,
                )
        elif kind == "predicate":
            operator = assertion["operator"]
            if operator == "is_null":
                forced_null = True
            elif operator == "not_null":
                continue
            elif "value" in assertion:
                value = assertion["value"]
                if (
                    operator == "!="
                    and _is_finite_number(value)
                    and _value_matches_logical_type(value, logical_type)
                ):
                    excluded_points.append(value)
                elif (
                    operator in ORDERING_OPERATORS | {"=="}
                    and _is_finite_number(value)
                    and _value_matches_logical_type(value, logical_type)
                ):
                    constraint = _predicate_interval(
                        minimum, maximum, operator, value
                    )
        if constraint is not None:
            interval = _intersect_numeric_intervals(interval, constraint)
    has_witness = not forced_null and _interval_has_accepted_value(
        interval, logical_type, excluded_points
    )
    return interval, excluded_points, has_witness


def _response_action_satisfies_own_field_assertions(
    action_field: str,
    action: str,
    assertions: list[Mapping[str, Any]],
) -> bool:
    """Check only exact action-string constraints on the action's own field."""

    for assertion in assertions:
        if assertion["field"] != action_field:
            continue
        if assertion["kind"] == "label_equals":
            if assertion["expected_label"] != action:
                return False
            continue
        if assertion["kind"] != "predicate":
            continue
        operator = assertion["operator"]
        if operator == "is_null":
            return False
        if operator in {"not_null", "<", "<=", ">", ">="}:
            continue
        expected = assertion.get("value")
        equal = isinstance(expected, str) and expected == action
        if (operator == "==" and not equal) or (operator == "!=" and equal):
            return False
    return True


def _is_unsupported_contract_version(config: Mapping[str, Any]) -> bool:
    version = config.get("contract_version")
    return _is_finite_number(version) and version == 1


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

    score_ranges: dict[str, tuple[int | float, int | float]] = {}
    score_logical_types: dict[str, str] = {}
    score_field_names = [item["field"] for item in output["score_fields"]]
    if len(score_field_names) != len(set(score_field_names)):
        issues.append(("duplicate_score_field", "output_contract.score_fields"))
    for index, score in enumerate(output["score_fields"]):
        minimum = score["minimum"]
        maximum = score["maximum"]
        if (
            not _is_finite_number(minimum)
            or not _is_finite_number(maximum)
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
        response_declaration = response_outputs.get(score["field"])
        if response_declaration is not None:
            score_logical_types[score["field"]] = response_declaration[
                "logical_type"
            ]
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
            if (
                not _is_finite_number(assertion["minimum"])
                or not _is_finite_number(assertion["maximum"])
                or assertion["minimum"] > assertion["maximum"]
            ):
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

    numeric_fixture_domains: dict[
        str,
        tuple[
            tuple[int | float, bool, int | float, bool],
            list[int | float],
            bool,
        ],
    ] = {}
    for index, score in enumerate(output["score_fields"]):
        field = score["field"]
        score_range = score_ranges[field]
        logical_type = score_logical_types.get(field)
        if (
            logical_type not in NUMERIC_LOGICAL_TYPES
            or not _is_finite_number(score_range[0])
            or not _is_finite_number(score_range[1])
            or score_range[0] > score_range[1]
        ):
            continue
        base_interval = (score_range[0], True, score_range[1], True)
        base_has_witness = _interval_has_accepted_value(
            base_interval, logical_type
        )
        if not base_has_witness:
            issues.append(
                (
                    "score_range_no_accepted_value",
                    f"output_contract.score_fields.{index}",
                )
            )
        fixture_domain = _numeric_fixture_domain(
            field,
            score_range,
            logical_type,
            output["semantic_assertions"],
        )
        numeric_fixture_domains[field] = fixture_domain
        if base_has_witness and not fixture_domain[2]:
            issues.append(
                (
                    "numeric_assertion_conjunction_unsatisfiable",
                    f"output_contract.score_fields.{index}",
                )
            )

    response_bound_decisions: list[tuple[int, Mapping[str, Any]]] = []
    for index, decision in enumerate(handoff["threshold_artifact"]["decisions"]):
        path = f"model_handoff.threshold_artifact.decisions.{index}"
        score_range = score_ranges.get(decision["field"])
        logical_type = score_logical_types.get(decision["field"])
        satisfiable = False
        if score_range is None:
            issues.append(("threshold_score_undeclared", path))
        elif not _is_finite_number(decision["value"]) or not (
            score_range[0] <= decision["value"] <= score_range[1]
        ):
            issues.append(("threshold_outside_score_range", path))
        elif logical_type in NUMERIC_LOGICAL_TYPES:
            satisfiable = _threshold_predicate_satisfiable(
                score_range[0], score_range[1], logical_type, decision
            )
            if not satisfiable:
                issues.append(("threshold_predicate_unsatisfiable", path))

        if decision["action_binding"] == "response_field":
            response_bound_decisions.append((index, decision))
            fixture_domain = numeric_fixture_domains.get(decision["field"])
            if satisfiable and fixture_domain is not None and fixture_domain[2]:
                threshold_interval = _predicate_interval(
                    score_range[0],
                    score_range[1],
                    decision["operator"],
                    decision["value"],
                )
                coverage_possible = (
                    threshold_interval is not None
                    and _interval_has_accepted_value(
                        _intersect_numeric_intervals(
                            fixture_domain[0], threshold_interval
                        ),
                        logical_type,
                        fixture_domain[1],
                    )
                )
                if not coverage_possible:
                    issues.append(("threshold_fixture_coverage_unsatisfiable", path))
            action_field = decision["action_field"]
            if not _response_action_satisfies_own_field_assertions(
                action_field,
                decision["action"],
                output["semantic_assertions"],
            ):
                issues.append(
                    ("threshold_fixture_action_coverage_unsatisfiable", path)
                )
            signature_action = signature_outputs.get(action_field)
            response_action = response_outputs.get(action_field)
            if action_field not in required_fields:
                issues.append(("threshold_action_field_not_required", path))
            if signature_action is None or response_action is None:
                issues.append(("threshold_action_field_undeclared", path))
            else:
                if signature_action != response_action:
                    issues.append(("threshold_action_field_declaration_mismatch", path))
                if (
                    signature_action["logical_type"] != "string"
                    or response_action["logical_type"] != "string"
                ):
                    issues.append(("threshold_action_field_not_string", path))
            label_contract = label_fields.get(action_field)
            if (
                label_contract is not None
                and decision["action"] not in label_contract["allowed_labels"]
            ):
                issues.append(("threshold_action_not_allowed", path))

    overlap_found = False
    for left_position, (_left_index, left) in enumerate(response_bound_decisions):
        for right_index, right in response_bound_decisions[left_position + 1 :]:
            if (
                left["action_field"] != right["action_field"]
                or left["action"] == right["action"]
            ):
                continue
            left_range = score_ranges.get(left["field"])
            right_range = score_ranges.get(right["field"])
            left_type = score_logical_types.get(left["field"])
            right_type = score_logical_types.get(right["field"])
            if (
                left_range is None
                or right_range is None
                or left_type not in NUMERIC_LOGICAL_TYPES
                or right_type not in NUMERIC_LOGICAL_TYPES
                or not _threshold_predicate_satisfiable(
                    left_range[0], left_range[1], left_type, left
                )
                or not _threshold_predicate_satisfiable(
                    right_range[0], right_range[1], right_type, right
                )
            ):
                continue
            overlaps = left["field"] != right["field"] or (
                left_type == right_type
                and left_range == right_range
                and _threshold_predicates_intersect(
                    left_range[0],
                    left_range[1],
                    left_type,
                    left,
                    right,
                )
            )
            if overlaps:
                issues.append(
                    (
                        "threshold_action_overlap",
                        f"model_handoff.threshold_artifact.decisions.{right_index}",
                    )
                )
                overlap_found = True
                break
        if overlap_found:
            break
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

    if _is_unsupported_contract_version(config):
        return [("unsupported_contract_version", "contract_version")]
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
    if _is_unsupported_contract_version(contract):
        raise UnsupportedContractVersionError("unsupported_contract_version")
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
        if _is_unsupported_contract_version(contract):
            raise UnsupportedContractVersionError("unsupported_contract_version")
        issues = validate_config(contract, schema)
    except UnsupportedContractVersionError:
        print("INVALID unsupported_contract_version", file=sys.stderr)
        return 1
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
