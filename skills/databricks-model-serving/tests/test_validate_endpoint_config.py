"""Offline tests for the closed model-serving rollout contract."""

from __future__ import annotations

import copy
import json
import math
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "assets" / "endpoint-config-schema.json"
VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_endpoint_config.py"
sys.path.insert(0, str(VALIDATOR_PATH.parent))

from validate_endpoint_config import validate_config  # noqa: E402


def schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def valid_config() -> dict[str, object]:
    return copy.deepcopy(schema()["examples"][0])


def issue_codes(config: dict[str, object]) -> set[str]:
    return {code for code, _ in validate_config(config, schema())}


def add_output_field(
    config: dict[str, object],
    name: str,
    logical_type: str,
    *,
    nullable: bool = False,
) -> None:
    declaration = {
        "name": name,
        "logical_type": logical_type,
        "nullable": nullable,
    }
    config["model_handoff"]["signature"]["outputs"].append(  # type: ignore[index]
        copy.deepcopy(declaration)
    )
    config["output_contract"]["response_schema"].append(  # type: ignore[index]
        copy.deepcopy(declaration)
    )
    config["output_contract"]["required_response_fields"].append(name)  # type: ignore[index]
    if nullable:
        config["output_contract"]["nullable_response_fields"].append(name)  # type: ignore[index]


def threshold_decisions(config: dict[str, object]) -> list[dict[str, object]]:
    return config["model_handoff"]["threshold_artifact"]["decisions"]  # type: ignore[index,return-value]


def test_schema_meta_validates_and_example_is_semantically_valid() -> None:
    contract_schema = schema()
    Draft202012Validator.check_schema(contract_schema)
    assert validate_config(valid_config(), contract_schema) == []


def test_rollout_contract_is_v2_while_request_contract_remains_v1() -> None:
    config = valid_config()
    assert config["contract_version"] == 2
    assert config["request_contract"]["version"] == 1  # type: ignore[index]

    config["contract_version"] = 1
    assert validate_config(config, schema()) == [
        ("unsupported_contract_version", "contract_version")
    ]

    for invalid_version in (True, 3, 3.0):
        config["contract_version"] = invalid_version
        assert "schema_const" in issue_codes(config)


def test_explicit_empty_fallback_set_with_expected_model_scored_is_valid() -> None:
    config = valid_config()
    label = config["output_contract"]["label_fields"][0]  # type: ignore[index]
    label["allowed_labels"].append("MODEL_SCORED")
    label["fallback_labels"] = []
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "label_equals",
            "field": "label",
            "expected_label": "MODEL_SCORED",
        }
    )
    threshold_decisions(config)[0]["action"] = "MODEL_SCORED"

    assert validate_config(config, schema()) == []


def test_python_starter_is_schema_and_semantically_valid() -> None:
    namespace = runpy.run_path(
        str(SKILL_ROOT / "assets" / "serving-config-template.py")
    )
    assert validate_config(namespace["ENDPOINT_CONTRACT"], schema()) == []
    disabled = copy.deepcopy(namespace["ENDPOINT_CONTRACT"])
    disabled["telemetry"] = namespace["DISABLED_TELEMETRY"]
    assert validate_config(disabled, schema()) == []


def gpu_xlarge_config() -> dict[str, object]:
    config = valid_config()
    entity = config["deployment"]["served_entities"][0]  # type: ignore[index]
    entity["workload_type"] = "GPU_XLARGE"
    entity["scale_to_zero_enabled"] = False
    target = (
        "https://example.cloud.databricks.com/serving-endpoints/"
        "risk-model-prod/invocations"
    )
    config["endpoint"]["route_optimized"] = False  # type: ignore[index]
    config["endpoint"]["target_url"] = target  # type: ignore[index]
    config["target_manifest"]["target_url"] = target  # type: ignore[index]
    del config["production_auth"]["route_optimized_transport"]  # type: ignore[index]
    return config


def test_gpu_xlarge_is_valid_with_static_incompatibilities_disabled() -> None:
    config = gpu_xlarge_config()
    assert validate_config(config, schema()) == []


def test_gpu_xlarge_rejects_scale_to_zero() -> None:
    config = gpu_xlarge_config()
    config["deployment"]["served_entities"][0][  # type: ignore[index]
        "scale_to_zero_enabled"
    ] = True
    assert ("schema_const", "deployment.served_entities.0.scale_to_zero_enabled") in (
        validate_config(config, schema())
    )


def test_gpu_xlarge_rejects_route_optimization() -> None:
    config = valid_config()
    config["deployment"]["served_entities"][0][  # type: ignore[index]
        "workload_type"
    ] = "GPU_XLARGE"
    assert ("schema_const", "endpoint.route_optimized") in validate_config(
        config, schema()
    )


