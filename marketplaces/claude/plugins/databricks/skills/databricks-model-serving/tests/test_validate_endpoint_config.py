"""Offline tests for the closed model-serving rollout contract."""

from __future__ import annotations

import copy
import json
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


def test_schema_meta_validates_and_example_is_semantically_valid() -> None:
    contract_schema = schema()
    Draft202012Validator.check_schema(contract_schema)
    assert validate_config(valid_config(), contract_schema) == []


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
    path.write_text(json.dumps(invalid), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--config", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr

    path.write_text('{"contract_version":1,"contract_version":2}', encoding="utf-8")
    duplicate = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--config", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode == 2
    path.write_text('{"contract_version":NaN}', encoding="utf-8")
    nonfinite = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--config", str(path)],
        check=False,
        capture_output=True,
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
