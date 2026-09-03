#!/usr/bin/env python3
"""Validate a bounded, explicit Spark schema contract before a Delta write.

The validator accepts only canonical ``StructType.jsonValue()`` JSON. It
validates schema shape, resolution mode, types, and nullability. It deliberately
does not validate temporal values or business-level SCD2 invariants.

Usage:
    python validate_schema.py compare --source-schema source.json --target-schema target.json
    python validate_schema.py compare --source-schema source.json --target-schema target.json --resolution by-position --strict
    python validate_schema.py compare --source-schema source.json --target-schema target.json --write-operation delta-sql-insert-by-name
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_SCHEMA_BYTES = 1_048_576
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_FIELDS = 10_000
MAX_IDENTIFIER_CHARS = 255
MAX_MESSAGE_CHARS = 240

PRIMITIVE_TYPES = frozenset(
    {
        "binary", "boolean", "byte", "date", "double", "float", "integer",
        "long", "short", "string", "timestamp", "timestamp_ntz",
    }
)
RESOLUTION_MODES = frozenset({"by-name", "by-position"})
WRITE_OPERATIONS = frozenset({"generic", "delta-sql-insert-by-name"})
TYPE_POLICIES = frozenset({"exact", "spark-assignment", "delta-type-widening-v4"})
DELTA_NESTED_POSITIONAL_RESOLUTION = "delta-nested-by-position"
CURRENT_DEFAULT_METADATA_KEY = "CURRENT_DEFAULT"
EXISTS_DEFAULT_METADATA_KEY = "EXISTS_DEFAULT"
DEFAULT_PRESENT_SENTINEL = "<present>"
DECIMAL_PATTERN = re.compile(r"decimal\(([1-9][0-9]*),(0|[1-9][0-9]*)\)\Z")
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(?:proxy-)?authorization\s*[:=]\s*(?:bearer|basic|dpop)?\s*[^\s,;]+",
)
AUTHORIZATION_WORD_PATTERN = re.compile(r"(?i)\b(?:proxy-)?authorization\b")
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
PY4J_PATTERN = re.compile(r"(?i)py4j[^\s:]*")

# Spark SQL ANSI source -> target assignment direction. This is intentionally
# distinct from Delta type widening, which changes target -> source.
SPARK_ASSIGNMENT_TARGETS: dict[str, frozenset[str]] = {
    "byte": frozenset({"byte", "short", "integer", "long", "decimal", "float", "double"}),
    "short": frozenset({"short", "integer", "long", "decimal", "float", "double"}),
    "integer": frozenset({"integer", "long", "decimal", "float", "double"}),
    "long": frozenset({"long", "decimal", "float", "double"}),
    "decimal": frozenset({"decimal", "float", "double"}),
    "float": frozenset({"float", "double"}),
    "double": frozenset({"double"}),
    "date": frozenset({"date", "timestamp_ntz", "timestamp"}),
    "timestamp_ntz": frozenset({"timestamp_ntz", "timestamp"}),
    "timestamp": frozenset({"timestamp"}),
    "string": frozenset({"string"}),
    "boolean": frozenset({"boolean"}),
    "binary": frozenset({"binary"}),
}

# Delta Lake 4.0 existing target -> incoming source widening direction.
DELTA_V4_WIDENING_TARGETS: dict[str, frozenset[str]] = {
    "byte": frozenset({"short", "integer", "long", "decimal", "double"}),
    "short": frozenset({"integer", "long", "decimal", "double"}),
    "integer": frozenset({"long", "decimal", "double"}),
    "long": frozenset({"decimal"}),
    "float": frozenset({"double"}),
    "date": frozenset({"timestamp_ntz"}),
}
INTEGER_DECIMAL_MIN_PRECISION = {"byte": 10, "short": 10, "integer": 10, "long": 20}
INTEGER_DECIMAL_MIN_INTEGER_DIGITS = {"byte": 3, "short": 5, "integer": 10, "long": 19}


class SchemaInputError(ValueError):
    """Schema payload was not a bounded canonical StructType JSON value."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("Schema input is invalid")
        self.errors = errors


class TargetTableNotFound(RuntimeError):
    """The table-not-found class was explicitly identified by Spark."""


class TargetSchemaUnavailable(RuntimeError):
    """A live target schema cannot be safely inspected."""

    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class


class SourceSchemaUnavailable(RuntimeError):
    """A live source DataFrame schema cannot be safely inspected."""

    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class


class _JsonCliArgumentError(ValueError):
    """A JSON-mode parse error that must not emit usage or raw arguments."""


@dataclass
class _SchemaBudget:
    fields: int = 0


@dataclass
class _Comparison:
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    casts: list[dict[str, Any]] = field(default_factory=list)
    conditional: bool = False
    non_exact: bool = False

    def error(self, issue_type: str, column: str, message: str, **details: str) -> None:
        entry: dict[str, Any] = {"type": issue_type, "column": column, "message": _safe_text(message)}
        entry.update({key: _safe_text(value) for key, value in details.items()})
        self.errors.append(entry)

    def warning(self, warning_type: str, column: str, message: str, **details: str) -> None:
        entry: dict[str, Any] = {"type": warning_type, "column": column, "message": _safe_text(message)}
        entry.update({key: _safe_text(value) for key, value in details.items()})
        self.warnings.append(entry)

    def cast(self, column: str, source_type: Any, target_type: Any) -> None:
        entry = {
            "column": column,
            "source_type": _type_repr(source_type),
            "target_type": _type_repr(target_type),
            "action": "cast_to_target_type",
        }
        if entry not in self.casts:
            self.casts.append(entry)