def test_gpu_xlarge_does_not_broaden_workload_type_policy() -> None:
    config = gpu_xlarge_config()
    entity = config["deployment"]["served_entities"][0]  # type: ignore[index]
    entity["workload_type"] = "GPU_XXLARGE"
    assert "schema_enum" in issue_codes(config)


@pytest.mark.parametrize("workload_type", ["GPU_SMALL", "GPU_MEDIUM", "GPU_LARGE"])
def test_gpu_xlarge_static_rules_do_not_change_other_gpu_types(
    workload_type: str,
) -> None:
    config = valid_config()
    entity = config["deployment"]["served_entities"][0]  # type: ignore[index]
    entity["workload_type"] = workload_type
    entity["scale_to_zero_enabled"] = True
    assert validate_config(config, schema()) == []


def test_schema_closes_every_object_and_requires_meaningful_output() -> None:
    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    contract_schema = schema()
    walk(contract_schema)
    empty_required = valid_config()
    empty_required["output_contract"]["required_response_fields"] = []  # type: ignore[index]
    assert "schema_minItems" in issue_codes(empty_required)
    empty_assertions = valid_config()
    empty_assertions["output_contract"]["semantic_assertions"] = []  # type: ignore[index]
    assert "schema_minItems" in issue_codes(empty_assertions)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_host", "http://workspace.example"),
        ("workspace_host", "https://user:secret@workspace.example"),
        ("workspace_host", "https://workspace.example/path"),
        ("target_url", "https://user:secret@route.example/invocations"),
        ("target_url", "https://route.example/invocations?token=private"),
    ],
)
def test_rejects_non_https_credential_bearing_or_non_origin_urls(
    field: str, value: str
) -> None:
    config = valid_config()
    config["endpoint"][field] = value  # type: ignore[index]
    assert issue_codes(config)


def test_root_and_target_manifest_must_match_exactly() -> None:
    for field, value in (
        ("endpoint_name", "other-endpoint"),
        ("workspace_host", "https://other.cloud.databricks.com"),
        (
            "target_url",
            "https://other.serving.cloud.databricks.com/123/serving-endpoints/risk-model-prod/invocations",
        ),
    ):
        config = valid_config()
        config["target_manifest"][field] = value  # type: ignore[index]
        assert "target_manifest_mismatch" in issue_codes(config)


def test_route_optimized_target_requires_dedicated_url_and_exact_endpoint_path() -> (
    None
):
    config = valid_config()
    workspace_url = (
        "https://example.cloud.databricks.com/serving-endpoints/"
        "risk-model-prod/invocations"
    )
    config["endpoint"]["target_url"] = workspace_url  # type: ignore[index]
    config["target_manifest"]["target_url"] = workspace_url  # type: ignore[index]
    assert "invalid_route_optimized_target" in issue_codes(config)
    config = valid_config()
    wrong_endpoint = (
        "https://abc123.serving.cloud.databricks.com/123/serving-endpoints/"
        "other/invocations"
    )
    config["endpoint"]["target_url"] = wrong_endpoint  # type: ignore[index]
    config["target_manifest"]["target_url"] = wrong_endpoint  # type: ignore[index]
    assert "target_url_endpoint_mismatch" in issue_codes(config)


def test_model_handoff_is_exact_across_manifest_entity_features_and_example() -> None:
    config = valid_config()
    config["deployment"]["served_entities"][0]["entity_version"] = "8"  # type: ignore[index]
    assert "model_handoff_entity_mismatch" in issue_codes(config)
    config = valid_config()
    config["model_handoff"]["input_example"]["columns"].reverse()  # type: ignore[index]
    assert "input_example_feature_schema_mismatch" in issue_codes(config)
    config = valid_config()
    config["model_handoff"]["signature"]["inputs"].reverse()  # type: ignore[index]
    assert "signature_feature_schema_mismatch" in issue_codes(config)


def test_routes_require_unique_names_exact_total_and_known_entities() -> None:
    config = valid_config()
    config["deployment"]["routes"][0]["traffic_percentage"] = 99  # type: ignore[index]
    assert "traffic_total_not_100" in issue_codes(config)
    config = valid_config()
    config["deployment"]["routes"][0]["served_entity_name"] = "missing"  # type: ignore[index]
    assert "route_entity_missing" in issue_codes(config)
    config = valid_config()
    config["deployment"]["routes"].append(
        copy.deepcopy(config["deployment"]["routes"][0])
    )  # type: ignore[index]
    assert "duplicate_name" in issue_codes(config)


