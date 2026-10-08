"""Pre-fit feature-schema artifact validation without Spark or Databricks imports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

_FINGERPRINT = re.compile(r"^[A-Fa-f0-9]{64}$")
_COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_COLUMN_NAME_LENGTH = 128
_LOGICAL_TYPES = frozenset(
    {
        "boolean",
        "integer",
        "long",
        "float",
        "double",
        "decimal",
        "string",
        "date",
        "timestamp",
        "array",
        "map",
        "struct",
        "binary",
    }
)
_NULL_POLICIES = frozenset(
    {"forbid", "impute", "indicator_and_impute", "model_native", "drop_row"}
)
_ARTIFACT_FIELDS = frozenset({"version", "fingerprint", "features", "target"})
_FIELD_FIELDS = frozenset({"name", "logical_type", "nullable", "null_policy"})
_OBSERVED_FIELDS = frozenset({"name", "logical_type", "nullable"})


def _exact_fields(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} has missing or unsupported fields")


def _validate_declared_field(field: Any, label: str) -> None:
    if not isinstance(field, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_fields(field, _FIELD_FIELDS, label)
    _validate_column_name(field["name"], label)
    if field["logical_type"] not in _LOGICAL_TYPES:
        raise ValueError(f"{label} has an unsupported logical type")
    if not isinstance(field["nullable"], bool):
        raise ValueError(f"{label} nullable must be boolean")
    if field["null_policy"] not in _NULL_POLICIES:
        raise ValueError(f"{label} has an unsupported null policy")


def _validate_column_name(value: Any, label: str) -> None:
    """Mirror the config schema's `columnName` grammar and 128-character cap."""

    if not isinstance(value, str) or not _COLUMN_NAME.fullmatch(value):
        raise ValueError(f"{label} requires a valid column name")
    if len(value) > _MAX_COLUMN_NAME_LENGTH:
        raise ValueError(f"{label} column name exceeds 128 characters")


def _canonical_declaration(field: Mapping[str, Any]) -> dict[str, Any]:
    """Return the closed declaration used for a stable schema fingerprint."""

    return {
        "name": field["name"],
        "logical_type": field["logical_type"],
        "nullable": field["nullable"],
        "null_policy": field["null_policy"],
    }


def compute_feature_schema_fingerprint(artifact: Mapping[str, Any]) -> str:
    """SHA-256 of version, ordered feature declarations, and target declaration.

    Declarations preserve their configured feature order.  JSON keys use a fixed
    lexical order and compact UTF-8 encoding so the same artifact always hashes
    to the same value across runtimes.
    """

    if not isinstance(artifact, Mapping):
        raise ValueError("feature schema artifact must be an object")
    features = artifact.get("features")
    target = artifact.get("target")
    version = artifact.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("feature schema artifact requires a version")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise ValueError("feature schema features must be an array")
    for field in features:
        _validate_declared_field(field, "feature schema field")
    _validate_declared_field(target, "feature schema target")
    canonical = {
        "features": [_canonical_declaration(field) for field in features],
        "target": _canonical_declaration(target),
        "version": version,
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_feature_schema_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate the closed artifact before comparing it to a fit-time schema."""

    if not isinstance(artifact, Mapping):
        raise ValueError("feature schema artifact must be an object")
    _exact_fields(artifact, _ARTIFACT_FIELDS, "feature schema artifact")
    if not isinstance(artifact["version"], str) or not artifact["version"]:
        raise ValueError("feature schema artifact requires a version")
    fingerprint = artifact["fingerprint"]
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("feature schema fingerprint must be a SHA-256 hex string")
    features = artifact["features"]
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise ValueError("feature schema features must be an array")
    names: set[str] = set()
    for field in features:
        _validate_declared_field(field, "feature schema field")
        name = str(field["name"])
        if name in names:
            raise ValueError("feature schema field names must be unique")
        names.add(name)
    if not names:
        raise ValueError("feature schema requires at least one feature")
    _validate_declared_field(artifact["target"], "feature schema target")
    if artifact["target"]["name"] in names:
        raise ValueError("target must not also be a feature")
    if fingerprint.casefold() != compute_feature_schema_fingerprint(artifact):
        raise ValueError("feature schema fingerprint does not match declarations")


def _validate_observed_field(field: Any, label: str) -> tuple[str, str, bool]:
    if not isinstance(field, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_fields(field, _OBSERVED_FIELDS, label)
    _validate_column_name(field["name"], label)
    if field["logical_type"] not in _LOGICAL_TYPES or not isinstance(
        field["nullable"], bool
    ):
        raise ValueError(f"{label} is invalid")
    return field["name"], field["logical_type"], field["nullable"]


def validate_feature_schema_before_fit(
    artifact: Mapping[str, Any],
    *,
    observed_features: Sequence[Mapping[str, Any]],
    observed_target: Mapping[str, Any],
) -> None:
    """Require selected fit-time features and target to match the frozen artifact."""

    validate_feature_schema_artifact(artifact)
    if isinstance(observed_features, (str, bytes)):
        raise ValueError("observed features must be an array")
    observed_declarations: list[tuple[str, str, bool]] = []
    observed_names: set[str] = set()
    for observed in observed_features:
        name, logical_type, nullable = _validate_observed_field(
            observed, "observed feature"
        )
        if name in observed_names:
            raise ValueError("observed feature names must be unique")
        observed_names.add(name)
        observed_declarations.append((name, logical_type, nullable))
    declared_declarations = [
        (field["name"], field["logical_type"], field["nullable"])
        for field in artifact["features"]
    ]
    if observed_declarations != declared_declarations:
        raise ValueError("observed features do not match the frozen schema artifact")
    target_name, target_type, target_nullable = _validate_observed_field(
        observed_target, "observed target"
    )
    declared_target = artifact["target"]
    if (target_name, target_type, target_nullable) != (
        declared_target["name"],
        declared_target["logical_type"],
        declared_target["nullable"],
    ):
        raise ValueError("observed target does not match the frozen schema artifact")