def _safe_text(value: Any, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Bound display text and redact controls, credentials, and Py4J details."""
    text = str(value)
    text = CONTROL_PATTERN.sub(" ", text)
    text = AUTHORIZATION_PATTERN.sub("<redacted-credential>", text)
    text = BEARER_PATTERN.sub("<redacted-credential>", text)
    text = AUTHORIZATION_WORD_PATTERN.sub("<redacted-auth>", text)
    text = PY4J_PATTERN.sub("<spark-runtime>", text)
    text = " ".join(text.split())
    return f"{text[:limit - 3]}..." if len(text) > limit else text


def _safe_exception_class(exc: BaseException) -> str:
    raw = f"{exc.__class__.__module__}.{exc.__class__.__name__}"
    if "py4j" in raw.casefold():
        return "spark_runtime_error"
    return _safe_text(re.sub(r"[^A-Za-z0-9_.]", "_", raw), limit=80) or "unknown_error"


def _path_join(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _invalid(side: str, issue_type: str, column: str, message: str) -> dict[str, Any]:
    return {"type": issue_type, "schema": side, "column": column, "message": _safe_text(message)}


def _validate_name(raw_name: Any, side: str, parent: str, errors: list[dict[str, Any]]) -> str | None:
    if type(raw_name) is not str:
        errors.append(_invalid(side, "invalid_field_name", parent or "<root>", "Field name must be a string"))
        return None
    if not raw_name or len(raw_name) > MAX_IDENTIFIER_CHARS or CONTROL_PATTERN.search(raw_name):
        errors.append(
            _invalid(
                side,
                "invalid_field_name",
                parent or "<root>",
                "Field name must be non-empty, bounded, and free of control characters",
            )
        )
        return None
    return raw_name


def _decimal_parts(type_spec: Any) -> tuple[int, int] | None:
    if type(type_spec) is not str:
        return None
    match = DECIMAL_PATTERN.fullmatch(type_spec)
    return (int(match.group(1)), int(match.group(2))) if match is not None else None


def _type_kind(type_spec: Any) -> str:
    if type(type_spec) is str:
        return "decimal" if _decimal_parts(type_spec) is not None else type_spec
    return type_spec["type"]


def _type_repr(type_spec: Any) -> str:
    if type(type_spec) is str:
        return type_spec
    kind = type_spec["type"]
    if kind == "array":
        return f"array<{_type_repr(type_spec['elementType'])}>"
    if kind == "map":
        return f"map<{_type_repr(type_spec['keyType'])},{_type_repr(type_spec['valueType'])}>"
    if kind == "struct":
        return "struct<" + ",".join(
            f"{field['name']}:{_type_repr(field['type'])}" for field in type_spec["fields"]
        ) + ">"
    return kind


def _canonicalize_fields(
    raw_fields: Any,
    side: str,
    parent: str,
    depth: int,
    budget: _SchemaBudget,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if type(raw_fields) is not list:
        errors.append(_invalid(side, "invalid_fields", parent or "<root>", "Struct fields must be a JSON list"))
        return None
    canonical_fields: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw_field in raw_fields:
        budget.fields += 1
        if budget.fields > MAX_SCHEMA_FIELDS:
            errors.append(_invalid(side, "schema_field_limit_exceeded", parent or "<root>", f"Schema exceeds the maximum of {MAX_SCHEMA_FIELDS} fields"))
            return None
        if type(raw_field) is not dict:
            errors.append(_invalid(side, "invalid_field", parent or "<root>", "Each field must be a JSON object"))
            continue
        if not {"name", "type", "nullable"}.issubset(raw_field) or not set(raw_field).issubset({"name", "type", "nullable", "metadata"}):
            errors.append(_invalid(side, "invalid_field_shape", parent or "<root>", "A field must contain only name, type, nullable, and optional metadata"))
            continue
        name = _validate_name(raw_field.get("name"), side, parent, errors)
        if name is None:
            continue
        field_path = _path_join(parent, name)
        if name.casefold() in seen_names:
            errors.append(_invalid(side, "duplicate_field_name", field_path, "Struct field names must be unique case-insensitively"))
            continue
        seen_names.add(name.casefold())
        if type(raw_field.get("nullable")) is not bool:
            errors.append(_invalid(side, "invalid_nullable", field_path, "Field nullable must be a JSON boolean"))
            continue
        if "metadata" in raw_field and type(raw_field["metadata"]) is not dict:
            errors.append(_invalid(side, "invalid_metadata", field_path, "Field metadata must be a JSON object"))
            continue
        raw_metadata = raw_field.get("metadata", {})
        default_metadata: dict[str, bool | str] = {}
        current_default = raw_metadata.get(CURRENT_DEFAULT_METADATA_KEY)
        if type(current_default) is str and bool(current_default.strip()):
            default_metadata[CURRENT_DEFAULT_METADATA_KEY] = DEFAULT_PRESENT_SENTINEL
        if EXISTS_DEFAULT_METADATA_KEY in raw_metadata:
            default_metadata[EXISTS_DEFAULT_METADATA_KEY] = True
        canonical_type = _canonicalize_type(raw_field.get("type"), side, field_path, depth + 1, budget, errors)
        if canonical_type is not None:
            canonical_field = {"name": name, "type": canonical_type, "nullable": raw_field["nullable"]}
            if default_metadata:
                canonical_field["metadata"] = default_metadata
            canonical_fields.append(canonical_field)
    return canonical_fields


def _canonicalize_type(
    raw_type: Any,
    side: str,
    path: str,
    depth: int,
    budget: _SchemaBudget,
    errors: list[dict[str, Any]],
) -> Any | None:
    if depth > MAX_SCHEMA_DEPTH:
        errors.append(_invalid(side, "schema_depth_limit_exceeded", path, f"Schema exceeds the maximum nesting depth of {MAX_SCHEMA_DEPTH}"))
        return None
    if type(raw_type) is str:
        decimal = _decimal_parts(raw_type)
        if decimal is not None:
            precision, scale = decimal
            if precision > 38 or scale > precision:
                errors.append(_invalid(side, "invalid_decimal", path, "Decimal precision must be 1 through 38 and scale must be between 0 and precision"))
                return None
            return raw_type
        if raw_type in PRIMITIVE_TYPES:
            return raw_type
        errors.append(_invalid(side, "unsupported_type", path, "Type must be a supported canonical Spark primitive or decimal(p,s)"))
        return None
    if type(raw_type) is not dict or type(raw_type.get("type")) is not str:
        errors.append(_invalid(side, "invalid_type", path, "Type must be a canonical Spark type string or object"))
        return None
    kind = raw_type["type"]
    if kind == "struct":
        if set(raw_type) != {"type", "fields"}:
            errors.append(_invalid(side, "invalid_struct_shape", path, "Struct type must contain exactly type and fields"))
            return None
        fields = _canonicalize_fields(raw_type.get("fields"), side, path, depth, budget, errors)
        return {"type": "struct", "fields": fields} if fields is not None else None
    if kind == "array":
        if set(raw_type) != {"type", "elementType", "containsNull"}:
            errors.append(_invalid(side, "invalid_array_shape", path, "Array type must contain exactly type, elementType, and containsNull"))
            return None
        if type(raw_type.get("containsNull")) is not bool:
            errors.append(_invalid(side, "invalid_array_nullability", path, "Array containsNull must be a JSON boolean"))
            return None
        element_type = _canonicalize_type(raw_type.get("elementType"), side, f"{path}[]", depth + 1, budget, errors)
        return {"type": "array", "elementType": element_type, "containsNull": raw_type["containsNull"]} if element_type is not None else None
    if kind == "map":
        if set(raw_type) != {"type", "keyType", "valueType", "valueContainsNull"}:
            errors.append(_invalid(side, "invalid_map_shape", path, "Map type must contain exactly type, keyType, valueType, and valueContainsNull"))
            return None
        if type(raw_type.get("valueContainsNull")) is not bool:
            errors.append(_invalid(side, "invalid_map_nullability", path, "Map valueContainsNull must be a JSON boolean"))
            return None
        key_type = _canonicalize_type(raw_type.get("keyType"), side, f"{path}{{key}}", depth + 1, budget, errors)
        value_type = _canonicalize_type(raw_type.get("valueType"), side, f"{path}{{value}}", depth + 1, budget, errors)
        if key_type is None or value_type is None:
            return None
        return {"type": "map", "keyType": key_type, "valueType": value_type, "valueContainsNull": raw_type["valueContainsNull"]}
    errors.append(_invalid(side, "unsupported_type", path, "Type object must be struct, array, or map"))
    return None


def _canonicalize_schema(payload: Any, side: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if type(payload) is not dict or set(payload) != {"type", "fields"} or payload.get("type") != "struct":
        errors.append(_invalid(side, "invalid_root_schema", "<root>", "Schema must be a canonical StructType JSON object with exactly type=struct and fields"))
        return None, errors
    fields = _canonicalize_fields(payload.get("fields"), side, "", 0, _SchemaBudget(), errors)
    return ({"type": "struct", "fields": fields} if fields is not None and not errors else None), errors


def _normalize_schema_payload(payload: Any) -> dict[str, Any]:
    schema, errors = _canonicalize_schema(payload, "schema")
    if schema is None or errors:
        raise SchemaInputError(errors)
    return schema


def _read_schema_file(schema_path: Path, side: str) -> dict[str, Any]:
    try:
        with schema_path.open("rb") as schema_file:
            payload_bytes = schema_file.read(MAX_SCHEMA_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise SchemaInputError([_invalid(side, "schema_file_unavailable", "<root>", "Schema file could not be read")]) from exc
    if len(payload_bytes) > MAX_SCHEMA_BYTES:
        raise SchemaInputError([_invalid(side, "schema_file_too_large", "<root>", f"Schema file exceeds the {MAX_SCHEMA_BYTES}-byte limit")])
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SchemaInputError([_invalid(side, "invalid_json", "<root>", "Schema file must contain valid UTF-8 JSON")]) from exc
    schema, errors = _canonicalize_schema(payload, side)
    if schema is None or errors:
        raise SchemaInputError(errors)
    return schema


def _load_schema_file(schema_path: Path) -> dict[str, Any]:
    """Load a canonical schema file for backwards-compatible local callers."""
    return _read_schema_file(schema_path, "schema")


def _schema_from_pyspark(struct_type: Any) -> dict[str, Any]:
    return _normalize_schema_payload(struct_type.jsonValue())


def _decimal_assignment_safe(source_type: Any, target_type: Any) -> bool:
    source_decimal = _decimal_parts(source_type)
    target_decimal = _decimal_parts(target_type)
    if source_decimal is None or target_decimal is None:
        return False
    source_precision, source_scale = source_decimal
    target_precision, target_scale = target_decimal
    return target_scale >= source_scale and target_precision - target_scale >= source_precision - source_scale


def _spark_assignment_supported(source_type: Any, target_type: Any) -> bool:
    source_kind = _type_kind(source_type)
    target_kind = _type_kind(target_type)
    if source_kind == target_kind == "decimal":
        return _decimal_assignment_safe(source_type, target_type)
    return target_kind in SPARK_ASSIGNMENT_TARGETS.get(source_kind, frozenset())


def _delta_v4_widening_supported(target_type: Any, source_type: Any) -> bool:
    target_kind = _type_kind(target_type)
    source_kind = _type_kind(source_type)
    if target_kind == source_kind == "decimal":
        target_decimal = _decimal_parts(target_type)
        source_decimal = _decimal_parts(source_type)
        if target_decimal is None or source_decimal is None:
            return False
        target_precision, target_scale = target_decimal
        source_precision, source_scale = source_decimal
        return source_precision - target_precision >= source_scale - target_scale >= 0
    if source_kind not in DELTA_V4_WIDENING_TARGETS.get(target_kind, frozenset()):
        return False
    if source_kind != "decimal":
        return True
    source_decimal = _decimal_parts(source_type)
    if source_decimal is None:
        return False
    source_precision, source_scale = source_decimal
    minimum_precision = INTEGER_DECIMAL_MIN_PRECISION[target_kind]
    return (
        source_precision >= minimum_precision + source_scale
        and source_precision - source_scale >= INTEGER_DECIMAL_MIN_INTEGER_DIGITS[target_kind]
    )


def _delta_manual_type_change_required(target_type: Any, source_type: Any) -> bool:
    return _type_kind(target_type) in INTEGER_DECIMAL_MIN_PRECISION and _type_kind(source_type) in {"decimal", "double"}


def _compare_scalar_type(
    source_type: Any,
    target_type: Any,
    column: str,
    comparison: _Comparison,
    type_policy: str,
    delta_type_widening_enabled: bool,
    delta_table_feature_enabled: bool,
    automatic_schema_evolution_enabled: bool,
    manual_type_change: bool,
) -> None:
    if source_type == target_type:
        return
    source_repr, target_repr = _type_repr(source_type), _type_repr(target_type)
    if type_policy == "exact":
        comparison.error("type_mismatch", column, f"Source type {source_repr} requires an explicit cast to target type {target_repr}", source_type=source_repr, target_type=target_repr)
        comparison.cast(column, source_type, target_type)
        return
    if _spark_assignment_supported(source_type, target_type):
        comparison.non_exact = True
        comparison.warning("spark_assignment_compatible", column, f"Spark assignment policy permits source type {source_repr} to target type {target_repr}", source_type=source_repr, target_type=target_repr)
        return
    if type_policy == "delta-type-widening-v4" and _delta_v4_widening_supported(target_type, source_type):
        missing_conditions: list[str] = []
        if not delta_type_widening_enabled:
            missing_conditions.append("delta.enableTypeWidening=true")
        if not delta_table_feature_enabled:
            missing_conditions.append("Delta table feature typeWidening")
        if not automatic_schema_evolution_enabled:
            missing_conditions.append("operation-level automatic schema evolution")
        manual_required = _delta_manual_type_change_required(target_type, source_type)
        if manual_required and not manual_type_change:
            missing_conditions.append("manual ALTER COLUMN for integer-to-decimal/double widening")
        if missing_conditions:
            comparison.error("delta_type_widening_unverified", column, "Delta widening is not ready until required conditions are explicitly verified", required_conditions=", ".join(missing_conditions), source_type=source_repr, target_type=target_repr)
            return
        comparison.conditional, comparison.non_exact = True, True
        note = "manual type change must complete before the write" if manual_required else "verify the live table and write operation still carry the declared capability"
        comparison.warning("delta_type_widening_conditional", column, f"Delta 4.0 supports widening target type {target_repr} to source type {source_repr}; {note}", source_type=source_repr, target_type=target_repr)
        return
    comparison.error("type_mismatch", column, f"Source type {source_repr} is not compatible with target type {target_repr} under {type_policy}", source_type=source_repr, target_type=target_repr)
    comparison.cast(column, source_type, target_type)


def _compare_type(
    source_type: Any,
    target_type: Any,
    column: str,
    comparison: _Comparison,
    resolution: str,
    strict: bool,
    type_policy: str,
    delta_type_widening_enabled: bool,
    delta_table_feature_enabled: bool,
    automatic_schema_evolution_enabled: bool,
    manual_type_change: bool,
) -> None:
    source_kind, target_kind = _type_kind(source_type), _type_kind(target_type)
    if source_kind != target_kind:
        _compare_scalar_type(source_type, target_type, column, comparison, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        return
    if source_kind == "struct":
        _compare_struct(source_type, target_type, column, comparison, resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        return
    if source_kind == "array":
        if source_type["containsNull"] and not target_type["containsNull"]:
            comparison.error("array_element_nullability_mismatch", f"{column}[]", "Source array elements may be NULL but target array elements are NOT NULL", direction="source->target")
        elif not source_type["containsNull"] and target_type["containsNull"]:
            comparison.non_exact = True
            comparison.warning("array_element_nullability_relaxed", f"{column}[]", "Target array elements permit NULL where source array elements do not", direction="source->target")
        _compare_type(source_type["elementType"], target_type["elementType"], f"{column}[]", comparison, resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        return
    if source_kind == "map":
        if source_type["valueContainsNull"] and not target_type["valueContainsNull"]:
            comparison.error("map_value_nullability_mismatch", f"{column}{{value}}", "Source map values may be NULL but target map values are NOT NULL", direction="source->target")
        elif not source_type["valueContainsNull"] and target_type["valueContainsNull"]:
            comparison.non_exact = True
            comparison.warning("map_value_nullability_relaxed", f"{column}{{value}}", "Target map values permit NULL where source map values do not", direction="source->target")
        _compare_type(source_type["keyType"], target_type["keyType"], f"{column}{{key}}", comparison, resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        _compare_type(source_type["valueType"], target_type["valueType"], f"{column}{{value}}", comparison, resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        return
    _compare_scalar_type(source_type, target_type, column, comparison, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)


def _compare_field(
    source_field: dict[str, Any],
    target_field: dict[str, Any],
    column: str,
    comparison: _Comparison,
    resolution: str,
    strict: bool,
    type_policy: str,
    delta_type_widening_enabled: bool,
    delta_table_feature_enabled: bool,
    automatic_schema_evolution_enabled: bool,
    manual_type_change: bool,
) -> None:
    if source_field["nullable"] and not target_field["nullable"]:
        comparison.error("nullability_mismatch", column, "Source field may be NULL but target field is NOT NULL", direction="source->target")
    elif not source_field["nullable"] and target_field["nullable"]:
        comparison.non_exact = True
        comparison.warning("nullability_relaxed", column, "Target field permits NULL where source field does not", direction="source->target")
    _compare_type(source_field["type"], target_field["type"], column, comparison, resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)


def _compare_struct(
    source_schema: dict[str, Any],
    target_schema: dict[str, Any],
    parent: str,
    comparison: _Comparison,
    resolution: str,
    strict: bool,
    type_policy: str,
    delta_type_widening_enabled: bool,
    delta_table_feature_enabled: bool,
    automatic_schema_evolution_enabled: bool,
    manual_type_change: bool,
    nested_resolution: str | None = None,
) -> None:
    source_fields, target_fields = source_schema["fields"], target_schema["fields"]
    child_resolution = nested_resolution or resolution
    if resolution in {"by-position", DELTA_NESTED_POSITIONAL_RESOLUTION}:
        if len(source_fields) != len(target_fields):
            if resolution == DELTA_NESTED_POSITIONAL_RESOLUTION:
                comparison.error(
                    "delta_insert_by_name_nested_field_count_mismatch",
                    parent or "<root>",
                    "Delta SQL INSERT BY NAME resolves nested struct fields by position; project the same nested fields in target order",
                    direction="source->target",
                )
            else:
                comparison.error("field_count_mismatch", parent or "<root>", "By-position resolution requires the same number of source and target fields", direction="source->target")
        for index, target_field in enumerate(target_fields):
            if index >= len(source_fields):
                continue
            source_field = source_fields[index]
            column = _path_join(parent, target_field["name"])
            if source_field["name"] != target_field["name"]:
                if resolution == DELTA_NESTED_POSITIONAL_RESOLUTION:
                    comparison.error(
                        "delta_insert_by_name_nested_field_order_mismatch",
                        column,
                        "Delta SQL INSERT BY NAME resolves nested struct fields by position; reorder the source struct to match the target field at this path",
                        direction="source->target",
                        position=str(index + 1),
                        source_field=source_field["name"],
                        target_field=target_field["name"],
                    )
                else:
                    comparison.error("positional_field_name_mismatch", column, "By-position resolution requires exact field names in the same order", direction="source->target")
            _compare_field(source_field, target_field, column, comparison, child_resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
        return
    source_by_name = {field["name"].casefold(): field for field in source_fields}
    target_by_name = {field["name"].casefold(): field for field in target_fields}
    source_order = [field["name"].casefold() for field in source_fields]
    target_order = [field["name"].casefold() for field in target_fields]
    if len(source_order) == len(target_order) and set(source_order) == set(target_order) and source_order != target_order:
        comparison.non_exact = True
        comparison.warning(
            "by_name_reordered_fields",
            parent or "<root>",
            "By-name resolution permits this field reordering; keep the write explicitly name-resolving",
            direction="source->target",
        )
    for target_field in target_fields:
        column = _path_join(parent, target_field["name"])
        source_field = source_by_name.get(target_field["name"].casefold())
        if source_field is None:
            if (
                nested_resolution == DELTA_NESTED_POSITIONAL_RESOLUTION
                and CURRENT_DEFAULT_METADATA_KEY in target_field.get("metadata", {})
            ):
                comparison.non_exact = True
                comparison.warning(
                    "target_default_column_omitted",
                    column,
                    "Source omits a target column with a recognized default; Delta SQL INSERT BY NAME will use that default",
                    direction="source->target",
                )
            elif target_field["nullable"]:
                comparison.non_exact = True
                comparison.warning("nullable_target_column_omitted", column, "Source omits a nullable target field; the write must use an explicit named projection", direction="source->target")
            else:
                comparison.error("missing_required_column", column, "Required target field is missing from source schema", direction="source->target")
            continue
        if source_field["name"] != target_field["name"]:
            comparison.non_exact = True
            comparison.warning("case_normalized_field_match", column, "By-name resolution matched field names case-insensitively; use an explicit projection", direction="source->target")
        _compare_field(source_field, target_field, column, comparison, child_resolution, strict, type_policy, delta_type_widening_enabled, delta_table_feature_enabled, automatic_schema_evolution_enabled, manual_type_change)
    for source_field in source_fields:
        if source_field["name"].casefold() in target_by_name:
            continue
        column = _path_join(parent, source_field["name"])
        if strict:
            comparison.error("extra_source_column", column, "Source field is not present in target schema", direction="source->target")
        else:
            comparison.non_exact = True
            comparison.warning("source_only_column", column, "Source-only field requires an explicit named projection before writing", direction="source->target")


def _option_errors(options: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if type(options["resolution"]) is not str or options["resolution"] not in RESOLUTION_MODES:
        errors.append(_invalid("options", "invalid_resolution", "<root>", "Resolution must be by-name or by-position"))
    if type(options["write_operation"]) is not str or options["write_operation"] not in WRITE_OPERATIONS:
        errors.append(
            _invalid(
                "options",
                "invalid_write_operation",
                "<root>",
                "Write operation must be generic or delta-sql-insert-by-name",
            )
        )
    elif options["write_operation"] == "delta-sql-insert-by-name" and options["resolution"] != "by-name":
        errors.append(
            _invalid(
                "options",
                "write_operation_resolution_conflict",
                "<root>",
                "Delta SQL INSERT BY NAME requires by-name top-level resolution",
            )
        )
    if type(options["type_policy"]) is not str or options["type_policy"] not in TYPE_POLICIES:
        errors.append(_invalid("options", "invalid_type_policy", "<root>", "Type policy must be exact, spark-assignment, or delta-type-widening-v4"))
    for name in ("strict", "delta_type_widening_enabled", "delta_table_feature_enabled", "automatic_schema_evolution_enabled", "manual_type_change"):
        if type(options[name]) is not bool:
            errors.append(_invalid("options", "invalid_option", "<root>", f"{name} must be a boolean"))
    return errors


def _report(
    decision: str,
    resolution: str,
    type_policy: str,
    *,
    write_operation: str = "generic",
    comparison: _Comparison | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    comparison = comparison or _Comparison()
    report = {
        "schema_contract_version": 2,
        "decision": decision,
        "resolution": _safe_text(resolution, limit=80),
        "type_policy": _safe_text(type_policy, limit=80),
        "errors": errors if errors is not None else comparison.errors,
        "casts": comparison.casts,
        "warnings": comparison.warnings,
    }
    if write_operation != "generic":
        report["schema_contract_version"] = 3
        report["write_operation"] = _safe_text(write_operation, limit=80)
    return report


def compare_schema_contract(
    source_schema: Any,
    target_schema: Any,
    *,
    resolution: str = "by-name",
    write_operation: str = "generic",
    strict: bool = False,
    type_policy: str = "exact",
    delta_type_widening_enabled: bool = False,
    delta_table_feature_enabled: bool = False,
    automatic_schema_evolution_enabled: bool = False,
    manual_type_change: bool = False,
) -> dict[str, Any]:
    """Return exact, compatible, conditional, incompatible, or invalid.

    The default ``exact`` policy returns cast plans instead of assuming implicit
    coercion. Delta 4.0 widening remains conditional even when all supplied
    capability flags are true, because the live write must verify them.
    """
    options = {
        "resolution": resolution,
        "write_operation": write_operation,
        "strict": strict,
        "type_policy": type_policy,
        "delta_type_widening_enabled": delta_type_widening_enabled,
        "delta_table_feature_enabled": delta_table_feature_enabled,
        "automatic_schema_evolution_enabled": automatic_schema_evolution_enabled,
        "manual_type_change": manual_type_change,
    }
    option_errors = _option_errors(options)
    source, source_errors = _canonicalize_schema(source_schema, "source")
    target, target_errors = _canonicalize_schema(target_schema, "target")
    input_errors = option_errors + source_errors + target_errors
    if input_errors or source is None or target is None:
        return _report(
            "invalid",
            str(resolution),
            str(type_policy),
            write_operation=str(write_operation),
            errors=input_errors,
        )
    comparison = _Comparison()
    nested_resolution = (
        DELTA_NESTED_POSITIONAL_RESOLUTION
        if write_operation == "delta-sql-insert-by-name"
        else None
    )
    operation_strict = strict or write_operation == "delta-sql-insert-by-name"
    _compare_struct(
        source,
        target,
        "",
        comparison,
        resolution,
        operation_strict,
        type_policy,
        delta_type_widening_enabled,
        delta_table_feature_enabled,
        automatic_schema_evolution_enabled,
        manual_type_change,
        nested_resolution,
    )
    decision = "incompatible" if comparison.errors else "conditional" if comparison.conditional else "compatible" if comparison.non_exact else "exact"
    return _report(
        decision,
        resolution,
        type_policy,
        write_operation=write_operation,
        comparison=comparison,
    )


def _blocking_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
    errors = list(report["errors"])
    if report["decision"] == "conditional":
        errors.append({"type": "conditional_schema_contract", "column": "<root>", "message": "Schema contract is conditional and must be explicitly verified before writing"})
    return errors


def validate_schema_definition(
    source_schema: dict[str, Any],
    target_schema: dict[str, Any],
    strict: bool = False,
    field_path: str = "",
    *,
    resolution: str = "by-name",
    write_operation: str = "generic",
    type_policy: str = "exact",
    delta_type_widening_enabled: bool = False,
    delta_table_feature_enabled: bool = False,
    automatic_schema_evolution_enabled: bool = False,
    manual_type_change: bool = False,
) -> list[dict[str, Any]]:
    """Return legacy blocking errors after complete root-schema validation."""
    del field_path
    report = compare_schema_contract(source_schema, target_schema, resolution=resolution, write_operation=write_operation, strict=strict, type_policy=type_policy, delta_type_widening_enabled=delta_type_widening_enabled, delta_table_feature_enabled=delta_table_feature_enabled, automatic_schema_evolution_enabled=automatic_schema_evolution_enabled, manual_type_change=manual_type_change)
    return _blocking_errors(report)


def _is_explicit_table_not_found(exc: BaseException) -> bool:
    get_error_class = getattr(exc, "getErrorClass", None)
    if not callable(get_error_class):
        return False
    try:
        return get_error_class() == "TABLE_OR_VIEW_NOT_FOUND"
    except Exception:  # pragma: no cover - third-party exception boundary
        return False


def _target_schema_from_live_table(spark: Any, target_table: str) -> dict[str, Any]:
    try:
        return _schema_from_pyspark(spark.table(target_table).schema)
    except SchemaInputError:
        raise
    except Exception as exc:
        if _is_explicit_table_not_found(exc):
            raise TargetTableNotFound("Target table was not found") from exc
        error_class, message = _safe_exception_class(exc), _safe_text(exc)
        raise TargetSchemaUnavailable(error_class, f"Target schema is unavailable: {message or 'no safe detail available'}") from exc


def _source_schema_from_live_dataframe(df: Any) -> dict[str, Any]:
    """Read a DataFrame schema without leaking third-party exception details."""
    try:
        return _schema_from_pyspark(df.schema)
    except SchemaInputError:
        raise
    except Exception as exc:
        error_class, message = _safe_exception_class(exc), _safe_text(exc)
        raise SourceSchemaUnavailable(error_class, f"Source schema is unavailable: {message or 'no safe detail available'}") from exc


def compare_live_schema(
    spark: Any,
    df: Any,
    target_table: str,
    *,
    resolution: str = "by-name",
    write_operation: str = "generic",
    strict: bool = False,
    type_policy: str = "exact",
    delta_type_widening_enabled: bool = False,
    delta_table_feature_enabled: bool = False,
    automatic_schema_evolution_enabled: bool = False,
    manual_type_change: bool = False,
) -> dict[str, Any]:
    """Compare a live DataFrame against a target table without broad errors."""
    try:
        target_schema = _target_schema_from_live_table(spark, target_table)
    except TargetTableNotFound:
        return _report("invalid", resolution, type_policy, write_operation=write_operation, errors=[{"type": "table_not_found", "column": "<root>", "message": "Target table was not found"}])
    except TargetSchemaUnavailable as exc:
        return _report("invalid", resolution, type_policy, write_operation=write_operation, errors=[{"type": "target_schema_unavailable", "column": "<root>", "error_class": exc.error_class, "message": _safe_text(exc)}])
    except SchemaInputError as exc:
        return _report("invalid", resolution, type_policy, write_operation=write_operation, errors=exc.errors)
    try:
        source_schema = _source_schema_from_live_dataframe(df)
    except SourceSchemaUnavailable as exc:
        errors = [{"type": "source_schema_unavailable", "column": "<root>", "error_class": exc.error_class, "message": _safe_text(exc)}]
        return _report("invalid", resolution, type_policy, write_operation=write_operation, errors=errors)
    except SchemaInputError as exc:
        errors = exc.errors
        return _report("invalid", resolution, type_policy, write_operation=write_operation, errors=errors)
    return compare_schema_contract(source_schema, target_schema, resolution=resolution, write_operation=write_operation, strict=strict, type_policy=type_policy, delta_type_widening_enabled=delta_type_widening_enabled, delta_table_feature_enabled=delta_table_feature_enabled, automatic_schema_evolution_enabled=automatic_schema_evolution_enabled, manual_type_change=manual_type_change)


def validate_schema(
    spark: Any,
    df: Any,
    target_table: str,
    strict: bool = False,
    *,
    write_operation: str = "generic",
) -> list[dict[str, Any]]:
    """Return notebook-compatible blocking errors for a live schema contract."""
    return _blocking_errors(
        compare_live_schema(
            spark,
            df,
            target_table,
            strict=strict,
            write_operation=write_operation,
        )
    )


def suggest_casts_from_schema_definition(source_schema: dict[str, Any], target_schema: dict[str, Any]) -> dict[str, str]:
    """Return actionable top-level casts, raising for invalid schema input."""
    report = compare_schema_contract(source_schema, target_schema)
    if report["decision"] == "invalid":
        raise SchemaInputError(report["errors"])
    return {cast["column"]: cast["target_type"] for cast in report["casts"] if "." not in cast["column"] and "[" not in cast["column"] and "{" not in cast["column"]}


def suggest_casts(spark: Any, df: Any, target_table: str) -> dict[str, str]:
    """Suggest casts, never silently returning an empty result for unavailable targets."""
    source_schema = _source_schema_from_live_dataframe(df)
    target_schema = _target_schema_from_live_table(spark, target_table)
    return suggest_casts_from_schema_definition(source_schema, target_schema)


def apply_casts(df: Any, casts: dict[str, str]) -> Any:
    """Apply top-level cast suggestions to a DataFrame."""
    functions_module = importlib.import_module("pyspark.sql.functions")
    for column_name, cast_type in casts.items():
        df = df.withColumn(column_name, functions_module.col(column_name).cast(cast_type))
    return df


def print_validation_report(report_or_errors: dict[str, Any] | list[dict[str, Any]]) -> None:
    """Print a safe report; conditional is deliberately not rendered as pass."""
    report = report_or_errors if isinstance(report_or_errors, dict) else {"decision": "exact" if not report_or_errors else "incompatible", "errors": report_or_errors, "warnings": [], "casts": []}
    print(f"{str(report['decision']).upper()}: schema contract")
    for error in report["errors"]:
        print(f"[{error['type'].upper()}] {error.get('column', '<root>')}: {error['message']}")
    for warning in report["warnings"]:
        print(f"[WARNING:{warning['type'].upper()}] {warning.get('column', '<root>')}: {warning['message']}")
    if report["casts"]:
        print("Required casts:")
        for cast in report["casts"]:
            print(f"  {cast['column']}: {cast['source_type']} -> {cast['target_type']}")


class _SchemaArgumentParser(argparse.ArgumentParser):
    """Keep JSON-mode argument failures structured and value-free."""

    def __init__(self, *args: Any, json_requested: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.json_requested = json_requested

    def error(self, message: str) -> None:
        if self.json_requested:
            raise _JsonCliArgumentError("JSON command arguments are invalid")
        super().error(message)


def _build_parser(json_requested: bool = False) -> argparse.ArgumentParser:
    parser = _SchemaArgumentParser(description="Validate a Spark pre-write schema contract", json_requested=json_requested)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compare_parser = subparsers.add_parser("compare", help="Compare canonical StructType JSON files")
    compare_parser.json_requested = json_requested
    compare_parser.add_argument("--source-schema", required=True, help="Source StructType JSON file")
    compare_parser.add_argument("--target-schema", required=True, help="Target StructType JSON file")
    compare_parser.add_argument("--resolution", choices=sorted(RESOLUTION_MODES), default="by-name")
    compare_parser.add_argument(
        "--write-operation",
        choices=sorted(WRITE_OPERATIONS),
        default="generic",
        help="Apply operation-specific schema resolution semantics",
    )
    compare_parser.add_argument("--strict", action="store_true", help="Reject source-only fields in by-name mode")
    compare_parser.add_argument("--type-policy", choices=sorted(TYPE_POLICIES), default="exact")
    compare_parser.add_argument("--delta-type-widening-enabled", action="store_true")
    compare_parser.add_argument("--delta-table-feature-enabled", action="store_true")
    compare_parser.add_argument("--automatic-schema-evolution-enabled", action="store_true")
    compare_parser.add_argument("--manual-type-change", action="store_true")
    compare_parser.add_argument("--json", action="store_true", help="Emit the stable JSON report")
    return parser


def _safe_relative_path(raw_path: str) -> str:
    try:
        return _safe_text(Path(raw_path).name, limit=MAX_IDENTIFIER_CHARS) or "<schema-file>"
    except (TypeError, ValueError):
        return "<schema-file>"


def _invalid_cli_report() -> dict[str, Any]:
    """Return the stable JSON envelope for parse errors without input values."""
    return _report(
        "invalid",
        "<invalid>",
        "<invalid>",
        errors=[
            {
                "type": "invalid_cli_arguments",
                "column": "<root>",
                "message": "Command arguments are invalid",
            }
        ],
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI with a stable JSON envelope for JSON-mode parser errors."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in arguments
    parser = _build_parser(json_requested=json_requested)
    try:
        args = parser.parse_args(arguments)
    except _JsonCliArgumentError:
        print(json.dumps(_invalid_cli_report(), ensure_ascii=True, sort_keys=True))
        return 2
    if args.command != "compare":  # pragma: no cover - argparse enforces this
        parser.error("Unsupported command")
    try:
        source_schema = _read_schema_file(Path(args.source_schema), "source")
        target_schema = _read_schema_file(Path(args.target_schema), "target")
    except SchemaInputError as exc:
        report = _report(
            "invalid",
            args.resolution,
            args.type_policy,
            write_operation=args.write_operation,
            errors=exc.errors,
        )
        report["input_files"] = {"source_schema": _safe_relative_path(args.source_schema), "target_schema": _safe_relative_path(args.target_schema)}
        if args.json:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        else:
            print_validation_report(report)
        return 2
    report = compare_schema_contract(source_schema, target_schema, resolution=args.resolution, write_operation=args.write_operation, strict=args.strict, type_policy=args.type_policy, delta_type_widening_enabled=args.delta_type_widening_enabled, delta_table_feature_enabled=args.delta_table_feature_enabled, automatic_schema_evolution_enabled=args.automatic_schema_evolution_enabled, manual_type_change=args.manual_type_change)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print_validation_report(report)
    return 0 if report["decision"] in {"exact", "compatible"} else 1


if __name__ == "__main__":
    sys.exit(main())