def test_zero_traffic_is_only_valid_as_an_explicit_fallback() -> None:
    config = valid_config()
    route = config["deployment"]["routes"][0]  # type: ignore[index]
    route["traffic_percentage"] = 0
    assert "zero_traffic_not_explicit_fallback" in issue_codes(config)
    route["role"] = "fallback"
    route["allow_zero_traffic"] = True
    config["deployment"]["routes"].append(  # type: ignore[index]
        {
            "name": "risk-model-v8",
            "served_entity_name": "risk-model-v8",
            "traffic_percentage": 100,
            "role": "primary",
            "allow_zero_traffic": False,
        }
    )
    config["deployment"]["served_entities"].append(  # type: ignore[index]
        {
            "name": "risk-model-v8",
            "entity_name": "main.ml.risk_model",
            "entity_version": "8",
            "workload_size": "Medium",
            "workload_type": "CPU",
            "scale_to_zero_enabled": False,
        }
    )
    assert "zero_traffic_not_explicit_fallback" not in issue_codes(config)


def test_score_threshold_and_assertion_ranges_are_ordered_and_consistent() -> None:
    config = valid_config()
    config["output_contract"]["score_fields"][0]["minimum"] = 2.0  # type: ignore[index]
    assert "invalid_score_range" in issue_codes(config)
    config = valid_config()
    config["model_handoff"]["threshold_artifact"]["decisions"][0]["value"] = 2.0  # type: ignore[index]
    assert "threshold_outside_score_range" in issue_codes(config)
    config = valid_config()
    assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    assertion["minimum"] = 2.0
    assert "invalid_assertion_range" in issue_codes(config)


def test_threshold_action_binding_shape_is_closed() -> None:
    config = valid_config()
    del threshold_decisions(config)[0]["action_field"]
    assert "schema_required" in issue_codes(config)

    config = valid_config()
    decision = threshold_decisions(config)[0]
    decision["action_binding"] = "downstream_only"
    assert "schema_not" in issue_codes(config)
    del decision["action_field"]
    assert validate_config(config, schema()) == []

    config = valid_config()
    threshold_decisions(config)[0]["action_binding"] = "implicit"
    assert "schema_enum" in issue_codes(config)

    config = valid_config()
    threshold_decisions(config)[0]["priority"] = 1
    assert "schema_additionalProperties" in issue_codes(config)


def test_response_bound_action_field_is_required_declared_and_string() -> None:
    config = valid_config()
    threshold_decisions(config)[0]["action_field"] = "unknown_action"
    codes = issue_codes(config)
    assert {
        "threshold_action_field_not_required",
        "threshold_action_field_undeclared",
    }.issubset(codes)

    config = valid_config()
    threshold_decisions(config)[0]["action_field"] = "risk_score"
    assert "threshold_action_field_not_string" in issue_codes(config)

    config = valid_config()
    threshold_decisions(config)[0]["action"] = "UNDECLARED"
    assert "threshold_action_not_allowed" in issue_codes(config)

    config = valid_config()
    add_output_field(config, "action_code", "string", nullable=True)
    threshold_decisions(config)[0]["action_field"] = "action_code"
    assert validate_config(config, schema()) == []


@pytest.mark.parametrize(
    "assertion",
    [
        {
            "kind": "label_equals",
            "field": "label",
            "expected_label": "ALLOW",
        },
        {
            "kind": "predicate",
            "field": "label",
            "operator": "==",
            "value": "ALLOW",
        },
        {
            "kind": "predicate",
            "field": "label",
            "operator": "!=",
            "value": "REVIEW",
        },
    ],
)
def test_response_bound_action_must_satisfy_own_field_exact_assertions(
    assertion: dict[str, object],
) -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(assertion)  # type: ignore[index]

    assert "threshold_fixture_action_coverage_unsatisfiable" in issue_codes(config)


def test_response_bound_nullable_action_cannot_require_null_on_matching_row() -> None:
    config = valid_config()
    add_output_field(config, "action_code", "string", nullable=True)
    threshold_decisions(config)[0]["action_field"] = "action_code"
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {"kind": "predicate", "field": "action_code", "operator": "is_null"}
    )

    assert "threshold_fixture_action_coverage_unsatisfiable" in issue_codes(config)


def test_matching_action_and_not_null_assertions_allow_fixture_coverage() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].extend(  # type: ignore[index]
        [
            {
                "kind": "label_equals",
                "field": "label",
                "expected_label": "REVIEW",
            },
            {
                "kind": "predicate",
                "field": "label",
                "operator": "==",
                "value": "REVIEW",
            },
            {
                "kind": "predicate",
                "field": "label",
                "operator": "not_null",
            },
        ]
    )

    assert validate_config(config, schema()) == []


def test_downstream_only_action_is_exempt_from_response_action_assertions() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "label_equals",
            "field": "label",
            "expected_label": "ALLOW",
        }
    )
    decision = threshold_decisions(config)[0]
    decision["action_binding"] = "downstream_only"
    del decision["action_field"]

    assert validate_config(config, schema()) == []


