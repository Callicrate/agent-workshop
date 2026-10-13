"""Pre-fit feature-schema validation without Spark or Databricks imports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

_FINGERPRINT = re.compile(r"^[A-Fa-f0-9]{64}$")
_COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DECIMAL_TYPE = re.compile(
    r"^decimal\(([1-9][0-9]{0,1}),(0|[1-9][0-9]?)\)$"
)
_MAX_COLUMN_NAME_LENGTH = 128
_MAX_TYPE_DEPTH = 16
_MAX_TYPE_NODES = 1_024
_MAX_STRUCT_FIELDS = 256
_SCALAR_LOGICAL_TYPES = frozenset(
    {
        "boolean",
        "integer",
        "long",
        "float",
        "double",
        "string",
        "date",
        "timestamp",
        "binary",
    }
)
_LOGICAL_TYPES = _SCALAR_LOGICAL_TYPES | frozenset({"decimal", "array", "map", "struct"})
_NULL_POLICIES = frozenset(
    {"forbid", "impute", "indicator_and_impute", "model_native", "drop_row"}
)
_ARTIFACT_FIELDS_V1 = frozenset({"version", "fingerprint", "features", "target"})
_ARTIFACT_FIELDS_V2 = _ARTIFACT_FIELDS_V1 | frozenset({"format"})
_DECLARATION_FIELDS_V1 = _ARTIFACT_FIELDS_V1 - frozenset({"fingerprint"})
_DECLARATION_FIELDS_V2 = _ARTIFACT_FIELDS_V2 - frozenset({"fingerprint"})
_FIELD_FIELDS_V1 = frozenset({"name", "logical_type", "nullable", "null_policy"})
_FIELD_FIELDS_V2 = _FIELD_FIELDS_V1 | frozenset({"data_type"})
_OBSERVED_FIELDS_V1 = frozenset({"name", "logical_type", "nullable"})
_OBSERVED_FIELDS_V2 = frozenset({"name", "data_type", "nullable"})
_SPARK_OBSERVED_FIELD_FIELDS = frozenset({"name", "type", "nullable", "metadata"})


class _TypeBudget:
    """Bound total recursive type work across one artifact or observed schema."""

    def __init__(self) -> None:
        self.nodes = 0
        self.struct_fields = 0

    def add_node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_TYPE_NODES:
            raise ValueError("physical schema type node limit exceeded")

    def add_struct_field(self) -> None:
        self.struct_fields += 1
        if self.struct_fields > _MAX_STRUCT_FIELDS:
            raise ValueError("physical schema struct field limit exceeded")


def _exact_fields(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} has missing or unsupported fields")


def _validate_column_name(value: Any, label: str) -> str:
    """Mirror the config schema's closed column-name grammar and length cap."""

    if not isinstance(value, str) or not _COLUMN_NAME.fullmatch(value):
        raise ValueError(f"{label} requires a valid column name")
    if len(value) > _MAX_COLUMN_NAME_LENGTH:
        raise ValueError(f"{label} column name exceeds 128 characters")
    return value


def _artifact_format(artifact: Mapping[str, Any], *, require_fingerprint: bool) -> int:
    fields = set(artifact)
    if fields == _ARTIFACT_FIELDS_V1 or (
        not require_fingerprint and fields == _DECLARATION_FIELDS_V1
    ):
        return 1
    if (
        (
            fields == _ARTIFACT_FIELDS_V2
            or (not require_fingerprint and fields == _DECLARATION_FIELDS_V2)
        )
        and artifact.get("format") == 2
    ):
        return 2
    raise ValueError("feature schema artifact has missing or unsupported fields")


def _validate_decimal_type(value: str) -> None:
    match = _DECIMAL_TYPE.fullmatch(value)
    if match is None:
        raise ValueError("physical decimal type must be canonical decimal(precision,scale)")
    precision, scale = (int(part) for part in match.groups())
    if not 1 <= precision <= 38 or not 0 <= scale <= precision:
        raise ValueError("physical decimal precision or scale is outside Spark limits")


