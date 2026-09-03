"""Offline tests for the closed training-config validator."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_eligibility import evaluate_eligibility  # noqa: E402
from privacy_column_cases import (  # noqa: E402
    BENIGN_COLUMN_NAMES,
    SENSITIVE_COLUMN_NAMES,
)
from validate_training_config import validate_config  # noqa: E402

SCHEMA_PATH = SKILL_ROOT / "assets" / "training-config-schema.json"
VALIDATOR_PATH = SCRIPTS_DIR / "validate_training_config.py"


def valid_config() -> dict[str, object]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(schema["examples"][0])


class TrainingConfigTests(unittest.TestCase):
    """Exercise structure and semantics without a Databricks workspace."""

    def setUp(self) -> None:
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def assert_invalid(self, config: dict[str, object], expected_code: str) -> None:
        issues = validate_config(config, self.schema)
        self.assertIn(expected_code, {code for code, _ in issues})

    def test_schema_example_validates_and_drives_eligibility_helper(self) -> None:
        config = valid_config()
        self.assertEqual(validate_config(config, self.schema), [])
        decision = evaluate_eligibility(
            {
                "candidate_id": "candidate-a",
                "metrics": {"pr_auc": 0.8},
                "baseline_metrics": {"pr_auc": 0.7},
                "slices": {"global": {"support": 100, "metric": 0.8}},
                "calibration_error": 0.04,
            },
            policy=config["promotion"]["eligibility"],  # type: ignore[index]
            training_mode=config["training"]["mode"],  # type: ignore[index]
        )
        self.assertTrue(decision.eligible)

    def test_cli_accepts_schema_example(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "training-config.json"
            config_path.write_text(json.dumps(valid_config()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), "--config", str(config_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "VALID training_config")
            self.assertEqual(result.stderr, "")

    def test_cli_reports_signature_column_contract_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = valid_config()
            config["signature"]["input_example"]["columns"] = ["country_code", "amount"]  # type: ignore[index]
            config_path = Path(temporary_directory) / "training-config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), "--config", str(config_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("signature_input_example_columns_mismatch", result.stderr)
            self.assertNotIn("country_code", result.stderr)

    def test_schema_closes_every_typed_object(self) -> None:
        def walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.schema)

    def test_rejects_unknown_required_and_boolean_masquerades(self) -> None:
        unknown = valid_config()
        unknown["model"]["framwork"] = "xgboost"  # type: ignore[index]
        self.assert_invalid(unknown, "schema_additionalProperties")
        missing = valid_config()
        del missing["target"]
        self.assert_invalid(missing, "schema_required")
        boolean_seed = valid_config()
        boolean_seed["splits"]["seed"] = True  # type: ignore[index]
        self.assert_invalid(boolean_seed, "schema_type")

    def test_enabled_policy_requires_metric_and_direction(self) -> None:
        for field in ("metric", "direction"):
            config = valid_config()
            del config["promotion"]["eligibility"][field]  # type: ignore[index]
            with self.subTest(field=field):
                self.assert_invalid(config, "schema_required")
        disabled = valid_config()
        disabled["training"]["mode"] = "dev"  # type: ignore[index]
        disabled["promotion"]["eligibility"] = {"enabled": False}  # type: ignore[index]
        self.assertEqual(validate_config(disabled, self.schema), [])

    def test_training_mode_requirements_and_transformer_foundation_model(self) -> None:
        missing_trials = valid_config()
        del missing_trials["training"]["max_tuning_trials"]  # type: ignore[index]
        self.assert_invalid(missing_trials, "schema_required")
        dev = valid_config()
        dev["training"]["mode"] = "dev"  # type: ignore[index]
        del dev["training"]["sample_cap"]  # type: ignore[index]
        self.assert_invalid(dev, "schema_required")
        full = valid_config()
        del full["training"]["sample_cap"]  # type: ignore[index]
        self.assertEqual(validate_config(full, self.schema), [])
        transformer = valid_config()
        transformer["model"]["framework"] = "transformer"  # type: ignore[index]
        self.assert_invalid(transformer, "schema_required")
        transformer["model"]["foundation_model"] = "distilbert-base-uncased"  # type: ignore[index]
        self.assertEqual(validate_config(transformer, self.schema), [])

    def test_enabled_policy_matches_the_helper_approval_contract(self) -> None:
        config = valid_config()
        config["promotion"]["eligibility"]["approval"] = {"status": "pending"}  # type: ignore[index]
        self.assert_invalid(config, "invalid_eligibility_policy")

    def test_dev_mode_rejects_eligibility_and_every_side_effect(self) -> None:
        enabled = valid_config()
        enabled["training"]["mode"] = "dev"  # type: ignore[index]
        self.assert_invalid(enabled, "dev_mode_ineligible")
        for container, field in (
            ("outputs", "register_model"),
            ("outputs", "write_promotion_candidate"),
            ("promotion", "request_promotion"),
        ):
            config = valid_config()
            config["training"]["mode"] = "dev"  # type: ignore[index]
            config["promotion"]["eligibility"] = {"enabled": False}  # type: ignore[index]
            config[container][field] = True  # type: ignore[index]
            if field == "register_model":
                config["outputs"]["registered_model_name"] = (
                    "main.ml_models.fraud_detector"  # type: ignore[index]
                )
            with self.subTest(field=field):
                self.assert_invalid(config, "dev_mode_side_effect")

    def test_side_effects_require_full_enabled_eligibility(self) -> None:
        config = valid_config()
        config["promotion"]["eligibility"] = {"enabled": False}  # type: ignore[index]
        config["outputs"]["register_model"] = True  # type: ignore[index]
        config["outputs"]["registered_model_name"] = "main.ml_models.fraud_detector"  # type: ignore[index]
        self.assert_invalid(config, "effects_require_full_enabled_eligibility")

    def test_registered_model_destination_is_explicit_and_distinct(self) -> None:
        config = valid_config()
        config["outputs"]["register_model"] = True  # type: ignore[index]
        self.assert_invalid(config, "schema_required")

        for destination in (
            "main.ml_models.fraud_detector",
            "ml-team.2026models.fraud-detector",
            "1catalog.2schema.3model",
            "café.モデル.模型",
            f"{'é' * 255}.schema.model",
        ):
            config = valid_config()
            config["outputs"]["register_model"] = True  # type: ignore[index]
            config["outputs"]["registered_model_name"] = destination  # type: ignore[index]
            with self.subTest(destination=destination):
                self.assertEqual(validate_config(config, self.schema), [])

        config = valid_config()
        config["outputs"]["registered_model_name"] = "main.ml_models.fraud_detector"  # type: ignore[index]
        self.assertEqual(validate_config(config, self.schema), [])

    def test_rejects_malformed_registered_model_destinations(self) -> None:
        for destination in (
            "main",
            "main.ml_models",
            "main.ml_models.fraud_detector.v2",
            ".ml_models.fraud_detector",
            "main..fraud_detector",
            "main.ml models.fraud_detector",
            "main/ml_models.fraud_detector",
            f"{'é' * 256}.schema.model",
        ):
            config = valid_config()
            config["outputs"]["registered_model_name"] = destination  # type: ignore[index]
            with self.subTest(destination=destination):
                self.assert_invalid(config, "schema_pattern")

    def test_rejects_controls_and_sql_quoted_registered_model_destinations(
        self,
    ) -> None:
        for destination in (
            "main.schema.model\n",
            "main.schema.model\r",
            "main.\x00schema.model",
            "main.\x1fschema.model",
            "main.\x7fschema.model",
            "`main`.`schema`.`model`",
            "main.`schema`.model",
            "main.schema.model`",
        ):
            config = valid_config()
            config["outputs"]["registered_model_name"] = destination  # type: ignore[index]
            with self.subTest(destination=destination):
                self.assert_invalid(config, "schema_not")

    def test_logged_model_name_preserves_legacy_safe_labels(self) -> None:
        for name in (
            "fraud_detector",
            "fraud-detector",
            "fraud2",
            "fraud.detector",
            "main.ml_models.fraud_detector",
        ):
            config = valid_config()
            config["model"]["name"] = name  # type: ignore[index]
            with self.subTest(name=name):
                self.assertEqual(validate_config(config, self.schema), [])
        for name in ("fraud_model\n", "fraud_model\r"):
            config = valid_config()
            config["model"]["name"] = name  # type: ignore[index]
            with self.subTest(name=name):
                self.assert_invalid(config, "schema_not")

        config = valid_config()
        config["model"]["name"] = "main.ml_models.fraud_detector"  # type: ignore[index]
        config["outputs"]["register_model"] = True  # type: ignore[index]
        self.assert_invalid(config, "schema_required")

    def test_rejects_metric_direction_mismatches(self) -> None:
        for container, metric_key, direction_key in (
            ("metrics", "selection_metric", "direction"),
            ("promotion", "selection.metric", "selection.direction"),
            ("promotion", "eligibility.metric", "eligibility.direction"),
        ):
            config = valid_config()
            target: object = config[container]
            for part in metric_key.split(".")[:-1]:
                target = target[part]  # type: ignore[index]
            target[metric_key.split(".")[-1]] = "log_loss"  # type: ignore[index]
            direction_target: object = config[container]
            for part in direction_key.split(".")[:-1]:
                direction_target = direction_target[part]  # type: ignore[index]
            direction_target[direction_key.split(".")[-1]] = "maximize"  # type: ignore[index]
            with self.subTest(container=container, metric_key=metric_key):
                self.assert_invalid(config, "invalid_metric_direction")

    def test_rejects_impossible_configured_metric_domains(self) -> None:
        config = valid_config()
        config["metrics"]["required_slices"][0]["minimum_metric"] = 1.01  # type: ignore[index]
        self.assert_invalid(config, "invalid_metric_domain")
        for field, value in (
            ("metric_threshold", 1.01),
            (
                "required_slices",
                [{"name": "global", "minimum_support": 50, "minimum_metric": -0.01}],
            ),
        ):
            config = valid_config()
            config["promotion"]["eligibility"][field] = value  # type: ignore[index]
            with self.subTest(field=field):
                self.assert_invalid(
                    config,
                    "invalid_eligibility_policy"
                    if field == "metric_threshold"
                    else "schema_minimum",
                )

    def test_baseline_delta_schema_matches_metric_domain(self) -> None:
        bounded = valid_config()
        bounded["promotion"]["eligibility"]["baseline_delta"] = 1.01  # type: ignore[index]
        self.assert_invalid(bounded, "schema_maximum")
        bounded["promotion"]["eligibility"]["baseline_delta"] = 1.0  # type: ignore[index]
        self.assertEqual(validate_config(bounded, self.schema), [])

        log_loss = valid_config()
        log_loss["metrics"]["selection_metric"] = "log_loss"  # type: ignore[index]
        log_loss["metrics"]["direction"] = "minimize"  # type: ignore[index]
        log_loss["promotion"]["selection"] = {  # type: ignore[index]
            "metric": "log_loss",
            "direction": "minimize",
            "split": "validation",
            "tie_break": "candidate_id_ascending",
        }
        log_loss["promotion"]["eligibility"]["metric"] = "log_loss"  # type: ignore[index]
        log_loss["promotion"]["eligibility"]["direction"] = "minimize"  # type: ignore[index]
        log_loss["promotion"]["eligibility"]["baseline_delta"] = 2.0  # type: ignore[index]
        self.assertEqual(validate_config(log_loss, self.schema), [])

    def test_selection_contract_must_match_declared_selection_metric(self) -> None:
        config = valid_config()
        config["promotion"]["selection"]["metric"] = "roc_auc"  # type: ignore[index]
        self.assert_invalid(config, "selection_metric_mismatch")

    def test_rejects_duplicate_hyperparameter_names_and_slice_names(self) -> None:
        hyperparameters = valid_config()
        hyperparameters["model"]["hyperparameters"].append(  # type: ignore[index]
            {"name": "max_depth", "value": 8}
        )
        self.assert_invalid(hyperparameters, "duplicate_hyperparameter_name")
        distinct_hyperparameters = valid_config()
        distinct_hyperparameters["model"]["hyperparameters"].append(  # type: ignore[index]
            {"name": "min_child_weight", "value": 1}
        )
        self.assertEqual(validate_config(distinct_hyperparameters, self.schema), [])

        metrics = valid_config()
        metrics["metrics"]["required_slices"].append(  # type: ignore[index]
            {"name": "global", "minimum_support": 10, "minimum_metric": 0.1}
        )
        self.assert_invalid(metrics, "duplicate_required_slice_name")
        promotion = valid_config()
        promotion["promotion"]["eligibility"]["required_slices"].append(  # type: ignore[index]
            {"name": "global", "minimum_support": 10, "minimum_metric": 0.1}
        )
        self.assert_invalid(promotion, "duplicate_promotion_required_slice_name")
        distinct_slices = valid_config()
        distinct_slices["metrics"]["required_slices"].append(  # type: ignore[index]
            {"name": "region", "minimum_support": 10, "minimum_metric": 0.1}
        )
        distinct_slices["promotion"]["eligibility"]["required_slices"].append(  # type: ignore[index]
            {"name": "region", "minimum_support": 10, "minimum_metric": 0.1}
        )
        self.assertEqual(validate_config(distinct_slices, self.schema), [])

    def test_rejects_reversed_overlapping_and_out_of_order_split_windows(self) -> None:
        reversed_window = valid_config()
        reversed_window["splits"]["train"] = {  # type: ignore[index]
            "start": "2025-03-01T00:00:00Z",
            "end": "2025-02-01T00:00:00Z",
        }
        self.assert_invalid(reversed_window, "window_not_ordered")
        overlap = valid_config()
        overlap["splits"]["validation"]["start"] = "2025-01-15T00:00:00Z"  # type: ignore[index]
        self.assert_invalid(overlap, "split_windows_overlap")
        out_of_order = valid_config()
        out_of_order["splits"]["validation"] = {  # type: ignore[index]
            "start": "2024-12-01T00:00:00Z",
            "end": "2024-12-15T00:00:00Z",
        }
        self.assert_invalid(out_of_order, "split_windows_not_chronological")

    def test_chronological_gaps_are_valid(self) -> None:
        config = valid_config()
        config["splits"]["validation"] = {  # type: ignore[index]
            "start": "2025-02-10T00:00:00Z",
            "end": "2025-02-15T00:00:00Z",
        }
        config["splits"]["test"] = {  # type: ignore[index]
            "start": "2025-02-20T00:00:00Z",
            "end": "2025-03-01T00:00:00Z",
        }
        self.assertEqual(validate_config(config, self.schema), [])

    def test_rejects_invalid_rfc3339_and_missing_timestamp_column(self) -> None:
        config = valid_config()
        config["provenance"]["run_started_at"] = "2025-13-01T00:00:00Z"  # type: ignore[index]
        self.assert_invalid(config, "invalid_rfc3339")
        config = valid_config()
        del config["provenance"]["example_timestamp_column"]  # type: ignore[index]
        self.assert_invalid(config, "schema_required")

    def test_rejects_unsafe_volume_project_and_identifier_paths(self) -> None:
        for root in (
            "/Volumes/main/ml_models",
            "/Volumes/main/ml_models/artifacts/../escape",
            "/Volumes/main/ml_models/artifacts\\escape",
            "/Volumes/main/ml_models/artifacts/unsafe space",
            "/volumes/main/ml_models/artifacts",
        ):
            config = valid_config()
            config["outputs"]["artifact_root"] = root  # type: ignore[index]
            with self.subTest(root=root):
                self.assert_invalid(config, "invalid_uc_volume_root")
        config = valid_config()
        config["outputs"] = {
            "artifact_root": "/project/../escape",
            "project_root": "/project",
        }
        self.assert_invalid(config, "invalid_project_path")
        config = valid_config()
        config["outputs"] = {
            "artifact_root": "/other/artifacts",
            "project_root": "/project",
        }
        self.assert_invalid(config, "artifact_root_outside_project_root")
        for field, value in (
            ("job_id", "../job"),
            ("run_id", "/run"),
            ("idempotency_key", "C:/run"),
        ):
            config = valid_config()
            config["provenance"][field] = value  # type: ignore[index]
            with self.subTest(field=field):
                self.assert_invalid(config, "schema_pattern")

    def test_rejects_sensitive_input_example_variants(self) -> None:
        for column in SENSITIVE_COLUMN_NAMES:
            config = valid_config()
            config["signature"]["input_example"]["columns"] = ["amount", column]  # type: ignore[index]
            with self.subTest(column=column):
                self.assert_invalid(config, "sensitive_input_example_column")
        for column in BENIGN_COLUMN_NAMES:
            config = valid_config()
            config["signature"]["input_example"]["columns"] = ["amount", column]  # type: ignore[index]
            with self.subTest(column=column):
                codes = {code for code, _ in validate_config(config, self.schema)}
                self.assertIn("signature_input_example_columns_mismatch", codes)
                self.assertNotIn("sensitive_input_example_column", codes)

    def test_signature_input_example_columns_match_features_exactly(self) -> None:
        for columns in (
            ["amount"],
            ["amount", "country_code", "extra_feature"],
            ["country_code", "amount"],
        ):
            config = valid_config()
            config["signature"]["input_example"]["columns"] = columns  # type: ignore[index]
            with self.subTest(columns=columns):
                self.assert_invalid(config, "signature_input_example_columns_mismatch")

    def test_rejects_non_array_input_example_columns(self) -> None:
        for columns in ("amount", b"amount", None, {"amount"}):
            config = valid_config()
            config["signature"]["input_example"]["columns"] = columns  # type: ignore[index]
            with self.subTest(columns_type=type(columns).__name__):
                self.assert_invalid(config, "schema_type")

    def test_requires_feature_artifact_to_agree_with_columns_and_target(self) -> None:
        config = valid_config()
        config["features"]["columns"] = ["country_code", "amount"]  # type: ignore[index]
        self.assert_invalid(config, "feature_schema_columns_mismatch")
        config = valid_config()
        config["features"]["schema_artifact"]["target"]["name"] = "other_label"  # type: ignore[index]
        from feature_schema_contract import compute_feature_schema_fingerprint

        config["features"]["schema_artifact"]["fingerprint"] = (  # type: ignore[index]
            compute_feature_schema_fingerprint(config["features"]["schema_artifact"])  # type: ignore[index]
        )
        self.assert_invalid(config, "feature_schema_target_mismatch")
        config = valid_config()
        config["features"]["schema_artifact"]["features"][0]["null_policy"] = "guess"  # type: ignore[index]
        self.assert_invalid(config, "schema_enum")

    def test_imbalance_contract_handles_binary_and_multiclass_cases(self) -> None:
        config = valid_config()
        config["target"] = {
            "column": "label",
            "task_type": "multiclass",
            "class_labels": ["a", "b", "c"],
        }
        config["threshold"]["enabled"] = False  # type: ignore[index]
        config["imbalance"] = {"strategy": "scale_pos_weight", "scale_pos_weight": 2.0}
        self.assert_invalid(config, "scale_pos_weight_binary_only")
        config["imbalance"] = {
            "strategy": "class_weight",
            "class_weights": [
                {"label": "a", "weight": 1.0},
                {"label": "b", "weight": 2.0},
                {"label": "d", "weight": 2.0},
            ],
        }
        self.assert_invalid(config, "multiclass_class_weights_mismatch")
        config["imbalance"]["class_weights"][2]["label"] = "c"  # type: ignore[index]
        self.assertEqual(validate_config(config, self.schema), [])

    def test_class_weight_labels_use_json_numeric_equality(self) -> None:
        duplicate = valid_config()
        duplicate["imbalance"] = {  # type: ignore[index]
            "strategy": "class_weight",
            "class_weights": [
                {"label": 1, "weight": 1.0},
                {"label": 1.0, "weight": 2.0},
            ],
        }
        self.assert_invalid(duplicate, "duplicate_class_weight_label")
        boolean_label = valid_config()
        boolean_label["imbalance"] = {  # type: ignore[index]
            "strategy": "class_weight",
            "class_weights": [
                {"label": True, "weight": 1.0},
                {"label": "other", "weight": 2.0},
            ],
        }
        self.assert_invalid(boolean_label, "schema_type")

    def test_rejects_multiclass_threshold_and_strategy_specific_fields(self) -> None:
        config = valid_config()
        config["target"] = {
            "column": "label",
            "task_type": "multiclass",
            "class_labels": ["a", "b", "c"],
        }
        self.assert_invalid(config, "binary_threshold_required")
        config = valid_config()
        config["imbalance"] = {"strategy": "none", "scale_pos_weight": 10.0}
        self.assert_invalid(config, "schema_not")

    def test_cli_rejects_nan_and_infinity_without_echoing_values(self) -> None:
        for non_finite in ("NaN", "Infinity"):
            with (
                self.subTest(non_finite=non_finite),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                config_path = Path(temporary_directory) / "training-config.json"
                text = json.dumps(valid_config()).replace("25.0", non_finite, 1)
                config_path.write_text(text, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(VALIDATOR_PATH), "--config", str(config_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(non_finite, result.stdout + result.stderr)

    def test_cli_is_value_free_for_semantic_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = valid_config()
            config["outputs"] = {"artifact_root": "private-token-must-not-appear"}
            config_path = Path(temporary_directory) / "training-config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), "--config", str(config_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("invalid_project_path", result.stderr)
            self.assertNotIn("private-token-must-not-appear", result.stderr)


if __name__ == "__main__":
    unittest.main()