@pytest.mark.parametrize(
    ("operator", "value", "expected_satisfiable"),
    [
        ("<", 0.0, False),
        ("<=", 0.0, True),
        (">", 1.0, False),
        (">=", 1.0, True),
        ("==", 0.5, True),
    ],
)
def test_threshold_predicate_satisfiability_respects_boundaries(
    operator: str, value: float, expected_satisfiable: bool
) -> None:
    config = valid_config()
    decision = threshold_decisions(config)[0]
    decision["operator"] = operator
    decision["value"] = value

    assert (
        "threshold_predicate_unsatisfiable" not in issue_codes(config)
    ) is expected_satisfiable


def test_threshold_predicate_uses_actual_integer_domain() -> None:
    config = valid_config()
    for declaration in (
        config["model_handoff"]["signature"]["outputs"],  # type: ignore[index]
        config["output_contract"]["response_schema"],  # type: ignore[index]
    ):
        declaration[1]["logical_type"] = "integer"
    decision = threshold_decisions(config)[0]
    decision["operator"] = "=="
    decision["value"] = 0.5
    assert "threshold_predicate_unsatisfiable" in issue_codes(config)

    decision["value"] = 1.0
    assert "threshold_predicate_unsatisfiable" not in issue_codes(config)

    config["output_contract"]["score_fields"][0]["minimum"] = 0.2  # type: ignore[index]
    config["output_contract"]["score_fields"][0]["maximum"] = 0.8  # type: ignore[index]
    decision["operator"] = ">="
    decision["value"] = 0.2
    assert "threshold_predicate_unsatisfiable" in issue_codes(config)


def test_threshold_satisfiability_handles_numbers_beyond_float_domain() -> None:
    config = valid_config()
    huge = 10**400
    config["output_contract"]["score_fields"][0]["maximum"] = huge  # type: ignore[index]
    decision = threshold_decisions(config)[0]
    decision["operator"] = "=="
    decision["value"] = huge
    assert "threshold_predicate_unsatisfiable" in issue_codes(config)

    decision["operator"] = "<"
    assert "threshold_predicate_unsatisfiable" not in issue_codes(config)


@pytest.mark.parametrize(
    ("minimum", "maximum", "operator", "threshold"),
    [
        (
            int(sys.float_info.max) + 1,
            int(sys.float_info.max) + 2,
            ">=",
            int(sys.float_info.max) + 1,
        ),
        (
            -int(sys.float_info.max) - 2,
            -int(sys.float_info.max) - 1,
            "<=",
            -int(sys.float_info.max) - 1,
        ),
    ],
)
def test_threshold_satisfiability_uses_actual_accepted_integer_witnesses(
    minimum: int, maximum: int, operator: str, threshold: int
) -> None:
    config = valid_config()
    score = config["output_contract"]["score_fields"][0]  # type: ignore[index]
    score["minimum"] = minimum
    score["maximum"] = maximum
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["minimum"] = minimum
    score_assertion["maximum"] = maximum
    decision = threshold_decisions(config)[0]
    decision["operator"] = operator
    decision["value"] = threshold

    assert validate_config(config, schema()) == []


def test_score_assertion_outside_declared_range_is_impossible() -> None:
    config = valid_config()
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["minimum"] = 2.0
    score_assertion["maximum"] = 3.0

    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)


def test_score_predicate_outside_declared_range_is_impossible() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "predicate",
            "field": "risk_score",
            "operator": ">",
            "value": 1.0,
        }
    )

    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)


def test_integer_score_range_requires_an_accepted_integer_witness() -> None:
    config = valid_config()
    add_output_field(config, "integer_score", "integer")
    config["output_contract"]["score_fields"].append(  # type: ignore[index]
        {
            "field": "integer_score",
            "minimum": 0.2,
            "maximum": 0.8,
            "direction": "custom",
            "units": "count",
        }
    )
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "score_range",
            "field": "integer_score",
            "minimum": 0.2,
            "maximum": 0.8,
        }
    )

    assert "score_range_no_accepted_value" in issue_codes(config)


def test_narrower_overlapping_numeric_assertions_have_a_joint_witness() -> None:
    config = valid_config()
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["minimum"] = 0.2
    score_assertion["maximum"] = 0.8
    config["output_contract"]["semantic_assertions"].extend(  # type: ignore[index]
        [
            {
                "kind": "predicate",
                "field": "risk_score",
                "operator": ">=",
                "value": 0.3,
            },
            {
                "kind": "predicate",
                "field": "risk_score",
                "operator": "<=",
                "value": 0.7,
            },
        ]
    )
    threshold_decisions(config)[0]["value"] = 0.6

    assert validate_config(config, schema()) == []