def _validate_physical_type(
    value: Any,
    *,
    label: str,
    depth: int,
    budget: _TypeBudget,
) -> str:
    """Validate the supported, canonical subset of Spark DataType.jsonValue()."""

    if depth > _MAX_TYPE_DEPTH:
        raise ValueError("physical schema type depth limit exceeded")
    budget.add_node()
    if isinstance(value, str):
        if value in _SCALAR_LOGICAL_TYPES:
            return value
        _validate_decimal_type(value)
        return "decimal"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} physical data type is invalid")

    kind = value.get("type")
    if kind == "array":
        _exact_fields(value, frozenset({"type", "elementType", "containsNull"}), label)
        if not isinstance(value["containsNull"], bool):
            raise ValueError(f"{label} array containsNull must be boolean")
        _validate_physical_type(
            value["elementType"], label=label, depth=depth + 1, budget=budget
        )
        return "array"
    if kind == "map":
        _exact_fields(
            value,
            frozenset({"type", "keyType", "valueType", "valueContainsNull"}),
            label,
        )
        if not isinstance(value["valueContainsNull"], bool):
            raise ValueError(f"{label} map valueContainsNull must be boolean")
        key_logical_type = _validate_physical_type(
            value["keyType"], label=label, depth=depth + 1, budget=budget
        )
        if key_logical_type not in _SCALAR_LOGICAL_TYPES | frozenset({"decimal"}):
            raise ValueError(f"{label} map key type must be a supported scalar")
        _validate_physical_type(
            value["valueType"], label=label, depth=depth + 1, budget=budget
        )
        return "map"
    if kind == "struct":
        _exact_fields(value, frozenset({"type", "fields"}), label)
        fields = value["fields"]
        if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
            raise ValueError(f"{label} struct fields must be an array")
        names: set[str] = set()
        for field in fields:
            if not isinstance(field, Mapping):
                raise ValueError(f"{label} struct field must be an object")
            _exact_fields(field, frozenset({"name", "type", "nullable", "metadata"}), label)
            name = _validate_column_name(field["name"], f"{label} struct field")
            if name in names:
                raise ValueError(f"{label} struct field names must be unique")
            names.add(name)
            if not isinstance(field["nullable"], bool):
                raise ValueError(f"{label} struct field nullable must be boolean")
            if field["metadata"] != {}:
                raise ValueError(f"{label} struct field metadata must be empty")
            budget.add_struct_field()
            _validate_physical_type(
                field["type"], label=label, depth=depth + 1, budget=budget
            )
        return "struct"
    raise ValueError(f"{label} has an unsupported physical data type")


def _validate_declared_field(
    field: Any,
    label: str,
    *,
    artifact_format: int,
    budget: _TypeBudget | None = None,
) -> None:
    if not isinstance(field, Mapping):
        raise ValueError(f"{label} must be an object")
    expected_fields = _FIELD_FIELDS_V1 if artifact_format == 1 else _FIELD_FIELDS_V2
    _exact_fields(field, expected_fields, label)
    _validate_column_name(field["name"], label)
    logical_type = field["logical_type"]
    if not isinstance(logical_type, str) or logical_type not in _LOGICAL_TYPES:
        raise ValueError(f"{label} has an unsupported logical type")
    if artifact_format == 1 and logical_type not in _SCALAR_LOGICAL_TYPES:
        raise ValueError(f"{label} format 1 supports scalar logical types only")
    if not isinstance(field["nullable"], bool):
        raise ValueError(f"{label} nullable must be boolean")
    if (
        not isinstance(field["null_policy"], str)
        or field["null_policy"] not in _NULL_POLICIES
    ):
        raise ValueError(f"{label} has an unsupported null policy")
    if artifact_format == 2:
        if budget is None:
            raise ValueError("physical schema validation requires a shared budget")
        physical_root = _validate_physical_type(
            field["data_type"], label=label, depth=1, budget=budget
        )
        if logical_type != physical_root:
            raise ValueError(f"{label} logical type does not match physical data type")


def _canonical_declaration(field: Mapping[str, Any], artifact_format: int) -> dict[str, Any]:
    """Return the closed declaration used for a stable schema fingerprint."""

    declaration = {
        "name": field["name"],
        "logical_type": field["logical_type"],
        "nullable": field["nullable"],
        "null_policy": field["null_policy"],
    }
    if artifact_format == 2:
        declaration["data_type"] = field["data_type"]
    return declaration


def _validate_artifact_contents(
    artifact: Mapping[str, Any], *, require_fingerprint: bool
) -> int:
    artifact_format = _artifact_format(
        artifact, require_fingerprint=require_fingerprint
    )
    version = artifact.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("feature schema artifact requires a version")
    features = artifact.get("features")
    if isinstance(features, (str, bytes)) or not isinstance(features, Sequence):
        raise ValueError("feature schema features must be an array")
    budget = _TypeBudget()
    names: set[str] = set()
    for field in features:
        _validate_declared_field(
            field,
            "feature schema field",
            artifact_format=artifact_format,
            budget=budget,
        )
        name = str(field["name"])
        if name in names:
            raise ValueError("feature schema field names must be unique")
        names.add(name)
    if not names:
        raise ValueError("feature schema requires at least one feature")
    target = artifact.get("target")
    _validate_declared_field(
        target,
        "feature schema target",
        artifact_format=artifact_format,
        budget=budget,
    )
    if target["name"] in names:
        raise ValueError("target must not also be a feature")
    return artifact_format


