"""Offline temporal-precision and physical-feature-schema contract tests."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feature_schema_contract import (  # noqa: E402
    compute_feature_schema_fingerprint,
    validate_feature_schema_artifact,
    validate_feature_schema_before_fit,
)
from point_in_time_contract import parse_rfc3339, select_feature_as_of  # noqa: E402
from validate_training_config import validate_config  # noqa: E402

SCHEMA_PATH = SKILL_ROOT / "assets" / "training-config-schema.json"


def scalar_config() -> dict[str, object]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(schema["examples"][0])


def physical_artifact() -> dict[str, object]:
    artifact: dict[str, object] = {
        "format": 2,
        "version": "transactions-physical-v2",
        "fingerprint": "",
        "features": [
            {
                "name": "amount",
                "logical_type": "decimal",
                "data_type": "decimal(12,2)",
                "nullable": False,
                "null_policy": "forbid",
            },
            {
                "name": "tags",
                "logical_type": "array",
                "data_type": {
                    "type": "array",
                    "elementType": "string",
                    "containsNull": False,
                },
                "nullable": True,
                "null_policy": "indicator_and_impute",
            },
            {
                "name": "attributes",
                "logical_type": "map",
                "data_type": {
                    "type": "map",
                    "keyType": "string",
                    "valueType": "decimal(8,3)",
                    "valueContainsNull": True,
                },
                "nullable": True,
                "null_policy": "model_native",
            },
            {
                "name": "profile",
                "logical_type": "struct",
                "data_type": {
                    "type": "struct",
                    "fields": [
                        {
                            "name": "country_code",
                            "type": "string",
                            "nullable": True,
                            "metadata": {},
                        },
                        {
                            "name": "risk_score",
                            "type": "double",
                            "nullable": False,
                            "metadata": {},
                        },
                    ],
                },
                "nullable": True,
                "null_policy": "model_native",
            },
        ],
        "target": {
            "name": "label",
            "logical_type": "string",
            "data_type": "string",
            "nullable": False,
            "null_policy": "forbid",
        },
    }
    artifact["fingerprint"] = compute_feature_schema_fingerprint(artifact)
    return artifact


class TemporalAndFeatureSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_microsecond_rfc3339_requires_a_known_offset_without_truncation(self) -> None:
        for timestamp in (
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00.123456+05:30",
            "2025-01-01T00:00:00.000001-00:01",
        ):
            with self.subTest(timestamp=timestamp):
                self.assertIsNotNone(parse_rfc3339(timestamp))
        for timestamp in (
            "2025-01-01T00:00:00",
            "2025-01-01T00:00:00-00:00",
            "2025-01-01T00:00:00.0000004Z",
            "2025-01-01T00:00:00.0000005Z",
            "2025-01-01T00:00:00.123456789+00:00",
            "2025-01-01T00:00:00+24:00",
            "2025-01-01T00:00:00+00:60",
            "2025-01-01T00:00:00+01:99",
            "2025-01-01T00:00:60Z",
        ):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                parse_rfc3339(timestamp)

        with self.assertRaises(ValueError):
            select_feature_as_of(
                example_at="2025-01-01T00:00:00.0000004Z",
                feature_rows=[
                    {
                        "valid_from": "2025-01-01T00:00:00.0000005Z",
                        "valid_to": None,
                        "available_at": "2025-01-01T00:00:00Z",
                    }
                ],
            )

    def test_config_schema_and_semantics_reject_submicrosecond_and_unknown_offsets(self) -> None:
        config = scalar_config()
        config["splits"]["train"]["start"] = "2025-01-01T00:00:00.0000004Z"  # type: ignore[index]
        issues = validate_config(config, self.schema)
        self.assertIn(("schema_pattern", "splits.train.start"), issues)
        config = scalar_config()
        config["provenance"]["run_started_at"] = "2025-03-01T00:00:00-00:00"  # type: ignore[index]
        issues = validate_config(config, self.schema)
        self.assertIn(("schema_pattern", "provenance.run_started_at"), issues)
        config = scalar_config()
        config["splits"]["validation"]["end"] = "2025-02-15T00:00:00+00:60"  # type: ignore[index]
        issues = validate_config(config, self.schema)
        self.assertIn(("schema_pattern", "splits.validation.end"), issues)

    def test_scalar_format_one_is_preserved_but_rejects_coarse_types(self) -> None:
        config = scalar_config()
        self.assertEqual(validate_config(config, self.schema), [])
        artifact = config["features"]["schema_artifact"]  # type: ignore[index]
        original_fingerprint = artifact["fingerprint"]  # type: ignore[index]
        self.assertEqual(
            original_fingerprint,
            compute_feature_schema_fingerprint(artifact),  # type: ignore[arg-type]
        )
        artifact["features"][0]["logical_type"] = "decimal"  # type: ignore[index]
        artifact["fingerprint"] = "0" * 64  # type: ignore[index]
        issues = validate_config(config, self.schema)
        self.assertIn(("invalid_feature_schema_artifact", "features.schema_artifact"), issues)

    def test_scalar_field_tokens_and_observed_feature_containers_fail_as_value_errors(self) -> None:
        for field_name, malformed_value in (
            ("logical_type", []),
            ("logical_type", {}),
            ("logical_type", False),
            ("null_policy", []),
            ("null_policy", {}),
            ("null_policy", True),
        ):
            artifact = scalar_config()["features"]["schema_artifact"]  # type: ignore[index]
            artifact["features"][0][field_name] = malformed_value  # type: ignore[index]
            artifact["fingerprint"] = "0" * 64  # type: ignore[index]
            with self.subTest(field_name=field_name, value_type=type(malformed_value).__name__):
                with self.assertRaises(ValueError):
                    validate_feature_schema_artifact(artifact)  # type: ignore[arg-type]

        artifact = scalar_config()["features"]["schema_artifact"]  # type: ignore[index]
        observed_target = {
            "name": artifact["target"]["name"],  # type: ignore[index]
            "logical_type": artifact["target"]["logical_type"],  # type: ignore[index]
            "nullable": artifact["target"]["nullable"],  # type: ignore[index]
        }
        for observed_features in (None, {"amount": "double"}, iter(())):
            with self.subTest(observed_type=type(observed_features).__name__):
                with self.assertRaises(ValueError):
                    validate_feature_schema_before_fit(
                        artifact,  # type: ignore[arg-type]
                        observed_features=observed_features,  # type: ignore[arg-type]
                        observed_target=observed_target,
                    )

    def test_format_two_uses_canonical_datatype_jsonvalue_fixtures(self) -> None:
        artifact = physical_artifact()
        validate_feature_schema_artifact(artifact)
        observed_features = [
            {
                "name": field["name"],
                "data_type": field["data_type"],
                "nullable": field["nullable"],
            }
            for field in artifact["features"]  # type: ignore[index]
        ]
        observed_target = {
            "name": artifact["target"]["name"],  # type: ignore[index]
            "data_type": artifact["target"]["data_type"],  # type: ignore[index]
            "nullable": artifact["target"]["nullable"],  # type: ignore[index]
        }
        validate_feature_schema_before_fit(
            artifact,
            observed_features=observed_features,
            observed_target=observed_target,
        )

        config = scalar_config()
        config["features"]["columns"] = [field["name"] for field in artifact["features"]]  # type: ignore[index]
        config["features"]["schema_artifact"] = artifact  # type: ignore[index]
        config["target"]["column"] = artifact["target"]["name"]  # type: ignore[index]
        config["signature"]["input_example"]["columns"] = config["features"]["columns"]  # type: ignore[index]
        self.assertEqual(validate_config(config, self.schema), [])

    def test_documented_format_two_migration_fingerprint_is_reproducible(self) -> None:
        migration_artifact = {
            "format": 2,
            "version": "transactions-physical-v2",
            "fingerprint": "",
            "features": [
                {
                    "name": "amount",
                    "logical_type": "decimal",
                    "data_type": "decimal(12,2)",
                    "nullable": False,
                    "null_policy": "forbid",
                }
            ],
            "target": {
                "name": "label",
                "logical_type": "string",
                "data_type": "string",
                "nullable": False,
                "null_policy": "forbid",
            },
        }
        self.assertEqual(
            compute_feature_schema_fingerprint(migration_artifact),
            "972997a0517a5f6b7d5a095f841a085f23dac9653287a53b96995ebbde101eb5",
        )

    def test_fingerprint_computation_accepts_closed_declarations_but_validation_requires_it(self) -> None:
        scalar_artifact = scalar_config()["features"]["schema_artifact"]  # type: ignore[index]
        scalar_declaration = copy.deepcopy(scalar_artifact)
        scalar_fingerprint = scalar_declaration.pop("fingerprint")
        self.assertEqual(
            compute_feature_schema_fingerprint(scalar_declaration), scalar_fingerprint
        )
        scalar_with_placeholder = {**scalar_declaration, "fingerprint": "ignored"}
        self.assertEqual(
            compute_feature_schema_fingerprint(scalar_with_placeholder),
            scalar_fingerprint,
        )
        with self.assertRaises(ValueError):
            validate_feature_schema_artifact(scalar_declaration)

        physical_schema_artifact = physical_artifact()
        physical_fingerprint = physical_schema_artifact["fingerprint"]
        physical_declaration = copy.deepcopy(physical_schema_artifact)
        del physical_declaration["fingerprint"]
        self.assertEqual(
            compute_feature_schema_fingerprint(physical_declaration),
            physical_fingerprint,
        )
        self.assertEqual(
            compute_feature_schema_fingerprint(
                {**physical_declaration, "fingerprint": "placeholder"}
            ),
            physical_fingerprint,
        )
        with self.assertRaises(ValueError):
            validate_feature_schema_artifact(physical_declaration)

        with self.assertRaises(ValueError):
            compute_feature_schema_fingerprint(
                {**scalar_declaration, "unsupported": "extra"}
            )

    def test_decimal_scales_are_canonical_in_helper_and_config_validation(self) -> None:
        for scale in ("0", "2"):
            artifact = physical_artifact()
            artifact["features"][0]["data_type"] = f"decimal(12,{scale})"  # type: ignore[index]
            artifact["fingerprint"] = compute_feature_schema_fingerprint(artifact)  # type: ignore[index]
            with self.subTest(scale=scale):
                validate_feature_schema_artifact(artifact)

        for scale in ("00", "02"):
            artifact = physical_artifact()
            artifact["features"][0]["data_type"] = f"decimal(12,{scale})"  # type: ignore[index]
            artifact["fingerprint"] = "0" * 64  # type: ignore[index]
            with self.subTest(helper_scale=scale), self.assertRaises(ValueError):
                validate_feature_schema_artifact(artifact)

            config = scalar_config()
            config["features"]["columns"] = [field["name"] for field in artifact["features"]]  # type: ignore[index]
            config["features"]["schema_artifact"] = artifact  # type: ignore[index]
            config["target"]["column"] = artifact["target"]["name"]  # type: ignore[index]
            config["signature"]["input_example"]["columns"] = config["features"]["columns"]  # type: ignore[index]
            issues = validate_config(config, self.schema)
            self.assertTrue(any(code.startswith("schema_") for code, _ in issues))

    def test_format_two_detects_physical_drift_and_rejects_unrepresentable_shapes(self) -> None:
        artifact = physical_artifact()
        observed_features = [
            {
                "name": field["name"],
                "data_type": field["data_type"],
                "nullable": field["nullable"],
            }
            for field in artifact["features"]  # type: ignore[index]
        ]
        observed_target = {
            "name": artifact["target"]["name"],  # type: ignore[index]
            "data_type": artifact["target"]["data_type"],  # type: ignore[index]
            "nullable": artifact["target"]["nullable"],  # type: ignore[index]
        }
        drifted = copy.deepcopy(observed_features)
        drifted[0]["data_type"] = "decimal(12,3)"
        with self.assertRaises(ValueError):
            validate_feature_schema_before_fit(
                artifact,
                observed_features=drifted,
                observed_target=observed_target,
            )
        drifted = copy.deepcopy(observed_features)
        drifted[3]["data_type"]["fields"].reverse()
        with self.assertRaises(ValueError):
            validate_feature_schema_before_fit(
                artifact,
                observed_features=drifted,
                observed_target=observed_target,
            )

        for mutation in (
            lambda value: value["features"][0].update({"logical_type": "double"}),
            lambda value: value["features"][0].update({"data_type": "decimal(39,0)"}),
            lambda value: value["features"][1]["data_type"].update({"containsNull": "no"}),
            lambda value: value["features"][2]["data_type"].update({"keyType": {"type": "struct", "fields": []}}),
            lambda value: value["features"][3]["data_type"]["fields"][0].update({"metadata": {"tag": "x"}}),
            lambda value: value["features"][0].update({"data_type": "com.example.CustomType"}),
        ):
            malformed = physical_artifact()
            mutation(malformed)
            malformed["fingerprint"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_feature_schema_artifact(malformed)

        nested: object = "string"
        for _ in range(17):
            nested = {"type": "array", "elementType": nested, "containsNull": False}
        too_deep = physical_artifact()
        too_deep["features"][1]["data_type"] = nested  # type: ignore[index]
        too_deep["fingerprint"] = "0" * 64  # type: ignore[index]
        with self.assertRaises(ValueError):
            validate_feature_schema_artifact(too_deep)

        too_many = physical_artifact()
        template = copy.deepcopy(too_many["features"][0])  # type: ignore[index]
        too_many["features"] = [
            {**template, "name": f"amount_{index}"}
            for index in range(1_025)
        ]  # type: ignore[index]
        too_many["fingerprint"] = "0" * 64  # type: ignore[index]
        with self.assertRaises(ValueError):
            validate_feature_schema_artifact(too_many)

    def test_format_two_rejections_flow_through_closed_config_validation(self) -> None:
        def physical_config() -> dict[str, object]:
            config = scalar_config()
            artifact = physical_artifact()
            config["features"]["columns"] = [field["name"] for field in artifact["features"]]  # type: ignore[index]
            config["features"]["schema_artifact"] = artifact  # type: ignore[index]
            config["target"]["column"] = artifact["target"]["name"]  # type: ignore[index]
            config["signature"]["input_example"]["columns"] = config["features"]["columns"]  # type: ignore[index]
            return config

        unexpected_property = physical_config()
        unexpected_property["features"]["schema_artifact"]["features"][1]["data_type"]["extra"] = True  # type: ignore[index]
        issues = validate_config(unexpected_property, self.schema)
        self.assertTrue(
            any(
                code.startswith("schema_")
                and path == "features.schema_artifact.features.1.data_type"
                for code, path in issues
            )
        )

        nonfinite = physical_config()
        nonfinite["features"]["schema_artifact"]["features"][1]["data_type"] = math.nan  # type: ignore[index]
        issues = validate_config(nonfinite, self.schema)
        self.assertTrue(any(code.startswith("schema_") for code, _ in issues))

        too_deep = physical_config()
        nested: object = "string"
        for _ in range(17):
            nested = {"type": "array", "elementType": nested, "containsNull": False}
        too_deep["features"]["schema_artifact"]["features"][1]["data_type"] = nested  # type: ignore[index]
        issues = validate_config(too_deep, self.schema)
        self.assertIn(("invalid_feature_schema_artifact", "features.schema_artifact"), issues)


if __name__ == "__main__":
    unittest.main()