def test_individually_satisfiable_numeric_predicates_can_be_jointly_impossible() -> (
    None
):
    config = valid_config()
    config["output_contract"]["semantic_assertions"].extend(  # type: ignore[index]
        [
            {
                "kind": "predicate",
                "field": "risk_score",
                "operator": ">=",
                "value": 0.7,
            },
            {
                "kind": "predicate",
                "field": "risk_score",
                "operator": "<=",
                "value": 0.6,
            },
        ]
    )

    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)


def test_numeric_not_equal_exclusion_requires_an_alternative_witness() -> None:
    config = valid_config()
    score = config["output_contract"]["score_fields"][0]  # type: ignore[index]
    score["minimum"] = 1.0
    score["maximum"] = 1.0
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["minimum"] = 1.0
    score_assertion["maximum"] = 1.0
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "predicate",
            "field": "risk_score",
            "operator": "!=",
            "value": 1.0,
        }
    )
    decision = threshold_decisions(config)[0]
    decision["operator"] = ">="
    decision["value"] = 1.0
    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)

    score["maximum"] = 2.0
    score_assertion["maximum"] = 2.0
    decision["value"] = 2.0
    assert validate_config(config, schema()) == []


def test_numeric_equality_conjoins_with_exact_point_exclusions() -> None:
    config = valid_config()
    equals = {
        "kind": "predicate",
        "field": "risk_score",
        "operator": "==",
        "value": 0.5,
    }
    not_equals = {
        "kind": "predicate",
        "field": "risk_score",
        "operator": "!=",
        "value": 0.5,
    }
    config["output_contract"]["semantic_assertions"].extend(  # type: ignore[index]
        [equals, not_equals]
    )
    threshold_decisions(config)[0]["value"] = 0.5
    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)

    config["output_contract"]["semantic_assertions"].remove(not_equals)  # type: ignore[index]
    assert validate_config(config, schema()) == []


def test_nonnull_score_rejects_null_fixture_constraint() -> None:
    config = valid_config()
    null_predicate = {
        "kind": "predicate",
        "field": "risk_score",
        "operator": "is_null",
    }
    config["output_contract"]["semantic_assertions"].append(null_predicate)  # type: ignore[index]
    codes = issue_codes(config)
    assert {
        "predicate_nullability_incompatible",
        "numeric_assertion_conjunction_unsatisfiable",
    }.issubset(codes)

    null_predicate["operator"] = "not_null"
    assert validate_config(config, schema()) == []


def test_integer_assertion_boundary_uses_discrete_domain() -> None:
    config = valid_config()
    add_output_field(config, "integer_score", "integer")
    config["output_contract"]["score_fields"].append(  # type: ignore[index]
        {
            "field": "integer_score",
            "minimum": 0.2,
            "maximum": 1.2,
            "direction": "custom",
            "units": "count",
        }
    )
    predicate = {
        "kind": "predicate",
        "field": "integer_score",
        "operator": ">",
        "value": 1,
    }
    config["output_contract"]["semantic_assertions"].append(predicate)  # type: ignore[index]
    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)

    predicate["operator"] = ">="
    assert validate_config(config, schema()) == []


def test_adjacent_subnormal_constraints_require_a_representable_witness() -> None:
    config = valid_config()
    smallest_positive = math.nextafter(0.0, math.inf)
    score = config["output_contract"]["score_fields"][0]  # type: ignore[index]
    score["minimum"] = 0.0
    score["maximum"] = smallest_positive
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["minimum"] = 0.0
    score_assertion["maximum"] = smallest_positive
    greater_than_zero = {
        "kind": "predicate",
        "field": "risk_score",
        "operator": ">",
        "value": 0.0,
    }
    config["output_contract"]["semantic_assertions"].append(greater_than_zero)  # type: ignore[index]
    decision = threshold_decisions(config)[0]
    decision["operator"] = ">="
    decision["value"] = smallest_positive
    assert validate_config(config, schema()) == []

    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "predicate",
            "field": "risk_score",
            "operator": "<",
            "value": smallest_positive,
        }
    )
    assert "numeric_assertion_conjunction_unsatisfiable" in issue_codes(config)


def test_response_bound_threshold_must_be_coverable_by_fixture_assertions() -> None:
    config = valid_config()
    score_assertion = config["output_contract"]["semantic_assertions"][0]  # type: ignore[index]
    score_assertion["maximum"] = 0.2
    assert "threshold_fixture_coverage_unsatisfiable" in issue_codes(config)

    decision = threshold_decisions(config)[0]
    decision["action_binding"] = "downstream_only"
    del decision["action_field"]
    assert validate_config(config, schema()) == []


