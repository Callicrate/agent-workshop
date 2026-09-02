"""Behavioral tests for the bounded Spark pre-write schema contract."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_schema.py"
SPEC = importlib.util.spec_from_file_location("spark_etl_validate_schema", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
validate_schema = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_schema
SPEC.loader.exec_module(validate_schema)


def field(
    name: str,
    type_spec: object,
    nullable: bool = True,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the canonical field shape used by StructType.jsonValue()."""
    return {
        "name": name,
        "type": type_spec,
        "nullable": nullable,
        "metadata": {} if metadata is None else metadata,
    }


def schema(*fields: dict[str, object]) -> dict[str, object]:
    """Build the canonical root StructType shape."""
    return {"type": "struct", "fields": list(fields)}


def struct_type(*fields: dict[str, object]) -> dict[str, object]:
    """Build a nested StructType shape."""
    return {"type": "struct", "fields": list(fields)}


def array_type(element_type: object, *, contains_null: bool = False) -> dict[str, object]:
    """Build an ArrayType shape."""
    return {"type": "array", "elementType": element_type, "containsNull": contains_null}


class _StructType:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def jsonValue(self) -> dict[str, object]:
        return self.payload


class _Frame:
    def __init__(self, payload: dict[str, object]) -> None:
        self.schema = _StructType(payload)


class _BrokenSourceFrame:
    def __init__(self, error: Exception) -> None:
        self.error = error

    @property
    def schema(self) -> object:
        raise self.error


class _BrokenJsonStruct:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def jsonValue(self) -> dict[str, object]:
        raise self.error


class _BrokenJsonFrame:
    def __init__(self, error: Exception) -> None:
        self.schema = _BrokenJsonStruct(error)


class _MissingTableError(Exception):
    def getErrorClass(self) -> str:
        return "TABLE_OR_VIEW_NOT_FOUND"


class _Spark:
    def __init__(self, result: object) -> None:
        self.result = result

    def table(self, target_table: str) -> object:
        if isinstance(self.result, BaseException):
            raise self.result
        return _Frame(self.result)  # type: ignore[arg-type]


