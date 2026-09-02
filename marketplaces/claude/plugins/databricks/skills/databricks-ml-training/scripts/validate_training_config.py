"""Offline validator for the closed Databricks ML training config contract.

The command reports stable issue codes and JSON paths only. It never echoes
configuration values, so tokens and sensitive metadata do not enter logs.
"""

from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from feature_schema_contract import validate_feature_schema_artifact
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from mlflow_example_privacy import validate_input_example_columns
from point_in_time_contract import parse_rfc3339
from promotion_eligibility import (
    METRIC_DIRECTIONS,
    validate_eligibility_policy,
    validate_metric_value,
)

MAX_CONFIG_BYTES = 2 * 1024 * 1024
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "assets" / "training-config-schema.json"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|//|\\\\)")


def _reject_non_finite_json(_: str) -> Any:
    raise ValueError("non-finite JSON number")


def _issue_path(parts: Sequence[Any]) -> str:
    return ".".join(str(part) for part in parts) or "<root>"


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _contains_non_finite(
    value: Any, path: tuple[Any, ...] = ()
) -> list[tuple[str, str]]:
    if isinstance(value, float) and not math.isfinite(value):
        return [("non_finite_number", _issue_path(path))]
    if isinstance(value, Mapping):
        issues: list[tuple[str, str]] = []
        for key, nested in value.items():
            issues.extend(_contains_non_finite(nested, (*path, key)))
        return issues
    if isinstance(value, list):
        issues = []
        for index, nested in enumerate(value):
            issues.extend(_contains_non_finite(nested, (*path, index)))
        return issues
    return []


def _is_normalized_posix_path(value: str) -> bool:
    if (
        not value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or _DRIVE_OR_UNC.match(value)
    ):
        return False
    segments = value.split("/")[1:]
    return bool(segments) and all(
        segment not in {"", ".", ".."} for segment in segments
    )


def _is_volume_root(value: str) -> bool:
    if not value.startswith("/Volumes/") or not _is_normalized_posix_path(value):
        return False
    components = value.split("/")[2:]
    return len(components) >= 3 and all(
        IDENTIFIER_PATTERN.fullmatch(component) for component in components
    )


def _validate_artifact_root(outputs: Mapping[str, Any]) -> list[tuple[str, str]]:
    artifact_root = outputs.get("artifact_root")
    project_root = outputs.get("project_root")
    if not isinstance(artifact_root, str):
        return []
    artifact_path = "outputs.artifact_root"
    if artifact_root.casefold().startswith("/dbfs/workspace/shared"):
        return [("forbidden_artifact_root", artifact_path)]
    if artifact_root.startswith("/Volumes/"):
        return (
            []
            if _is_volume_root(artifact_root)
            else [("invalid_uc_volume_root", artifact_path)]
        )
    if artifact_root.casefold().startswith("/volumes"):
        return [("invalid_uc_volume_root", artifact_path)]
    if not _is_normalized_posix_path(artifact_root):
        return [("invalid_project_path", artifact_path)]
    if not isinstance(project_root, str):
        return [("project_root_required", artifact_path)]
    if project_root.startswith("/Volumes/") or not _is_normalized_posix_path(
        project_root
    ):
        return [("invalid_project_root", "outputs.project_root")]
    try:
        if posixpath.commonpath((artifact_root, project_root)) != project_root:
            return [("artifact_root_outside_project_root", artifact_path)]
    except ValueError:
        return [("artifact_root_outside_project_root", artifact_path)]
    return []


def _metric_pair_issue(metric: Any, direction: Any, path: str) -> list[tuple[str, str]]:
    if METRIC_DIRECTIONS.get(metric) != direction:
        return [("invalid_metric_direction", path)]
    return []