def compute_feature_schema_fingerprint(artifact: Mapping[str, Any]) -> str:
    """SHA-256 of a closed format-1 or format-2 declaration.

    The input must contain only declaration keys, optionally with a fingerprint
    key that is ignored. Final artifact validation separately requires a
    matching 64-hex fingerprint.
    """

    if not isinstance(artifact, Mapping):
        raise ValueError("feature schema artifact must be an object")
    artifact_format = _validate_artifact_contents(
        artifact, require_fingerprint=False
    )
    canonical: dict[str, Any] = {
        "features": [
            _canonical_declaration(field, artifact_format)
            for field in artifact["features"]
        ],
        "target": _canonical_declaration(artifact["target"], artifact_format),
        "version": artifact["version"],
    }
    if artifact_format == 2:
        canonical["format"] = 2
    encoded = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_feature_schema_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate a closed artifact before comparing it to a fit-time schema."""

    if not isinstance(artifact, Mapping):
        raise ValueError("feature schema artifact must be an object")
    _validate_artifact_contents(artifact, require_fingerprint=True)
    fingerprint = artifact.get("fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("feature schema fingerprint must be a SHA-256 hex string")
    if fingerprint.casefold() != compute_feature_schema_fingerprint(artifact):
        raise ValueError("feature schema fingerprint does not match declarations")


def _validate_observed_field(
    field: Any,
    label: str,
    *,
    artifact_format: int,
    budget: _TypeBudget | None = None,
) -> tuple[str, object, bool]:
    if not isinstance(field, Mapping):
        raise ValueError(f"{label} must be an object")
    expected_fields = _OBSERVED_FIELDS_V1 if artifact_format == 1 else _OBSERVED_FIELDS_V2
    _exact_fields(field, expected_fields, label)
    name = _validate_column_name(field["name"], label)
    if not isinstance(field["nullable"], bool):
        raise ValueError(f"{label} nullable must be boolean")
    if artifact_format == 1:
        logical_type = field["logical_type"]
        if (
            not isinstance(logical_type, str)
            or logical_type not in _SCALAR_LOGICAL_TYPES
        ):
            raise ValueError(f"{label} format 1 supports scalar logical types only")
        return name, logical_type, field["nullable"]
    if budget is None:
        raise ValueError("physical schema validation requires a shared budget")
    return _validated_physical_observed_triple(
        name=name,
        data_type=field["data_type"],
        nullable=field["nullable"],
        label=label,
        budget=budget,
    )


def _validated_physical_observed_triple(
    *,
    name: Any,
    data_type: Any,
    nullable: Any,
    label: str,
    budget: _TypeBudget,
) -> tuple[str, object, bool]:
    """Validate the shared format-2 observed-field tuple contract."""

    validated_name = _validate_column_name(name, label)
    if not isinstance(nullable, bool):
        raise ValueError(f"{label} nullable must be boolean")
    _validate_physical_type(data_type, label=label, depth=1, budget=budget)
    return validated_name, data_type, nullable


def observed_field_from_spark_json(field: Any) -> dict[str, object]:
    """Convert one complete Spark StructField.jsonValue() object to an observed mapping.

    Root metadata is checked before it is discarded. The offline format-2
    contract accepts only ordinary empty metadata and does not import PySpark.
    """

    if not isinstance(field, Mapping):
        raise ValueError("Spark observed field must be an object")
    _exact_fields(field, _SPARK_OBSERVED_FIELD_FIELDS, "Spark observed field")
    if not isinstance(field["metadata"], Mapping) or field["metadata"]:
        raise ValueError("Spark observed field metadata must be empty")
    name, data_type, nullable = _validated_physical_observed_triple(
        name=field["name"],
        data_type=field["type"],
        nullable=field["nullable"],
        label="Spark observed field",
        budget=_TypeBudget(),
    )
    return {"name": name, "data_type": data_type, "nullable": nullable}


def validate_feature_schema_before_fit(
    artifact: Mapping[str, Any],
    *,
    observed_features: Sequence[Mapping[str, Any]],
    observed_target: Mapping[str, Any],
) -> None:
    """Require selected fit-time features and target to match the frozen artifact."""

    validate_feature_schema_artifact(artifact)
    artifact_format = _artifact_format(artifact, require_fingerprint=True)
    if isinstance(observed_features, (str, bytes)) or not isinstance(
        observed_features, Sequence
    ):
        raise ValueError("observed features must be an array")
    budget = _TypeBudget()
    observed_declarations: list[tuple[str, object, bool]] = []
    observed_names: set[str] = set()
    for observed in observed_features:
        declaration = _validate_observed_field(
            observed,
            "observed feature",
            artifact_format=artifact_format,
            budget=budget,
        )
        if declaration[0] in observed_names:
            raise ValueError("observed feature names must be unique")
        observed_names.add(declaration[0])
        observed_declarations.append(declaration)
    if artifact_format == 1:
        declared_declarations = [
            (field["name"], field["logical_type"], field["nullable"])
            for field in artifact["features"]
        ]
    else:
        declared_declarations = [
            (field["name"], field["data_type"], field["nullable"])
            for field in artifact["features"]
        ]
    if observed_declarations != declared_declarations:
        raise ValueError("observed features do not match the frozen schema artifact")
    observed_target_declaration = _validate_observed_field(
        observed_target,
        "observed target",
        artifact_format=artifact_format,
        budget=budget,
    )
    declared_target = artifact["target"]
    declared_target_declaration: tuple[str, object, bool]
    if artifact_format == 1:
        declared_target_declaration = (
            declared_target["name"],
            declared_target["logical_type"],
            declared_target["nullable"],
        )
    else:
        declared_target_declaration = (
            declared_target["name"],
            declared_target["data_type"],
            declared_target["nullable"],
        )
    if observed_target_declaration != declared_target_declaration:
        raise ValueError("observed target does not match the frozen schema artifact")