class ValidateSchemaTests(unittest.TestCase):
    def test_by_name_permits_reordered_same_type_fields(self) -> None:
        source = schema(field("second", "string", False), field("first", "integer", False))
        target = schema(field("first", "integer", False), field("second", "string", False))

        report = validate_schema.compare_schema_contract(source, target, resolution="by-name")

        self.assertEqual(report["decision"], "compatible")
        self.assertEqual(report["schema_contract_version"], 2)
        self.assertNotIn("write_operation", report)
        self.assertEqual(report["errors"], [])

    def test_by_position_rejects_same_type_field_swap(self) -> None:
        source = schema(field("second", "integer", False), field("first", "integer", False))
        target = schema(field("first", "integer", False), field("second", "integer", False))

        report = validate_schema.compare_schema_contract(source, target, resolution="by-position")

        self.assertEqual(report["decision"], "incompatible")
        self.assertTrue(any(error["type"] == "positional_field_name_mismatch" for error in report["errors"]))

    def test_generic_by_name_still_permits_nested_struct_reordering(self) -> None:
        source = schema(field("profile", struct_type(field("second", "string"), field("first", "integer"))))
        target = schema(field("profile", struct_type(field("first", "integer"), field("second", "string"))))

        report = validate_schema.compare_schema_contract(source, target, resolution="by-name")

        self.assertEqual(report["decision"], "compatible")
        self.assertEqual(report["errors"], [])
        self.assertTrue(any(warning["column"] == "profile" for warning in report["warnings"]))

    def test_delta_insert_by_name_reorders_top_level_but_not_nested_structs(self) -> None:
        ordered_profile = struct_type(field("first", "integer", False), field("second", "string", False))
        source = schema(field("profile", ordered_profile, False), field("id", "long", False))
        target = schema(field("id", "long", False), field("profile", ordered_profile, False))

        report = validate_schema.compare_schema_contract(
            source,
            target,
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "compatible")
        self.assertEqual(report["schema_contract_version"], 3)
        self.assertEqual(report["write_operation"], "delta-sql-insert-by-name")
        self.assertEqual(report["errors"], [])
        self.assertTrue(any(warning["type"] == "by_name_reordered_fields" for warning in report["warnings"]))

    def test_delta_insert_by_name_rejects_conflicting_top_level_resolution(self) -> None:
        report = validate_schema.compare_schema_contract(
            schema(field("id", "long")),
            schema(field("id", "long")),
            resolution="by-position",
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "invalid")
        self.assertEqual(report["errors"][0]["type"], "write_operation_resolution_conflict")

    def test_delta_insert_by_name_rejects_source_only_top_level_columns(self) -> None:
        report = validate_schema.compare_schema_contract(
            schema(field("id", "long"), field("unexpected", "string")),
            schema(field("id", "long")),
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual(report["errors"][0]["type"], "extra_source_column")
        self.assertEqual(report["errors"][0]["column"], "unexpected")

    def test_delta_insert_by_name_uses_bounded_recognized_target_default_markers(self) -> None:
        for metadata in (
            {"CURRENT_DEFAULT": "current_timestamp() + INTERVAL 999 DAYS"},
            {
                "CURRENT_DEFAULT": "current_timestamp() + INTERVAL 999 DAYS",
                "EXISTS_DEFAULT": "timestamp'2026-08-31 00:00:00'",
            },
        ):
            expression = "current_timestamp() + INTERVAL 999 DAYS"
            target = schema(
                field("id", "long", False),
                field(
                    "created_at",
                    "timestamp",
                    False,
                    {**metadata, "comment": "unrelated metadata"},
                ),
            )

            canonical, errors = validate_schema._canonicalize_schema(target, "target")
            report = validate_schema.compare_schema_contract(
                schema(field("id", "long", False)),
                target,
                write_operation="delta-sql-insert-by-name",
            )
            generic_report = validate_schema.compare_schema_contract(
                schema(field("id", "long", False)),
                target,
            )
            rendered = json.dumps(report)

            self.assertEqual(errors, [])
            self.assertEqual(
                canonical["fields"][1]["metadata"],
                {
                    key: (
                        validate_schema.DEFAULT_PRESENT_SENTINEL
                        if key == "CURRENT_DEFAULT"
                        else True
                    )
                    for key in metadata
                },
            )
            self.assertEqual(report["decision"], "compatible")
            self.assertEqual(report["warnings"][0]["type"], "target_default_column_omitted")
            self.assertEqual(report["warnings"][0]["column"], "created_at")
            self.assertEqual(generic_report["decision"], "incompatible")
            self.assertEqual(generic_report["errors"][0]["type"], "missing_required_column")
            self.assertNotIn(expression, rendered)
            self.assertNotIn("unrelated metadata", json.dumps(canonical))

    def test_existence_default_alone_does_not_claim_a_current_insert_default(self) -> None:
        expression = "timestamp'2026-08-31 00:00:00'"
        target = schema(
            field("id", "long", False),
            field("created_at", "timestamp", False, {"EXISTS_DEFAULT": expression}),
        )

        canonical, errors = validate_schema._canonicalize_schema(target, "target")
        report = validate_schema.compare_schema_contract(
            schema(field("id", "long", False)),
            target,
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(errors, [])
        self.assertEqual(canonical["fields"][1]["metadata"], {"EXISTS_DEFAULT": True})
        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual(report["errors"][0]["type"], "missing_required_column")
        self.assertNotIn(expression, json.dumps(report))

    def test_invalid_current_default_metadata_does_not_authorize_json_omission(self) -> None:
        invalid_values: tuple[object, ...] = (False, None, "", " \t\n", 0, [], {})
        for invalid_value in invalid_values:
            target = schema(
                field("id", "long", False),
                field(
                    "created_at",
                    "timestamp",
                    False,
                    {
                        "CURRENT_DEFAULT": invalid_value,
                        "EXISTS_DEFAULT": "timestamp'2026-08-31 00:00:00'",
                    },
                ),
            )

            canonical, errors = validate_schema._canonicalize_schema(target, "target")
            report = validate_schema.compare_schema_contract(
                schema(field("id", "long", False)),
                target,
                write_operation="delta-sql-insert-by-name",
            )

            self.assertEqual(errors, [])
            self.assertEqual(canonical["fields"][1]["metadata"], {"EXISTS_DEFAULT": True})
            self.assertEqual(report["decision"], "incompatible")
            self.assertEqual(report["errors"][0]["type"], "missing_required_column")
            self.assertNotIn("CURRENT_DEFAULT", json.dumps(report))

    def test_delta_insert_by_name_does_not_accept_unrecognized_default_metadata(self) -> None:
        target = schema(
            field("id", "long", False),
            field("created_at", "timestamp", False, {"DEFAULT": "current_timestamp()"}),
        )

        report = validate_schema.compare_schema_contract(
            schema(field("id", "long", False)),
            target,
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual(report["errors"][0]["type"], "missing_required_column")
        self.assertNotIn("current_timestamp", json.dumps(report))

    def test_live_delta_insert_by_name_preserves_only_default_presence(self) -> None:
        expression = "uuid() || '-private-expression'"
        source = _Frame(schema(field("id", "long", False)))
        target = schema(
            field("id", "long", False),
            field("generated_id", "string", False, {"CURRENT_DEFAULT": expression}),
        )

        report = validate_schema.compare_live_schema(
            _Spark(target),
            source,
            "catalog.schema.target",
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "compatible")
        self.assertEqual(report["warnings"][0]["type"], "target_default_column_omitted")
        self.assertEqual(report["warnings"][0]["column"], "generated_id")
        self.assertNotIn(expression, json.dumps(report))

    def test_live_invalid_current_default_metadata_does_not_authorize_omission(self) -> None:
        source = _Frame(schema(field("id", "long", False)))
        for invalid_value in (False, None, "", " \t", 1, ["not", "an", "expression"]):
            target = schema(
                field("id", "long", False),
                field("generated_id", "string", False, {"CURRENT_DEFAULT": invalid_value}),
            )

            report = validate_schema.compare_live_schema(
                _Spark(target),
                source,
                "catalog.schema.target",
                write_operation="delta-sql-insert-by-name",
            )

            self.assertEqual(report["decision"], "incompatible")
            self.assertEqual(report["errors"][0]["type"], "missing_required_column")
            self.assertNotIn("CURRENT_DEFAULT", json.dumps(report))

    def test_delta_insert_by_name_rejects_nested_struct_reordering_at_exact_paths(self) -> None:
        source = schema(field("profile", struct_type(field("second", "integer"), field("first", "integer"))))
        target = schema(field("profile", struct_type(field("first", "integer"), field("second", "integer"))))

        report = validate_schema.compare_schema_contract(
            source,
            target,
            write_operation="delta-sql-insert-by-name",
        )

        order_errors = [
            error
            for error in report["errors"]
            if error["type"] == "delta_insert_by_name_nested_field_order_mismatch"
        ]
        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual({error["column"] for error in order_errors}, {"profile.first", "profile.second"})
        self.assertTrue(all(error["source_field"] != error["target_field"] for error in order_errors))
        self.assertTrue(all("reorder the source struct" in error["message"] for error in order_errors))

    def test_delta_insert_by_name_rejects_array_struct_reordering_at_exact_paths(self) -> None:
        source = schema(
            field("events", array_type(struct_type(field("second", "integer"), field("first", "integer"))))
        )
        target = schema(
            field("events", array_type(struct_type(field("first", "integer"), field("second", "integer"))))
        )

        report = validate_schema.compare_schema_contract(
            source,
            target,
            write_operation="delta-sql-insert-by-name",
        )

        order_paths = {
            error["column"]
            for error in report["errors"]
            if error["type"] == "delta_insert_by_name_nested_field_order_mismatch"
        }
        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual(order_paths, {"events[].first", "events[].second"})

    def test_delta_insert_by_name_accepts_array_struct_fields_in_target_order(self) -> None:
        event_type = array_type(
            struct_type(field("first", "integer", False), field("second", "string", False))
        )

        report = validate_schema.compare_schema_contract(
            schema(field("events", event_type, False)),
            schema(field("events", event_type, False)),
            write_operation="delta-sql-insert-by-name",
        )

        self.assertEqual(report["decision"], "exact")
        self.assertEqual(report["errors"], [])

    def test_delta_insert_by_name_cli_rejects_nested_array_struct_reordering(self) -> None:
        source = schema(
            field("events", array_type(struct_type(field("second", "integer"), field("first", "integer"))))
        )
        target = schema(
            field("events", array_type(struct_type(field("first", "integer"), field("second", "integer"))))
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.json"
            target_path = Path(temp_dir) / "target.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            target_path.write_text(json.dumps(target), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = validate_schema.main(
                    [
                        "compare",
                        "--source-schema",
                        str(source_path),
                        "--target-schema",
                        str(target_path),
                        "--write-operation",
                        "delta-sql-insert-by-name",
                        "--json",
                    ]
                )

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["write_operation"], "delta-sql-insert-by-name")
        self.assertEqual(
            {error["column"] for error in report["errors"]},
            {"events[].first", "events[].second"},
        )

    def test_rejects_case_insensitive_duplicates_at_top_and_nested_levels(self) -> None:
        top_level = schema(field("ID", "long"), field("id", "long"))
        nested = schema(
            field(
                "events",
                {
                    "type": "array",
                    "elementType": {
                        "type": "struct",
                        "fields": [field("Code", "string"), field("code", "string")],
                    },
                    "containsNull": False,
                },
            ),
            field(
                "attributes",
                {
                    "type": "map",
                    "keyType": "string",
                    "valueType": {
                        "type": "struct",
                        "fields": [field("Kind", "string"), field("kind", "string")],
                    },
                    "valueContainsNull": False,
                },
            ),
        )
        valid = schema(field("id", "long"))

        for invalid in (top_level, nested):
            report = validate_schema.compare_schema_contract(invalid, valid)
            self.assertEqual(report["decision"], "invalid")
            self.assertTrue(any(error["type"] == "duplicate_field_name" for error in report["errors"]))
        nested_report = validate_schema.compare_schema_contract(nested, valid)
        duplicate_paths = {error["column"] for error in nested_report["errors"] if error["type"] == "duplicate_field_name"}
        self.assertTrue({"events[].code", "attributes{value}.kind"}.issubset(duplicate_paths))

    def test_rejects_malformed_nullable_array_map_decimal_and_depth(self) -> None:
        missing_nullable = {"type": "struct", "fields": [{"name": "id", "type": "long"}]}
        non_boolean_nullable = {"type": "struct", "fields": [{"name": "id", "type": "long", "nullable": 1}]}
        missing_array_flag = schema(field("items", {"type": "array", "elementType": "long"}))
        missing_map_flag = schema(field("attributes", {"type": "map", "keyType": "string", "valueType": "long"}))
        invalid_decimal = schema(field("amount", "decimal(39,0)"))
        deep_type: object = "string"
        for index in range(validate_schema.MAX_SCHEMA_DEPTH + 1):
            deep_type = {
                "type": "array",
                "elementType": deep_type,
                "containsNull": False,
            }
        too_deep = schema(field("deep", deep_type))
        target = schema()

        for invalid in (missing_nullable, non_boolean_nullable, missing_array_flag, missing_map_flag, invalid_decimal, too_deep):
            report = validate_schema.compare_schema_contract(invalid, target)
            self.assertEqual(report["decision"], "invalid")
            self.assertTrue(report["errors"])

    def test_default_exact_rejects_widening_but_returns_a_cast_plan(self) -> None:
        report = validate_schema.compare_schema_contract(schema(field("id", "integer")), schema(field("id", "long")))

        self.assertEqual(report["decision"], "incompatible")
        self.assertEqual(report["casts"], [{"column": "id", "source_type": "integer", "target_type": "long", "action": "cast_to_target_type"}])

    def test_required_target_and_timestamp_contracts_still_fail_loudly(self) -> None:
        missing_required = validate_schema.compare_schema_contract(schema(), schema(field("business_key", "string", False)))
        timestamp_from_string = validate_schema.compare_schema_contract(
            schema(field("event_time", "string")),
            schema(field("event_time", "timestamp")),
        )
        timestamp_from_epoch = validate_schema.compare_schema_contract(
            schema(field("event_time", "long")),
            schema(field("event_time", "timestamp")),
            type_policy="spark-assignment",
        )

        self.assertEqual(missing_required["decision"], "incompatible")
        self.assertTrue(any(error["type"] == "missing_required_column" for error in missing_required["errors"]))
        self.assertEqual(timestamp_from_string["decision"], "incompatible")
        self.assertEqual(timestamp_from_epoch["decision"], "incompatible")

    def test_spark_assignment_and_delta_widening_are_separate_policies(self) -> None:
        spark_report = validate_schema.compare_schema_contract(
            schema(field("id", "long")),
            schema(field("id", "double")),
            type_policy="spark-assignment",
        )
        delta_unverified = validate_schema.compare_schema_contract(
            schema(field("id", "long")),
            schema(field("id", "integer")),
            type_policy="delta-type-widening-v4",
        )
        delta_conditional = validate_schema.compare_schema_contract(
            schema(field("id", "long")),
            schema(field("id", "integer")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
        )

        self.assertEqual(spark_report["decision"], "compatible")
        self.assertEqual(delta_unverified["decision"], "incompatible")
        self.assertEqual(delta_conditional["decision"], "conditional")

    def test_delta_decimal_rules_and_date_to_timestamp_ntz_are_exactly_scoped(self) -> None:
        decimal_conditional = validate_schema.compare_schema_contract(
            schema(field("amount", "decimal(12,3)")),
            schema(field("amount", "decimal(10,1)")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
        )
        decimal_loss = validate_schema.compare_schema_contract(
            schema(field("amount", "decimal(11,3)")),
            schema(field("amount", "decimal(10,1)")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
        )
        date_conditional = validate_schema.compare_schema_contract(
            schema(field("event_time", "timestamp_ntz")),
            schema(field("event_time", "date")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
        )
        float_to_decimal = validate_schema.compare_schema_contract(
            schema(field("amount", "float")),
            schema(field("amount", "decimal(10,2)")),
            type_policy="spark-assignment",
        )

        self.assertEqual(decimal_conditional["decision"], "conditional")
        self.assertEqual(decimal_loss["decision"], "incompatible")
        self.assertEqual(date_conditional["decision"], "conditional")
        self.assertEqual(float_to_decimal["decision"], "incompatible")

    def test_delta_integer_decimal_minimum_precision_includes_scale(self) -> None:
        rejected = validate_schema.compare_schema_contract(
            schema(field("id", "decimal(20,1)")),
            schema(field("id", "long")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
            manual_type_change=True,
        )
        boundary = validate_schema.compare_schema_contract(
            schema(field("id", "decimal(21,1)")),
            schema(field("id", "long")),
            type_policy="delta-type-widening-v4",
            delta_type_widening_enabled=True,
            delta_table_feature_enabled=True,
            automatic_schema_evolution_enabled=True,
            manual_type_change=True,
        )

        self.assertEqual(rejected["decision"], "incompatible")
        self.assertEqual(boundary["decision"], "conditional")

    def test_nested_field_array_and_map_nullability_use_source_to_target_paths(self) -> None:
        source = schema(
            field(
                "profile",
                {
                    "type": "struct",
                    "fields": [
                        field("email", "string", True),
                        field("tags", {"type": "array", "elementType": "string", "containsNull": True}, False),
                        field("attributes", {"type": "map", "keyType": "string", "valueType": "string", "valueContainsNull": True}, False),
                    ],
                },
                False,
            )
        )
        target = schema(
            field(
                "profile",
                {
                    "type": "struct",
                    "fields": [
                        field("email", "string", False),
                        field("tags", {"type": "array", "elementType": "string", "containsNull": False}, False),
                        field("attributes", {"type": "map", "keyType": "string", "valueType": "string", "valueContainsNull": False}, False),
                    ],
                },
                False,
            )
        )

        report = validate_schema.compare_schema_contract(source, target)
        error_paths = {error["column"] for error in report["errors"]}

        self.assertEqual(report["decision"], "incompatible")
        self.assertTrue({"profile.email", "profile.tags[]", "profile.attributes{value}"}.issubset(error_paths))
        self.assertTrue(all(error.get("direction") == "source->target" for error in report["errors"]))

    def test_nullability_relaxation_is_compatible_not_exact_at_all_levels(self) -> None:
        source = schema(
            field("id", "long", False),
            field(
                "profile",
                {
                    "type": "struct",
                    "fields": [
                        field("email", "string", False),
                        field("tags", {"type": "array", "elementType": "string", "containsNull": False}, False),
                        field("attributes", {"type": "map", "keyType": "string", "valueType": "string", "valueContainsNull": False}, False),
                    ],
                },
                False,
            ),
        )
        target = schema(
            field("id", "long", True),
            field(
                "profile",
                {
                    "type": "struct",
                    "fields": [
                        field("email", "string", True),
                        field("tags", {"type": "array", "elementType": "string", "containsNull": True}, True),
                        field("attributes", {"type": "map", "keyType": "string", "valueType": "string", "valueContainsNull": True}, True),
                    ],
                },
                True,
            ),
        )

        report = validate_schema.compare_schema_contract(source, target)
        warning_paths = {warning["column"] for warning in report["warnings"]}

        self.assertEqual(report["decision"], "compatible")
        self.assertEqual(report["errors"], [])
        self.assertTrue({"id", "profile", "profile.email", "profile.tags[]", "profile.attributes{value}"}.issubset(warning_paths))

    def test_live_permission_failure_is_not_misclassified_or_leaked(self) -> None:
        source = _Frame(schema(field("id", "long")))
        denied = PermissionError("Authorization: Bearer session-credential\nPy4JJavaError")

        report = validate_schema.compare_live_schema(_Spark(denied), source, "catalog.schema.target")

        self.assertEqual(report["decision"], "invalid")
        self.assertEqual(report["errors"][0]["type"], "target_schema_unavailable")
        rendered = json.dumps(report)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("Py4J", rendered)
        with self.assertRaises(validate_schema.TargetSchemaUnavailable):
            validate_schema.suggest_casts(_Spark(denied), source, "catalog.schema.target")

    def test_live_source_schema_failures_are_redacted_and_never_silent(self) -> None:
        target = _Spark(schema(field("id", "long")))
        for broken_source in (
            _BrokenSourceFrame(PermissionError("Authorization: Bearer source-session\nPy4JJavaError")),
            _BrokenJsonFrame(RuntimeError("Authorization: Bearer source-session\nPy4JJavaError")),
        ):
            report = validate_schema.compare_live_schema(target, broken_source, "catalog.schema.target")
            rendered = json.dumps(report)
            self.assertEqual(report["decision"], "invalid")
            self.assertEqual(report["errors"][0]["type"], "source_schema_unavailable")
            self.assertNotIn("Bearer", rendered)
            self.assertNotIn("Py4J", rendered)
            with self.assertRaises(validate_schema.SourceSchemaUnavailable):
                validate_schema.suggest_casts(target, broken_source, "catalog.schema.target")

    def test_live_not_found_is_narrowly_classified(self) -> None:
        report = validate_schema.compare_live_schema(
            _Spark(_MissingTableError("not found")),
            _Frame(schema(field("id", "long"))),
            "catalog.schema.target",
        )

        self.assertEqual(report["errors"][0]["type"], "table_not_found")

    def test_control_identifiers_do_not_reach_report(self) -> None:
        report = validate_schema.compare_schema_contract(
            schema(field("bad\nname", "long")),
            schema(),
            resolution="bad\nmode",
        )
        rendered = json.dumps(report)

        self.assertEqual(report["decision"], "invalid")
        self.assertNotIn("bad\\nname", rendered)
        self.assertNotIn("bad\nname", rendered)
        self.assertNotIn("bad\\nmode", rendered)
        self.assertNotIn("bad\nmode", rendered)

    def test_cli_invalid_json_and_size_exit_two_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid_path = root / "invalid.json"
            target_path = root / "target.json"
            oversized_path = root / "oversized.json"
            invalid_path.write_text("{", encoding="utf-8")
            target_path.write_text(json.dumps(schema()), encoding="utf-8")
            oversized_path.write_bytes(b" " * (validate_schema.MAX_SCHEMA_BYTES + 1))

            for source_path in (invalid_path, oversized_path):
                stdout = io.StringIO()
                with mock.patch.object(sys, "argv", ["validate_schema.py", "compare", "--source-schema", str(source_path), "--target-schema", str(target_path), "--json"]), contextlib.redirect_stdout(stdout):
                    exit_code = validate_schema.main()
                output = stdout.getvalue()
                self.assertEqual(exit_code, 2)
                self.assertIn('"decision": "invalid"', output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn(str(root), output)

    def test_json_cli_argument_errors_are_stable_and_value_free(self) -> None:
        invalid_argument_sets = (
            ["compare", "--resolution", "not-a-resolution\nraw", "--json"],
            ["compare", "--type-policy", "not-a-policy\nraw", "--json"],
            ["compare", "--unknown-option", "not-an-option\nraw", "--json"],
        )
        expected = validate_schema._invalid_cli_report()
        self.assertEqual(expected["schema_contract_version"], 2)
        self.assertNotIn("write_operation", expected)

        for arguments in invalid_argument_sets:
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "argv", ["validate_schema.py", *arguments]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = validate_schema.main()
            output = stdout.getvalue()
            self.assertEqual(exit_code, 2)
            self.assertEqual(json.loads(output), expected)
            self.assertEqual(stderr.getvalue(), "")
            self.assertNotIn("usage", output.casefold())
            self.assertNotIn("not-a-", output)

    def test_deterministic_fuzz_507_payloads_do_not_escape_the_contract(self) -> None:
        primitive_types = tuple(sorted(validate_schema.PRIMITIVE_TYPES))
        for index in range(507):
            mode = index % 9
            if mode == 0:
                payload = schema(field(f"field_{index}", primitive_types[index % len(primitive_types)], index % 2 == 0))
            elif mode == 1:
                payload = schema(field(f"amount_{index}", f"decimal({1 + index % 38},{index % 5})"))
            elif mode == 2:
                payload = schema(field("items", {"type": "array", "elementType": primitive_types[index % len(primitive_types)], "containsNull": bool(index % 2)}))
            elif mode == 3:
                payload = schema(field("attributes", {"type": "map", "keyType": "string", "valueType": primitive_types[index % len(primitive_types)], "valueContainsNull": bool(index % 2)}))
            elif mode == 4:
                payload = schema(field("nested", {"type": "struct", "fields": [field("child", "long", False)]}, False))
            elif mode == 5:
                payload = {"type": "struct", "fields": [{"name": f"bad_{index}", "type": "long", "nullable": index}]}
            elif mode == 6:
                payload = schema(field("dup", "long"), field("DUP", "long"))
            elif mode == 7:
                payload = schema(field("bad\nname", "long"))
            else:
                payload = schema(field("unsupported", "decimal(39,0)"))

            report = validate_schema.compare_schema_contract(payload, payload)
            rendered = json.dumps(report)
            self.assertIn(report["decision"], {"exact", "invalid"})
            self.assertNotIn("bad\nname", rendered)


if __name__ == "__main__":
    unittest.main()