def test_policy_overlap_still_uses_declared_score_domain() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"][0]["maximum"] = 0.1  # type: ignore[index]
    decisions = threshold_decisions(config)
    decisions[0].update({"operator": ">=", "value": 0.2, "action": "REVIEW"})
    decisions.append(
        {
            "name": "allow_low",
            "field": "risk_score",
            "operator": "<=",
            "value": 0.8,
            "action": "ALLOW",
            "action_binding": "response_field",
            "action_field": "label",
        }
    )

    assert "threshold_action_overlap" in issue_codes(config)


def test_conflicting_threshold_overlaps_fail_without_priority_inference() -> None:
    config = valid_config()
    decisions = threshold_decisions(config)
    decisions[0].update({"operator": ">=", "value": 0.2, "action": "REVIEW"})
    decisions.append(
        {
            "name": "allow_low",
            "field": "risk_score",
            "operator": "<=",
            "value": 0.8,
            "action": "ALLOW",
            "action_binding": "response_field",
            "action_field": "label",
        }
    )
    assert "threshold_action_overlap" in issue_codes(config)


def test_threshold_overlap_boundary_compatibility_and_action_fields() -> None:
    config = valid_config()
    decisions = threshold_decisions(config)
    decisions[0].update({"operator": "<", "value": 0.5, "action": "ALLOW"})
    decisions.append(
        {
            "name": "review_high",
            "field": "risk_score",
            "operator": ">=",
            "value": 0.5,
            "action": "REVIEW",
            "action_binding": "response_field",
            "action_field": "label",
        }
    )
    assert "threshold_action_overlap" not in issue_codes(config)

    decisions[0]["operator"] = "<="
    assert "threshold_action_overlap" in issue_codes(config)

    decisions[0]["action"] = "REVIEW"
    assert "threshold_action_overlap" not in issue_codes(config)

    add_output_field(config, "action_code", "string")
    decisions[0]["action"] = "ALLOW_CODE"
    decisions[0]["action_field"] = "action_code"
    assert "threshold_action_overlap" not in issue_codes(config)


def test_threshold_overlap_uses_integer_domain_at_shared_boundary() -> None:
    config = valid_config()
    for declarations in (
        config["model_handoff"]["signature"]["outputs"],  # type: ignore[index]
        config["output_contract"]["response_schema"],  # type: ignore[index]
    ):
        declarations[1]["logical_type"] = "integer"
    decisions = threshold_decisions(config)
    decisions[0].update({"operator": "<=", "value": 0.5, "action": "ALLOW"})
    decisions.append(
        {
            "name": "review_high",
            "field": "risk_score",
            "operator": ">=",
            "value": 0.5,
            "action": "REVIEW",
            "action_binding": "response_field",
            "action_field": "label",
        }
    )
    assert "threshold_action_overlap" not in issue_codes(config)

    decisions[0]["value"] = 1.0
    decisions[1]["value"] = 1.0
    assert "threshold_action_overlap" in issue_codes(config)


def test_different_score_fields_conflict_conservatively_for_one_action_field() -> None:
    config = valid_config()
    add_output_field(config, "risk_score_two", "double")
    config["output_contract"]["score_fields"].append(  # type: ignore[index]
        {
            "field": "risk_score_two",
            "minimum": 0.0,
            "maximum": 1.0,
            "direction": "higher_is_riskier",
            "units": "probability",
        }
    )
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {
            "kind": "score_range",
            "field": "risk_score_two",
            "minimum": 0.0,
            "maximum": 1.0,
        }
    )
    threshold_decisions(config).append(
        {
            "name": "allow_second",
            "field": "risk_score_two",
            "operator": "<",
            "value": 0.2,
            "action": "ALLOW",
            "action_binding": "response_field",
            "action_field": "label",
        }
    )

    assert "threshold_action_overlap" in issue_codes(config)


def test_threshold_decision_limit_accepts_100_and_rejects_101() -> None:
    config = valid_config()
    decisions = threshold_decisions(config)
    for index in range(1, 100):
        decision = copy.deepcopy(decisions[0])
        decision["name"] = f"review_{index}"
        decisions.append(decision)
    assert validate_config(config, schema()) == []

    extra = copy.deepcopy(decisions[0])
    extra["name"] = "review_100"
    decisions.append(extra)
    assert "schema_maxItems" in issue_codes(config)


def test_predicate_shape_nullable_and_response_signature_cross_checks() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {"kind": "predicate", "field": "risk_score", "operator": ">="}
    )
    assert "predicate_value_required" in issue_codes(config)
    config = valid_config()
    config["output_contract"]["nullable_response_fields"] = ["unknown"]  # type: ignore[index]
    assert "nullable_field_not_required" in issue_codes(config)
    config = valid_config()
    config["output_contract"]["required_response_fields"].append("unknown")  # type: ignore[index]
    assert "response_signature_contract_mismatch" in issue_codes(config)


