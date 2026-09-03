"""Copyable, schema-valid starter for a Databricks serving rollout contract."""

from __future__ import annotations

from typing import Any


DISABLED_TELEMETRY: dict[str, Any] = {
    "required": False,
    "mode": "disabled",
}


ENDPOINT_CONTRACT: dict[str, Any] = {
    "contract_version": 1,
    "ownership_mode": "sdk_api_managed",
    "endpoint": {
        "name": "risk-model-dev",
        "workspace_host": "https://example.cloud.databricks.com",
        "target_url": "https://example.cloud.databricks.com/serving-endpoints/risk-model-dev/invocations",
        "route_optimized": False,
        "expected_config_version": 1,
        "creator_principal": "serving-runtime-dev",
    },
    "target_manifest": {
        "environment": "dev",
        "profile": "dev-serving",
        "workspace_host": "https://example.cloud.databricks.com",
        "endpoint_name": "risk-model-dev",
        "target_url": "https://example.cloud.databricks.com/serving-endpoints/risk-model-dev/invocations",
        "model_full_name": "main.ml.risk_model",
        "served_entity_name": "risk-model-v1",
        "destructive_operation": False,
    },
    "model_handoff": {
        "catalog": "main",
        "schema": "ml",
        "model_name": "risk_model",
        "model_version": "1",
        "served_entity_name": "risk-model-v1",
        "mlflow_run_id": "0123456789abcdef0123456789abcdef",
        "signature": {
            "inputs": [{"name": "amount", "logical_type": "double", "nullable": False}],
            "outputs": [
                {"name": "label", "logical_type": "string", "nullable": False},
                {"name": "risk_score", "logical_type": "double", "nullable": False},
            ],
        },
        "input_example": {
            "kind": "synthetic",
            "columns": ["amount"],
            "row_count": 2,
            "artifact_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        },
        "feature_schema": {
            "version": "v1",
            "fingerprint": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            "features": [
                {"name": "amount", "logical_type": "double", "nullable": False}
            ],
        },
        "threshold_artifact": {
            "version": "v1",
            "digest": "abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            "selection_split": "validation",
            "decisions": [
                {
                    "name": "review",
                    "field": "risk_score",
                    "operator": ">=",
                    "value": 0.8,
                    "action": "REVIEW",
                }
            ],
        },
    },
    "request_contract": {
        "version": 1,
        "allowed_shapes": ["dataframe_records", "dataframe_split"],
        "input_schema": [
            {"name": "amount", "logical_type": "double", "nullable": False}
        ],
        "response_cardinality": "one_output_per_input_row",
    },
    "deployment": {
        "served_entities": [
            {
                "name": "risk-model-v1",
                "entity_name": "main.ml.risk_model",
                "entity_version": "1",
                "scaling_mode": "workload_size",
                "workload_size": "Small",
                "workload_type": "CPU",
                "scale_to_zero_enabled": True,
            }
        ],
        "routes": [
            {
                "name": "risk-model-v1",
                "served_entity_name": "risk-model-v1",
                "traffic_percentage": 100,
                "role": "primary",
                "allow_zero_traffic": False,
            }
        ],
    },
    "telemetry": {
        "required": True,
        "mode": "unity_ai_gateway",
        "table_names": {
            "logs_table": "main.observability.risk_model_otel_logs",
            "metrics_table": "main.observability.risk_model_otel_metrics",
            "traces_table": "main.observability.risk_model_otel_spans",
        },
        "inference_table_config": {"sampling_fraction": 1.0},
        "enabled_telemetry_features": [
            "TELEMETRY_FEATURE_LOGS",
            "TELEMETRY_FEATURE_TRACES",
            "TELEMETRY_FEATURE_METRICS",
            "TELEMETRY_FEATURE_INFERENCE_TABLE",
        ],
    },
    "output_contract": {
        "response_schema": [
            {"name": "label", "logical_type": "string", "nullable": False},
            {"name": "risk_score", "logical_type": "double", "nullable": False},
        ],
        "required_response_fields": ["label", "risk_score"],
        "nullable_response_fields": [],
        "score_fields": [
            {
                "field": "risk_score",
                "minimum": 0.0,
                "maximum": 1.0,
                "direction": "higher_is_riskier",
                "units": "probability",
            }
        ],
        "label_fields": [
            {
                "field": "label",
                "allowed_labels": ["ALLOW", "REVIEW"],
                "fallback_labels": [],
            }
        ],
        "semantic_assertions": [
            {
                "kind": "score_range",
                "field": "risk_score",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            {"kind": "predicate", "field": "label", "operator": "not_null"},
        ],
        "minimum_non_fallback_rate": 1.0,
        "allow_identical_outputs": False,
    },
    "production_auth": {
        "principal_type": "service_principal",
        "service_principal_id": "11111111-2222-3333-4444-555555555555",
        "oauth_flow": "oauth_m2m",
        "endpoint_permission": "CAN_QUERY",
        "immutable_creator_identity": "serving-runtime-dev",
        "creator_uc_grants": ["USE_CATALOG", "USE_SCHEMA", "EXECUTE_MODEL"],
    },
    "recovery": {
        "manifest_path": "deploy/recovery/risk-model-dev.json",
        "pre_state_digest": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "reverse_operation": {
            "kind": "route_shift",
            "artifact_path": "deploy/recovery/risk-model-dev-previous-routes.json",
        },
    },
}