def _metric_slice_issue(metrics: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Check configured slice gates against the declared selection metric domain."""

    metric = metrics.get("selection_metric")
    requirements = metrics.get("required_slices")
    if not isinstance(metric, str) or not isinstance(requirements, list):
        return []
    try:
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            validate_metric_value(
                metric, requirement.get("minimum_metric"), "slice minimum metric"
            )
    except ValueError:
        return [("invalid_metric_domain", "metrics.required_slices")]
    return []


def _duplicate_slice_name_issue(
    requirements: Any, *, code: str, path: str
) -> list[tuple[str, str]]:
    if not isinstance(requirements, list):
        return []
    names = [
        requirement.get("name")
        for requirement in requirements
        if isinstance(requirement, Mapping)
    ]
    if len(set(names)) != len(names):
        return [(code, path)]
    return []


def _validate_hyperparameter_names(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    model = config.get("model")
    if not isinstance(model, Mapping):
        return []
    hyperparameters = model.get("hyperparameters")
    if not isinstance(hyperparameters, list):
        return []
    names = [
        parameter.get("name")
        for parameter in hyperparameters
        if isinstance(parameter, Mapping)
    ]
    if len(set(names)) != len(names):
        return [("duplicate_hyperparameter_name", "model.hyperparameters")]
    return []


def _validate_feature_contract(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    features = config.get("features")
    target = config.get("target")
    if not isinstance(features, Mapping) or not isinstance(target, Mapping):
        return []
    artifact = features.get("schema_artifact")
    if isinstance(artifact, Mapping):
        try:
            validate_feature_schema_artifact(artifact)
        except ValueError:
            return [("invalid_feature_schema_artifact", "features.schema_artifact")]
        artifact_features = artifact.get("features")
        if isinstance(artifact_features, list):
            artifact_columns = [field.get("name") for field in artifact_features]
            if features.get("columns") != artifact_columns:
                return [("feature_schema_columns_mismatch", "features.columns")]
        artifact_target = artifact.get("target")
        if isinstance(artifact_target, Mapping) and artifact_target.get(
            "name"
        ) != target.get("column"):
            return [
                ("feature_schema_target_mismatch", "features.schema_artifact.target")
            ]
    return []


def _validate_imbalance(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    target = config.get("target")
    imbalance = config.get("imbalance")
    if not isinstance(target, Mapping) or not isinstance(imbalance, Mapping):
        return []
    issues: list[tuple[str, str]] = []
    task_type = target.get("task_type")
    strategy = imbalance.get("strategy")
    if task_type != "binary" and strategy == "scale_pos_weight":
        issues.append(("scale_pos_weight_binary_only", "imbalance.strategy"))
    class_weights = imbalance.get("class_weights")
    if strategy == "class_weight" and isinstance(class_weights, list):
        labels: list[Any] = []
        for entry in class_weights:
            if isinstance(entry, Mapping):
                labels.append(entry.get("label"))
        if len(set(labels)) != len(labels):
            issues.append(("duplicate_class_weight_label", "imbalance.class_weights"))
        if task_type == "multiclass" and set(labels) != set(
            target.get("class_labels", [])
        ):
            issues.append(
                ("multiclass_class_weights_mismatch", "imbalance.class_weights")
            )
    return issues


def _validate_semantics(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    issues.extend(_contains_non_finite(config))

    splits = config.get("splits")
    parsed_windows: dict[str, tuple[Any, Any]] = {}
    if isinstance(splits, Mapping):
        for split_name in ("train", "validation", "test"):
            window = splits.get(split_name)
            if not isinstance(window, Mapping):
                continue
            try:
                start = parse_rfc3339(window["start"])
                end = parse_rfc3339(window["end"])
            except (KeyError, ValueError, TypeError):
                issues.append(("invalid_rfc3339", f"splits.{split_name}"))
                continue
            if not start < end:
                issues.append(("window_not_ordered", f"splits.{split_name}"))
            parsed_windows[split_name] = (start, end)
        for first, second in (("train", "validation"), ("validation", "test")):
            if first in parsed_windows and second in parsed_windows:
                if parsed_windows[first][1] > parsed_windows[second][0]:
                    issues.append(("split_windows_not_chronological", "splits"))
        for first, second in (
            ("train", "validation"),
            ("train", "test"),
            ("validation", "test"),
        ):
            if first in parsed_windows and second in parsed_windows:
                first_start, first_end = parsed_windows[first]
                second_start, second_end = parsed_windows[second]
                if max(first_start, second_start) < min(first_end, second_end):
                    issues.append(("split_windows_overlap", f"splits.{first}.{second}"))

    provenance = config.get("provenance")
    if isinstance(provenance, Mapping):
        try:
            parse_rfc3339(provenance["run_started_at"])
        except (KeyError, ValueError, TypeError):
            issues.append(("invalid_rfc3339", "provenance.run_started_at"))
        sources = provenance.get("sources")
        if isinstance(sources, list):
            roles = {
                source.get("role") for source in sources if isinstance(source, Mapping)
            }
            if not {"examples", "features"}.issubset(roles):
                issues.append(("missing_source_snapshot_role", "provenance.sources"))

    outputs = config.get("outputs")
    if isinstance(outputs, Mapping):
        issues.extend(_validate_artifact_root(outputs))

    signature = config.get("signature")
    if isinstance(signature, Mapping) and isinstance(
        signature.get("input_example"), Mapping
    ):
        columns = signature["input_example"].get("columns")
        if isinstance(columns, list):
            try:
                validate_input_example_columns(columns)
            except ValueError:
                issues.append(
                    (
                        "sensitive_input_example_column",
                        "signature.input_example.columns",
                    )
                )
        features = config.get("features")
        if isinstance(features, Mapping) and columns != features.get("columns"):
            issues.append(
                (
                    "signature_input_example_columns_mismatch",
                    "signature.input_example.columns",
                )
            )

    target = config.get("target")
    threshold = config.get("threshold")
    if isinstance(target, Mapping) and isinstance(threshold, Mapping):
        if target.get("task_type") == "multiclass" and threshold.get("enabled"):
            issues.append(("binary_threshold_required", "threshold.enabled"))

    metrics = config.get("metrics")
    promotion = config.get("promotion")
    training = config.get("training")
    if isinstance(metrics, Mapping):
        issues.extend(
            _metric_pair_issue(
                metrics.get("selection_metric"), metrics.get("direction"), "metrics"
            )
        )
        issues.extend(_metric_slice_issue(metrics))
        issues.extend(
            _duplicate_slice_name_issue(
                metrics.get("required_slices"),
                code="duplicate_required_slice_name",
                path="metrics.required_slices",
            )
        )
    if isinstance(promotion, Mapping):
        selection = promotion.get("selection")
        eligibility = promotion.get("eligibility")
        if isinstance(selection, Mapping):
            issues.extend(
                _metric_pair_issue(
                    selection.get("metric"),
                    selection.get("direction"),
                    "promotion.selection",
                )
            )
            if isinstance(metrics, Mapping) and (
                selection.get("metric") != metrics.get("selection_metric")
                or selection.get("direction") != metrics.get("direction")
            ):
                issues.append(("selection_metric_mismatch", "promotion.selection"))
        if isinstance(eligibility, Mapping) and eligibility.get("enabled") is True:
            try:
                validate_eligibility_policy(eligibility)
            except ValueError:
                issues.append(("invalid_eligibility_policy", "promotion.eligibility"))
            issues.extend(
                _metric_pair_issue(
                    eligibility.get("metric"),
                    eligibility.get("direction"),
                    "promotion.eligibility",
                )
            )
            issues.extend(
                _duplicate_slice_name_issue(
                    eligibility.get("required_slices"),
                    code="duplicate_promotion_required_slice_name",
                    path="promotion.eligibility.required_slices",
                )
            )
            if isinstance(training, Mapping) and training.get("mode") == "dev":
                issues.append(("dev_mode_ineligible", "promotion.eligibility.enabled"))
        effects_requested = bool(promotion.get("request_promotion"))
        if isinstance(outputs, Mapping):
            effects_requested = (
                effects_requested
                or bool(outputs.get("register_model"))
                or bool(outputs.get("write_promotion_candidate"))
            )
        if effects_requested:
            if isinstance(training, Mapping) and training.get("mode") == "dev":
                issues.append(("dev_mode_side_effect", "outputs"))
            if not (
                isinstance(training, Mapping)
                and training.get("mode") == "full"
                and isinstance(eligibility, Mapping)
                and eligibility.get("enabled") is True
            ):
                issues.append(("effects_require_full_enabled_eligibility", "promotion"))

    issues.extend(_validate_feature_contract(config))
    issues.extend(_validate_hyperparameter_names(config))
    issues.extend(_validate_imbalance(config))
    return sorted(set(issues))


def validate_config(
    config: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """Return stable, value-free schema and semantic contract failures."""

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    schema_issues = [
        (f"schema_{error.validator}", _issue_path(error.absolute_path))
        for error in validator.iter_errors(config)
    ]
    if schema_issues:
        return sorted(set(schema_issues))
    return _validate_semantics(config)


def _read_json(path: Path) -> Mapping[str, Any]:
    if (
        path.suffix.lower() != ".json"
        or not path.is_file()
        or path.stat().st_size > MAX_CONFIG_BYTES
    ):
        raise ValueError("invalid config file")
    parsed = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject_non_finite_json
    )
    if not isinstance(parsed, Mapping):
        raise ValueError("config root must be an object")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Validate one config without emitting its data values."""

    parser = argparse.ArgumentParser(
        description="Validate an ML training JSON config without printing its values."
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="JSON configuration file"
    )
    args = parser.parse_args(argv)
    try:
        config = _read_json(args.config)
        schema = _read_json(SCHEMA_PATH)
        issues = validate_config(config, schema)
    except (OSError, ValueError, json.JSONDecodeError):
        print("INVALID config_read_or_parse", file=sys.stderr)
        return 2
    except SchemaError:
        print("INVALID validator_internal_error", file=sys.stderr)
        return 3
    if issues:
        for code, path in issues[:20]:
            print(f"INVALID {code} {path}", file=sys.stderr)
        return 1
    print("VALID training_config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