def test_required_telemetry_and_production_auth_are_fail_closed() -> None:
    config = valid_config()
    config["telemetry"]["enabled_telemetry_features"].remove(  # type: ignore[index]
        "TELEMETRY_FEATURE_INFERENCE_TABLE"
    )
    assert "required_inference_telemetry_missing" in issue_codes(config)
    config = valid_config()
    config["telemetry"]["inference_table_config"]["sampling_fraction"] = 0  # type: ignore[index]
    assert "required_telemetry_zero_sampling" in issue_codes(config)
    config = valid_config()
    del config["telemetry"]["table_names"]  # type: ignore[index]
    assert "schema_oneOf" in issue_codes(config)
    config = valid_config()
    config["telemetry"] = {"required": False, "mode": "disabled"}
    assert validate_config(config, schema()) == []
    config["telemetry"]["table_names"] = {  # type: ignore[index]
        "logs_table": "main.observability.unexpected_logs"
    }
    assert "schema_oneOf" in issue_codes(config)
    config = valid_config()
    config["production_auth"]["creator_uc_grants"] = ["USE_CATALOG"]  # type: ignore[index]
    assert issue_codes(config) & {"schema_minItems", "creator_uc_grants_incomplete"}
    config = valid_config()
    config["production_auth"]["immutable_creator_identity"] = "another-principal"  # type: ignore[index]
    assert "creator_identity_mismatch" in issue_codes(config)


@pytest.mark.parametrize(
    "path",
    [
        "../escape.json",
        "/absolute/file.json",
        "C:/workspace/file.json",
        "folder\\file.json",
        "folder//file.json",
        "folder/./file.json",
    ],
)
def test_recovery_paths_are_relative_normalized_and_delete_is_not_representable(
    path: str,
) -> None:
    config = valid_config()
    config["recovery"]["manifest_path"] = path  # type: ignore[index]
    assert issue_codes(config)
    delete = valid_config()
    delete["recovery"]["reverse_operation"]["kind"] = "delete"  # type: ignore[index]
    assert "schema_enum" in issue_codes(delete)


def test_validator_cli_is_value_free_and_rejects_duplicate_and_nonfinite_json(
    tmp_path: Path,
) -> None:
    secret = "private-token-must-never-appear"
    invalid = valid_config()
    invalid["endpoint"]["workspace_host"] = f"https://user:{secret}@example.com"  # type: ignore[index]
    path = tmp_path / "contract.json"
    subprocess_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    validator_command = [sys.executable, "-B", str(VALIDATOR_PATH), "--config"]
    path.write_text(json.dumps(invalid), encoding="utf-8")
    result = subprocess.run(
        [*validator_command, str(path)],
        check=False,
        capture_output=True,
        env=subprocess_env,
        text=True,
    )
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr

    version_one = valid_config()
    version_one["unexpected_secret"] = secret
    serialized = json.dumps(version_one)
    for version_literal in ("1", "1.0", "1e0"):
        path.write_text(
            serialized.replace(
                '"contract_version": 2',
                f'"contract_version": {version_literal}',
                1,
            ),
            encoding="utf-8",
        )
        unsupported = subprocess.run(
            [*validator_command, str(path)],
            check=False,
            capture_output=True,
            env=subprocess_env,
            text=True,
        )
        assert unsupported.returncode == 1
        assert unsupported.stdout == ""
        assert unsupported.stderr.strip() == "INVALID unsupported_contract_version"
        assert secret not in unsupported.stderr

    path.write_text('{"contract_version":1,"contract_version":2}', encoding="utf-8")
    duplicate = subprocess.run(
        [*validator_command, str(path)],
        check=False,
        capture_output=True,
        env=subprocess_env,
        text=True,
    )
    assert duplicate.returncode == 2
    path.write_text('{"contract_version":NaN}', encoding="utf-8")
    nonfinite = subprocess.run(
        [*validator_command, str(path)],
        check=False,
        capture_output=True,
        env=subprocess_env,
        text=True,
    )
    assert nonfinite.returncode == 2


def test_route_transport_is_closed_and_bound_to_dedicated_host_id() -> None:
    config = valid_config()
    transport = config["production_auth"]["route_optimized_transport"]  # type: ignore[index]
    transport["route_id"] = "wrong-route"
    assert "route_id_target_mismatch" in issue_codes(config)

    config = valid_config()
    authorization = config["production_auth"]["route_optimized_transport"][  # type: ignore[index]
        "authorization_details"
    ]
    authorization["object_path"] = "/serving-endpoints/wrong-route"
    assert "authorization_details_target_mismatch" in issue_codes(config)

    config = valid_config()
    config["production_auth"]["client_secret"] = "must-never-be-a-contract-field"  # type: ignore[index]
    assert "schema_additionalProperties" in issue_codes(config)


def test_nonoptimized_contract_forbids_route_transport() -> None:
    config = valid_config()
    target = (
        "https://example.cloud.databricks.com/serving-endpoints/"
        "risk-model-prod/invocations"
    )
    config["endpoint"]["route_optimized"] = False  # type: ignore[index]
    config["endpoint"]["target_url"] = target  # type: ignore[index]
    config["target_manifest"]["target_url"] = target  # type: ignore[index]
    assert "schema_not" in issue_codes(config)
    del config["production_auth"]["route_optimized_transport"]  # type: ignore[index]
    assert validate_config(config, schema()) == []


@pytest.mark.parametrize(
    "mode,required_fields,forbidden_field",
    [
        (
            "provisioned_concurrency",
            {"min_provisioned_concurrency": 4, "max_provisioned_concurrency": 8},
            "workload_size",
        ),
        (
            "provisioned_throughput",
            {"min_provisioned_throughput": 970, "max_provisioned_throughput": 1960},
            "workload_size",
        ),
        (
            "provisioned_model_units",
            {"provisioned_model_units": 100, "burst_scaling_enabled": False},
            "workload_size",
        ),
    ],
)
def test_scaling_modes_are_mutually_exclusive_and_validate_ranges(
    mode: str, required_fields: dict[str, object], forbidden_field: str
) -> None:
    config = valid_config()
    entity = config["deployment"]["served_entities"][0]  # type: ignore[index]
    entity["scaling_mode"] = mode
    entity.pop(forbidden_field)
    entity.update(required_fields)
    assert validate_config(config, schema()) == []
    entity[forbidden_field] = "Small"
    assert "schema_not" in issue_codes(config)


@pytest.mark.parametrize(
    "mode,minimum,maximum",
    [
        (
            "provisioned_concurrency",
            "min_provisioned_concurrency",
            "max_provisioned_concurrency",
        ),
        (
            "provisioned_throughput",
            "min_provisioned_throughput",
            "max_provisioned_throughput",
        ),
    ],
)
def test_scaling_minimum_must_not_exceed_maximum(
    mode: str, minimum: str, maximum: str
) -> None:
    config = valid_config()
    entity = config["deployment"]["served_entities"][0]  # type: ignore[index]
    entity["scaling_mode"] = mode
    entity.pop("workload_size")
    entity[minimum] = 8
    entity[maximum] = 4
    assert "scaling_range_not_ordered" in issue_codes(config)


def test_signature_feature_schema_requires_full_declaration_equality() -> None:
    config = valid_config()
    config["model_handoff"]["feature_schema"]["features"][0]["nullable"] = True  # type: ignore[index]
    assert "signature_feature_schema_mismatch" in issue_codes(config)
    config = valid_config()
    config["model_handoff"]["feature_schema"]["features"][0][  # type: ignore[index]
        "logical_type"
    ] = "string"
    assert "signature_feature_schema_mismatch" in issue_codes(config)

    config = valid_config()
    config["request_contract"]["input_schema"][0]["nullable"] = True  # type: ignore[index]
    assert "request_signature_contract_mismatch" in issue_codes(config)


def test_output_types_and_nullability_must_match_semantic_contract() -> None:
    config = valid_config()
    config["model_handoff"]["signature"]["outputs"][1]["logical_type"] = "string"  # type: ignore[index]
    codes = issue_codes(config)
    assert {"score_signature_incompatible", "assertion_type_incompatible"} & codes

    config = valid_config()
    config["output_contract"]["response_schema"][1]["logical_type"] = "string"  # type: ignore[index]
    codes = issue_codes(config)
    assert "response_signature_declaration_mismatch" in codes
    assert "score_signature_incompatible" in codes

    config = valid_config()
    config["model_handoff"]["signature"]["outputs"][0]["logical_type"] = "double"  # type: ignore[index]
    assert "label_signature_incompatible" in issue_codes(config)

    config = valid_config()
    config["model_handoff"]["signature"]["outputs"][1]["nullable"] = True  # type: ignore[index]
    codes = issue_codes(config)
    assert "response_signature_declaration_mismatch" in codes
    assert "score_signature_incompatible" in codes

    config = valid_config()
    config["output_contract"]["response_schema"][1]["nullable"] = True  # type: ignore[index]
    assert "response_nullability_mismatch" in issue_codes(config)


def test_predicates_are_type_and_nullability_compatible() -> None:
    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {"kind": "predicate", "field": "label", "operator": ">", "value": 1}
    )
    assert "predicate_type_incompatible" in issue_codes(config)

    config = valid_config()
    config["output_contract"]["semantic_assertions"].append(  # type: ignore[index]
        {"kind": "predicate", "field": "label", "operator": "is_null"}
    )
    assert "predicate_nullability_incompatible" in issue_codes(config)
