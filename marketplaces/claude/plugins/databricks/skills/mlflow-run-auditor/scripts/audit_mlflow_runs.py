"""Audit MLflow runs through a bounded, offline-testable readiness gate.

Usage:
    python scripts/audit_mlflow_runs.py <experiment_name_or_id> [--last N] [--json]

The command only reads MLflow tracking data. It never starts training, changes
registry state, moves aliases, or updates serving.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RUN_STAGES = (
    "prototype",
    "job-ready-training",
    "promotion-candidate",
    "serving-candidate",
    "batch-inference-dependency",
)
MAX_ARTIFACT_DEPTH = 16
MAX_ARTIFACT_COUNT = 10_000
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_JSON_ARTIFACT_BYTES = 1 * 1024 * 1024
MAX_ARTIFACTS_PER_DIRECTORY = 1_000
MAX_ARTIFACT_DIRECTORIES = 1_000
MAX_CODE_SCAN_FILES = 500
MAX_CODE_SCAN_BYTES = 5 * 1024 * 1024
MAX_CODE_SCAN_WALK_ENTRIES = 2_000
MAX_TEXT_LENGTH = 512
MAX_EVIDENCE_VALUE_LENGTH = 4096
MAX_PROFILE_LENGTH = 128
LOCAL_NULL_POLICIES = frozenset({"short_circuit_unscorable", "fail", "drop", "impute_with_audited_default"})
LOCAL_METRIC_AVERAGING = frozenset({"binary", "macro", "micro", "weighted", "none"})
LOCAL_CANONICAL_METRICS = frozenset({"accuracy", "precision", "recall", "f1", "roc_auc"})

METRIC_ALIASES = {
    "accuracy": ("accuracy", "val_accuracy", "test_accuracy"),
    "f1": ("f1", "f1_score", "val_f1", "test_f1"),
    "precision": ("precision", "precision_score", "val_precision", "test_precision"),
    "recall": ("recall", "recall_score", "val_recall", "test_recall"),
    "auc": ("auc", "roc_auc", "auc_roc", "val_auc", "test_auc"),
}
PARAM_ALIASES = {
    "source_table": ("source_table", "source_tables", "training_source_table", "input_table"),
    "dataset_version": ("dataset_version", "source_table_version", "table_version"),
    "at_timestamp": ("AT_TIMESTAMP", "at_timestamp", "as_of_timestamp", "as_of_date"),
    "train_start_offset": ("TRAIN_START_OFFSET_IN_DAYS", "train_start_offset_days"),
    "train_end_offset": ("TRAIN_END_OFFSET_IN_HOURS", "train_end_offset_hours"),
    "validation_start_offset": ("VAL_START_OFFSET_IN_DAYS", "validation_start_offset_days"),
    "validation_end_offset": ("VAL_END_OFFSET_IN_HOURS", "validation_end_offset_hours"),
    "test_start_offset": ("TEST_START_OFFSET_IN_DAYS", "test_start_offset_days"),
    "test_end_offset": ("TEST_END_OFFSET_IN_HOURS", "test_end_offset_hours"),
    "timezone": ("timezone", "time_zone", "dataset_timezone"),
    "scd2_semantics": ("scd2_predicate", "scd2_as_of_column", "table_version", "dataset_version"),
    "source_freshness": ("source_freshness_checked_at", "source_max_event_timestamp", "source_max_updated_at"),
    "null_policy": ("null_policy", "input_null_policy", "missing_input_policy"),
    "registered_model_name": ("registered_model_name", "uc_model_name", "unity_catalog_model_name"),
    "experiment_path": ("experiment_path", "mlflow_experiment_path"),
    "workspace_path": ("databricks_workspace_path", "workspace_path", "notebook_path"),
    "entrypoint": ("entrypoint", "script_path", "training_source_path"),
    "job_parameters": ("job_parameters", "runtime_parameters", "argparse_contract"),
    "inference_loader": ("inference_loader", "inference_model_uri", "serving_model_uri"),
    "selected_model_objective": ("selected_model_objective", "selection_metric", "best_model_metric"),
}
ARTIFACT_PATHS = {
    "feature_list": ("feature_list.json",),
    "label_map": ("label_mapping.json", "label_map.json"),
    "metric_formulas": ("metric_formulas.json",),
    "confusion_matrix": ("confusion_matrix.json",),
    "null_policy": ("null_policy.json",),
    "source_freshness": ("source_freshness.json",),
    "job_parameter_contract": ("job_parameter_contract.json",),
    "job_smoke": ("job_smoke.json",),
    "inference_stub": ("inference_stub.py", "inference_loader.py"),
    "selected_model_metadata": ("selected_model.json",),
    "promotion_handoff": ("promotion_handoff.json",),
    "serving_contract": ("serving_contract.json",),
    "batch_input_contract": ("batch_input_contract.json",),
    "batch_output_contract": ("batch_output_contract.json",),
}
JOB_ARTIFACT_REQUIREMENTS = frozenset({"job_parameter_contract", "job_smoke", "inference_stub"})
INPUT_EXAMPLE_UNPROVEN_INVENTORY_REASONS = frozenset({
    "artifact count limit reached",
    "artifact directory entry limit reached",
    "artifact byte limit reached",
})
SUPPORTED_INPUT_EXAMPLE_TYPES = frozenset(
    {
        "dataframe",
        "ndarray",
        "sparse_matrix_csc",
        "sparse_matrix_csr",
        "json_object",
    }
)


@dataclass(frozen=True)
class StageRequirements:
    """Evidence one lifecycle stage adds; all earlier rows are inherited."""

    parameters: tuple[str, ...] = ()
    json_artifacts: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


STAGE_REQUIREMENTS = {
    "prototype": StageRequirements(
        parameters=(
            "source_table", "dataset_version", "experiment_path", "workspace_path",
            "registered_model_name", "train_rows", "val_rows",
        ),
        json_artifacts=("feature_list", "label_map", "metric_formulas", "confusion_matrix"),
    ),
    "job-ready-training": StageRequirements(
        parameters=("entrypoint", "job_parameters"),
        json_artifacts=("job_parameter_contract", "job_smoke"),
        artifacts=("inference_stub",),
    ),
    "promotion-candidate": StageRequirements(
        parameters=("selected_model_objective",),
        json_artifacts=("selected_model_metadata", "promotion_handoff"),
    ),
    "serving-candidate": StageRequirements(
        parameters=("inference_loader",), json_artifacts=("serving_contract",),
    ),
    "batch-inference-dependency": StageRequirements(
        json_artifacts=("batch_input_contract", "batch_output_contract"),
    ),
}


@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    is_dir: bool
    size: int


@dataclass
class ArtifactInventory:
    entries: list[ArtifactEntry] = field(default_factory=list)
    complete: bool = True
    warnings: list[str] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    bytes_seen: int = 0
    directories_visited: int = 0

    def mark_incomplete(self, reason: str, warning: str | None = None) -> None:
        self.complete = False
        self.incomplete_reasons.append(reason)
        if warning:
            self.warnings.append(warning)


@dataclass
class CodeScanResult:
    """A bounded code scan with an explicit tri-state result."""

    status: str
    matches: dict[str, list[str]] = field(default_factory=dict)
    files_scanned: int = 0
    bytes_scanned: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_data({
            "status": self.status,
            "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned,
            "matches": {key: sorted(value) for key, value in sorted(self.matches.items())},
            "errors": self.errors,
        })


@dataclass
class RunAudit:
    run_id: str
    run_name: str
    status: str
    run_stage: str
    experiment_id: str | None = None
    experiment_path: str | None = None
    registered_model_name: str | None = None
    source_tables: list[str] = field(default_factory=list)
    metrics_present: list[str] = field(default_factory=list)
    artifacts_present: list[str] = field(default_factory=list)
    model_uris: list[str] = field(default_factory=list)
    json_evidence: list[str] = field(default_factory=list)
    artifact_count: int = 0
    artifact_bytes: int = 0
    complete: bool = True
    incomplete_reasons: list[str] = field(default_factory=list)
    missing_metadata: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)
    inconsistent_values: list[str] = field(default_factory=list)
    job_readiness_risk: list[str] = field(default_factory=list)
    registry_drift: list[str] = field(default_factory=list)
    metric_provenance_risk: list[str] = field(default_factory=list)
    data_semantics_risk: list[str] = field(default_factory=list)
    recommended_patch_location: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def findings(self) -> dict[str, list[str]]:
        return {
            "missing_metadata": sorted(set(self.missing_metadata)),
            "missing_artifacts": sorted(set(self.missing_artifacts)),
            "inconsistent_values": sorted(set(self.inconsistent_values)),
            "job_readiness_risk": sorted(set(self.job_readiness_risk)),
            "registry_drift": sorted(set(self.registry_drift)),
            "metric_provenance_risk": sorted(set(self.metric_provenance_risk)),
            "data_semantics_risk": sorted(set(self.data_semantics_risk)),
            "incomplete_reasons": sorted(set(self.incomplete_reasons)),
        }

    @property
    def is_clean(self) -> bool:
        return self.status == "FINISHED" and self.complete and not any(self.findings.values())

    def to_dict(self) -> dict[str, Any]:
        return redact_data({
            "run_id": self.run_id, "run_name": self.run_name, "status": self.status,
            "run_stage": self.run_stage, "experiment_id": self.experiment_id,
            "experiment_path": self.experiment_path,
            "registered_model_name": self.registered_model_name,
            "source_tables": sorted(set(self.source_tables)),
            "metrics_present": sorted(set(self.metrics_present)),
            "artifacts_present": sorted(set(self.artifacts_present)),
            "model_uris": sorted(set(self.model_uris)),
            "json_evidence": sorted(set(self.json_evidence)),
            "artifact_count": self.artifact_count, "artifact_bytes": self.artifact_bytes,
            "complete": self.complete, "is_clean": self.is_clean,
            "findings": self.findings, "warnings": sorted(set(self.warnings)),
            "recommended_patch_location": sorted(set(self.recommended_patch_location)),
        })


class ArgumentFailure(ValueError):
    """A parser failure rendered through the normal output contract."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentFailure(message)


def redact_text(value: object, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Make every user-visible operational string bounded and credential-safe."""

    text = str(value).strip().replace("\x00", "")
    patterns = (
        (r"(?i)\b(bearer|basic|dpop)\s+[^\s,;]+", r"\1 <redacted>"),
        (r"(?i)\b(authorization|proxy-authorization|api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+", r"\1=<redacted>"),
        (r"(?i)([?&](?:access_token|api[_-]?key|token|secret|password)=)[^&\s]+", r"\1<redacted>"),
        (r"\bsk-[A-Za-z0-9_-]{8,}\b", "<redacted>"),
        (r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b", "<redacted>"),
        (r"(?i)(://)[^\s/@]+(?::[^\s/@]*)?@", r"\1<redacted>@"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return f"{text[:limit]}…" if len(text) > limit else (text or "operation failed")


def redact_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, Mapping):
        return {redact_text(key): redact_data(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return "<nonfinite>"
    return value


def safe_error(exc: BaseException) -> dict[str, str]:
    try:
        raw_code = getattr(exc, "error_code", getattr(exc, "code", "unknown"))
        code = str(raw_code).strip()
    except Exception:
        code = "unknown"
    code = code if re.fullmatch(r"[1-5][0-9]{2}", code) else "unknown"
    try:
        message = redact_text(exc)
    except Exception:
        message = "operation failed"
    return {"type": type(exc).__name__[:80], "code": code, "message": message}


def _mark_incomplete(audit: RunAudit, reason: str, exc: BaseException | None = None) -> None:
    audit.complete = False
    audit.incomplete_reasons.append(reason)
    if exc is not None:
        error = safe_error(exc)
        audit.warnings.append(f"{reason}: {error['type']}: {error['message']}")


def _safe_string(value: object, field_name: str, audit: RunAudit, default: str) -> str:
    if not isinstance(value, str):
        _mark_incomplete(audit, f"invalid {field_name}")
        return default
    clean = value.strip()
    if not clean or len(clean) > MAX_EVIDENCE_VALUE_LENGTH:
        _mark_incomplete(audit, f"invalid {field_name}")
        return default
    return clean


def _normalize_string_map(raw: object, label: str, audit: RunAudit) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        _mark_incomplete(audit, f"malformed {label} map")
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            _mark_incomplete(audit, f"malformed {label} map")
            continue
        key, value = key.strip(), value.strip()
        if not value or len(key) > MAX_EVIDENCE_VALUE_LENGTH or len(value) > MAX_EVIDENCE_VALUE_LENGTH:
            _mark_incomplete(audit, f"malformed {label} map")
            continue
        result[key] = value
    return result


def _normalize_metrics(raw: object, audit: RunAudit) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        _mark_incomplete(audit, "malformed metrics map")
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or not _is_finite_number(value):
            _mark_incomplete(audit, "malformed metrics map")
            continue
        result[key.strip()] = float(value)
    return result


def first_param(params: Mapping[str, str], logical_name: str) -> str | None:
    for name in PARAM_ALIASES.get(logical_name, (logical_name,)):
        value = params.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def source_tables(params: Mapping[str, str]) -> list[str]:
    tables: list[str] = []
    for name in PARAM_ALIASES["source_table"]:
        value = params.get(name)
        if isinstance(value, str):
            tables.extend(part.strip() for part in value.split(",") if part.strip())
    return sorted(set(tables))


def any_metric(metrics: Mapping[str, float], names: Iterable[str]) -> bool:
    metric_names = {name.lower() for name in metrics}
    return any(
        alias.lower() in metric_names
        for name in names
        for alias in METRIC_ALIASES.get(name, (name,))
    )


def _is_nonnegative_int(value: str | None) -> bool:
    return value is not None and bool(re.fullmatch(r"0|[1-9][0-9]*", value))


def _is_string(value: object) -> bool:
    return isinstance(value, str) and value == value.strip() and bool(value)


def _is_local_three_part_identifier(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_-]*\.[A-Za-z0-9_][A-Za-z0-9_-]*\.[A-Za-z0-9_][A-Za-z0-9_-]*",
            value,
        )
    )


def _is_uc_model_name(value: str) -> bool:
    return _is_local_three_part_identifier(value)


def _parse_fixed_timestamp(value: str | None) -> datetime | None:
    if not _is_string(value) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _is_valid_timezone(value: str | None) -> bool:
    if not _is_string(value):
        return False
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return False
    return True


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_relative_python_path(value: object) -> bool:
    if not _is_string(value) or not value.endswith(".py") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and bool(path.parts) and all(part not in {".", ".."} for part in path.parts)


def _parse_models_uri(value: object) -> tuple[str, str | None] | None:
    """Parse the narrow local-policy models URI grammar without prefix matching."""

    prefix = "models:/"
    if not _is_string(value) or value[: len(prefix)] != prefix:
        return None
    remainder = value[len(prefix) :]
    if "@" in remainder:
        if remainder.count("@") != 1 or "/" in remainder:
            return None
        model_name, alias = remainder.split("@", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", alias):
            return None
        selector = f"@{alias}"
    elif "/" in remainder:
        if remainder.count("/") != 1:
            return None
        model_name, version = remainder.split("/", 1)
        if not re.fullmatch(r"[1-9][0-9]*", version):
            return None
        selector = f"/{version}"
    else:
        model_name, selector = remainder, None
    return (model_name, selector) if _is_uc_model_name(model_name) else None


def _effective_stage_requirements(stage: str) -> StageRequirements:
    parameters: list[str] = []
    json_artifacts: list[str] = []
    artifacts: list[str] = []
    for stage_name in RUN_STAGES[: RUN_STAGES.index(stage) + 1]:
        requirement = STAGE_REQUIREMENTS[stage_name]
        parameters.extend(requirement.parameters)
        json_artifacts.extend(requirement.json_artifacts)
        artifacts.extend(requirement.artifacts)
    return StageRequirements(tuple(parameters), tuple(json_artifacts), tuple(artifacts))


def _normalise_artifact_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.strip()
    if not path or path[:1] == "/" or "\\" in path or len(path) > MAX_EVIDENCE_VALUE_LENGTH:
        return None
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _normalise_file_info(raw: object) -> tuple[ArtifactEntry | None, dict[str, str] | None]:
    """Read every SDK FileInfo property behind one exception boundary."""

    try:
        raw_path = getattr(raw, "path")
        raw_is_dir = getattr(raw, "is_dir")
        raw_size = getattr(raw, "file_size")
    except Exception as exc:
        return None, safe_error(exc)
    path = _normalise_artifact_path(raw_path)
    if path is None or not isinstance(raw_is_dir, bool):
        return None, {"type": "SchemaError", "code": "unknown", "message": "malformed artifact listing entry"}
    if raw_is_dir:
        return ArtifactEntry(path, True, 0), None
    if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
        return None, {"type": "SchemaError", "code": "unknown", "message": "artifact file size is unavailable or invalid"}
    return ArtifactEntry(path, False, raw_size), None


def _read_directory_entries(
    listed_raw: object,
    *,
    limit: int,
) -> tuple[list[ArtifactEntry], dict[str, str] | None, bool]:
    """Bound iterator consumption before normalizing or sorting a directory."""

    if isinstance(listed_raw, (str, bytes)):
        return [], {"type": "SchemaError", "code": "unknown", "message": "list_artifacts returned a non-iterable collection"}, False
    try:
        iterator = iter(listed_raw)
    except Exception as exc:
        return [], safe_error(exc), False
    entries: list[ArtifactEntry] = []
    for index in range(limit + 1):
        try:
            raw = next(iterator)
        except StopIteration:
            return sorted(entries, key=lambda entry: (entry.path, entry.is_dir)), None, False
        except Exception as exc:
            return [], safe_error(exc), False
        if index == limit:
            return sorted(entries, key=lambda entry: (entry.path, entry.is_dir)), None, True
        entry, error = _normalise_file_info(raw)
        if error is not None:
            return [], error, False
        assert entry is not None
        entries.append(entry)
    return sorted(entries, key=lambda entry: (entry.path, entry.is_dir)), None, False


def list_artifacts_recursive(
    client: Any,
    run_id: str,
    *,
    max_depth: int = MAX_ARTIFACT_DEPTH,
    max_count: int = MAX_ARTIFACT_COUNT,
    max_bytes: int = MAX_ARTIFACT_BYTES,
    max_directories: int = MAX_ARTIFACT_DIRECTORIES,
) -> ArtifactInventory:
    """Iteratively list artifacts with stable ordering, visited paths, and caps."""

    inventory = ArtifactInventory()
    queue: deque[tuple[str | None, int]] = deque([(None, 0)])
    visited_dirs, queued_dirs, seen_paths = {""}, {""}, set()
    while queue and inventory.complete:
        if inventory.directories_visited >= max_directories:
            inventory.mark_incomplete("artifact directory limit reached", "artifact traversal was truncated at the directory limit")
            break
        path, _depth = queue.popleft()
        inventory.directories_visited += 1
        try:
            listed_raw = client.list_artifacts(run_id, path)
        except Exception as exc:
            error = safe_error(exc)
            inventory.mark_incomplete("artifact listing error", f"artifact listing error: {error['type']}: {error['message']}")
            break
        remaining = max_count - len(inventory.entries)
        if remaining <= 0:
            inventory.mark_incomplete("artifact count limit reached", "artifact traversal was truncated at the entry limit")
            break
        listed, error, truncated = _read_directory_entries(
            listed_raw,
            limit=min(MAX_ARTIFACTS_PER_DIRECTORY, remaining),
        )
        if error is not None:
            inventory.mark_incomplete("malformed artifact listing", f"artifact listing error: {error['type']}: {error['message']}")
            break
        if truncated:
            if remaining <= MAX_ARTIFACTS_PER_DIRECTORY:
                inventory.mark_incomplete("artifact count limit reached", "artifact traversal was truncated at the entry limit")
            else:
                inventory.mark_incomplete("artifact directory entry limit reached", "artifact traversal was truncated at the per-directory entry limit")
            break
        for entry in listed:
            artifact_path, is_dir, size = entry.path, entry.is_dir, entry.size
            if artifact_path in seen_paths:
                inventory.warnings.append(
                    "artifact directory cycle or duplicate ignored"
                    if is_dir
                    else "duplicate artifact entry ignored"
                )
                continue
            seen_paths.add(artifact_path)
            if len(inventory.entries) >= max_count:
                inventory.mark_incomplete("artifact count limit reached", "artifact traversal was truncated at the entry limit")
                break
            if inventory.bytes_seen + size > max_bytes:
                inventory.mark_incomplete("artifact byte limit reached", "artifact traversal was truncated at the byte limit")
                break
            inventory.entries.append(ArtifactEntry(artifact_path, is_dir, size))
            inventory.bytes_seen += size
            if is_dir:
                child_depth = len(PurePosixPath(artifact_path).parts)
                if child_depth > max_depth:
                    inventory.mark_incomplete("artifact depth limit reached", "artifact traversal was truncated at the depth limit")
                    break
                if artifact_path in visited_dirs or artifact_path in queued_dirs:
                    inventory.warnings.append("artifact directory cycle or duplicate ignored")
                    continue
                if inventory.directories_visited + len(queue) >= max_directories:
                    inventory.mark_incomplete("artifact directory limit reached", "artifact traversal was truncated at the directory limit")
                    break
                visited_dirs.add(artifact_path)
                queued_dirs.add(artifact_path)
                queue.append((artifact_path, child_depth))
    inventory.entries.sort(key=lambda entry: (entry.path, entry.is_dir))
    inventory.warnings = sorted(set(inventory.warnings))
    inventory.incomplete_reasons = sorted(set(inventory.incomplete_reasons))
    return inventory


def _matches_artifact_requirement(path: str, requirement: str) -> bool:
    basename = PurePosixPath(path).name
    return any(
        path == expected or ("/" not in expected and basename == expected)
        for expected in ARTIFACT_PATHS[requirement]
    )


def _find_required_artifact(entries: Sequence[ArtifactEntry], requirement: str) -> ArtifactEntry | None:
    matching = [entry for entry in entries if not entry.is_dir and _matches_artifact_requirement(entry.path, requirement)]
    return min(matching, key=lambda entry: entry.path) if matching else None


def _nonempty_mapping(value: object) -> bool:
    return isinstance(value, Mapping) and bool(value)


def _valid_feature_list(value: object) -> bool:
    return (
        _nonempty_mapping(value)
        and isinstance(value.get("features"), list)
        and bool(value["features"])
        and all(_is_string(item) for item in value["features"])
        and len(set(value["features"])) == len(value["features"])
    )


def _valid_label_map(value: object) -> bool:
    if not _nonempty_mapping(value) or not all(_is_string(key) for key in value):
        return False
    labels = list(value.values())
    return all(isinstance(label, int) and not isinstance(label, bool) and label >= 0 for label in labels) and sorted(labels) == list(range(len(labels)))


def _valid_metric_formulas(value: object) -> bool:
    required = {"canonical_metric", "formula", "class_label", "averaging", "denominator", "threshold"}
    return _nonempty_mapping(value) and all(
        isinstance(item, Mapping)
        and required.issubset(item)
        and all(_is_string(item[key]) for key in required - {"threshold"})
        and item["canonical_metric"] in LOCAL_CANONICAL_METRICS
        and item["averaging"] in LOCAL_METRIC_AVERAGING
        and _is_finite_number(item["threshold"])
        and 0.0 <= float(item["threshold"]) <= 1.0
        for item in value.values()
    )


def _valid_confusion_matrix(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(row, list) and len(row) == len(value) for row in value)
        and all(_is_finite_number(cell) and float(cell) >= 0 for row in value for cell in row)
    )


def _valid_job_contract(value: object) -> bool:
    if not _nonempty_mapping(value) or not _is_relative_python_path(value.get("entrypoint")):
        return False
    parameters = value.get("parameters")
    if not _nonempty_mapping(parameters) or not all(_is_string(key) for key in parameters):
        return False
    timestamp = parameters.get("at_timestamp", parameters.get("AT_TIMESTAMP"))
    return _parse_fixed_timestamp(timestamp) is not None and all(
        isinstance(item, (str, int, float)) and not isinstance(item, bool)
        for item in parameters.values()
    )


def _valid_job_smoke(value: object) -> bool:
    return _nonempty_mapping(value) and _is_string(value.get("command")) and "--at-timestamp" in value["command"] and "\n" not in value["command"]


def _valid_selected_model(value: object) -> bool:
    return _nonempty_mapping(value) and value.get("objective") in LOCAL_CANONICAL_METRICS and _is_string(value.get("run_id")) and _is_string(value.get("registered_model_name"))


def _valid_promotion_handoff(value: object) -> bool:
    return _nonempty_mapping(value) and _is_string(value.get("run_id")) and _is_string(value.get("registered_model_name")) and _is_uc_model_name(value["registered_model_name"])


def _valid_serving_contract(value: object) -> bool:
    return _nonempty_mapping(value) and all(_is_string(value.get(name)) for name in ("run_id", "registered_model_name", "model_uri", "input_schema", "output_schema", "null_policy")) and value["null_policy"] in LOCAL_NULL_POLICIES and _parse_models_uri(value["model_uri"]) is not None


def _valid_batch_input(value: object) -> bool:
    return _nonempty_mapping(value) and all(_is_string(value.get(name)) for name in ("run_id", "registered_model_name", "source_table", "input_schema")) and _is_local_three_part_identifier(value["source_table"])


def _valid_batch_output(value: object) -> bool:
    return _nonempty_mapping(value) and all(_is_string(value.get(name)) for name in ("run_id", "registered_model_name", "output_schema"))


def _valid_null_policy(value: object) -> bool:
    if not _nonempty_mapping(value) or value.get("policy") not in LOCAL_NULL_POLICIES:
        return False
    return any(
        isinstance(value.get(name), int) and not isinstance(value.get(name), bool) and value[name] >= 0
        for name in ("skipped_null_rows", "unscorable_rows")
    )


def _valid_source_freshness(value: object) -> bool:
    return _nonempty_mapping(value) and _parse_fixed_timestamp(value.get("checked_at")) is not None


JSON_VALIDATORS: dict[str, Callable[[object], bool]] = {
    "feature_list": _valid_feature_list, "label_map": _valid_label_map,
    "metric_formulas": _valid_metric_formulas, "confusion_matrix": _valid_confusion_matrix,
    "job_parameter_contract": _valid_job_contract, "job_smoke": _valid_job_smoke,
    "selected_model_metadata": _valid_selected_model, "promotion_handoff": _valid_promotion_handoff,
    "serving_contract": _valid_serving_contract, "batch_input_contract": _valid_batch_input,
    "batch_output_contract": _valid_batch_output, "null_policy": _valid_null_policy,
    "source_freshness": _valid_source_freshness,
}


def _has_bound_identity(requirement: str, value: object, audit: RunAudit) -> bool:
    if requirement not in {"selected_model_metadata", "promotion_handoff", "serving_contract", "batch_input_contract", "batch_output_contract"}:
        return True
    if not isinstance(value, Mapping):
        return False
    if value.get("run_id") != audit.run_id or value.get("registered_model_name") != audit.registered_model_name:
        return False
    if requirement == "serving_contract":
        parsed_uri = _parse_models_uri(value["model_uri"])
        if parsed_uri is None or parsed_uri[0] != audit.registered_model_name:
            return False
    return requirement != "batch_input_contract" or value.get("source_table") in audit.source_tables


def _strict_json_loads(payload: bytes) -> object:
    return json.loads(
        payload.decode("utf-8"),
        parse_constant=lambda _constant: (_ for _ in ()).throw(ValueError("non-finite JSON constant")),
    )


def _download_and_validate_json(client: Any, run_id: str, artifact: ArtifactEntry, requirement: str, audit: RunAudit) -> None:
    if artifact.size > MAX_JSON_ARTIFACT_BYTES:
        _mark_incomplete(audit, "JSON artifact byte limit reached")
        _record_artifact_finding(audit, requirement, f"{requirement} JSON artifact exceeds the byte limit")
        return
    try:
        with tempfile.TemporaryDirectory(prefix="mlflow-run-auditor-") as destination:
            local_path = Path(client.download_artifacts(run_id, artifact.path, destination))
            if not local_path.is_file():
                raise ValueError("downloaded artifact is not a regular file")
            with local_path.open("rb") as handle:
                payload = handle.read(MAX_JSON_ARTIFACT_BYTES + 1)
            if len(payload) > MAX_JSON_ARTIFACT_BYTES:
                raise ValueError("downloaded artifact exceeds the JSON byte limit")
            parsed = _strict_json_loads(payload)
    except Exception as exc:
        _mark_incomplete(audit, f"unable to inspect {requirement} JSON artifact", exc)
        return
    if not JSON_VALIDATORS[requirement](parsed):
        _record_artifact_finding(audit, requirement, f"invalid {requirement} JSON artifact")
    elif not _has_bound_identity(requirement, parsed, audit):
        audit.inconsistent_values.append(f"{requirement} JSON artifact identity does not match the logged run")
    else:
        audit.json_evidence.append(requirement)


def _record_artifact_finding(audit: RunAudit, requirement: str, message: str) -> None:
    (audit.job_readiness_risk if requirement in JOB_ARTIFACT_REQUIREMENTS else audit.missing_artifacts).append(message)


def _audit_required_artifacts(audit: RunAudit, client: Any, run_id: str, entries: Sequence[ArtifactEntry], requirements: StageRequirements) -> None:
    for requirement in requirements.artifacts:
        if _find_required_artifact(entries, requirement) is None:
            _record_artifact_finding(audit, requirement, f"missing {requirement} artifact")
    for requirement in requirements.json_artifacts:
        artifact = _find_required_artifact(entries, requirement)
        if artifact is None:
            _record_artifact_finding(audit, requirement, f"missing {requirement} JSON artifact")
        else:
            _download_and_validate_json(client, run_id, artifact, requirement, audit)


def _model_uri(run_id: str, directory: str) -> str:
    return f"runs:/{run_id}/{directory}" if directory else f"runs:/{run_id}"


def _is_safe_run_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value))


def has_loadable_input_example_metadata(metadata: object) -> bool:
    """Return whether ModelInfo metadata has the fields MLflow needs to load an example."""
    if not isinstance(metadata, Mapping):
        return False
    try:
        artifact_path = metadata["artifact_path"]
        example_type = metadata["type"]
    except (KeyError, TypeError, ValueError):
        return False
    return (
        isinstance(artifact_path, str)
        and bool(artifact_path.strip())
        and isinstance(example_type, str)
        and example_type in SUPPORTED_INPUT_EXAMPLE_TYPES
    )


def _audit_models(
    audit: RunAudit,
    run_id: str,
    entries: Sequence[ArtifactEntry],
    loader: Callable[[str], Any] | None,
    input_example_loader: Callable[[str], object] | None,
    input_examples_bounded: bool,
) -> None:
    directories = sorted({str(PurePosixPath(entry.path).parent) if str(PurePosixPath(entry.path).parent) != "." else "" for entry in entries if not entry.is_dir and PurePosixPath(entry.path).name == "MLmodel"})
    if not directories:
        audit.missing_artifacts.append("model artifact containing MLmodel")
        return
    if loader is None:
        try:
            from mlflow.models import get_model_info
        except Exception as exc:
            _mark_incomplete(audit, "MLflow model inspector unavailable", exc)
            return
        loader = get_model_info
    for directory in directories:
        uri = _model_uri(run_id, directory)
        audit.model_uris.append(uri)
        try:
            info = loader(uri)
        except Exception as exc:
            _mark_incomplete(audit, "unable to inspect model metadata", exc)
            continue
        if getattr(info, "signature", None) is None:
            audit.missing_metadata.append(f"model signature for {uri}")
        if not has_loadable_input_example_metadata(getattr(info, "saved_input_example_info", None)):
            audit.missing_metadata.append(f"input example for {uri}")
            continue
        if not input_examples_bounded:
            audit.missing_metadata.append("input example cannot be verified within artifact limits")
            _mark_incomplete(audit, "artifact inventory limit prevents input example verification")
            continue
        if input_example_loader is None:
            try:
                from mlflow.models import Model
            except Exception:
                audit.missing_metadata.append("input example could not be loaded")
                _mark_incomplete(audit, "MLflow input example loader unavailable")
                continue

            def load_input_example(model_uri: str) -> object:
                return Model.load(model_uri).load_input_example(model_uri)

            input_example_loader = load_input_example
        try:
            input_example = input_example_loader(uri)
        except Exception:
            audit.missing_metadata.append("input example could not be loaded")
            _mark_incomplete(audit, "unable to load model input example")
            continue
        if input_example is None:
            audit.missing_metadata.append("input example could not be loaded")
            _mark_incomplete(audit, "model input example loader returned no example")


def _audit_stage_metadata(audit: RunAudit, params: Mapping[str, str], requirements: StageRequirements) -> None:
    for name in requirements.parameters:
        value = params.get(name) if name in {"train_rows", "val_rows"} else first_param(params, name)
        if not value:
            destination = audit.job_readiness_risk if name in {"entrypoint", "job_parameters"} else audit.missing_metadata
            destination.append(f"missing {name}" if destination is audit.job_readiness_risk else name)
    for name in ("train_rows", "val_rows", "skipped_null_rows", "unscorable_rows"):
        value = params.get(name)
        if value is not None and not _is_nonnegative_int(value):
            audit.missing_metadata.append(f"{name} must be a nonnegative integer")


def _audit_table_semantics(audit: RunAudit, params: Mapping[str, str]) -> None:
    if not audit.source_tables:
        return
    if any(not _is_local_three_part_identifier(table) for table in audit.source_tables):
        audit.data_semantics_risk.append("source table is not a local three-part identifier")
    if not _is_nonnegative_int(first_param(params, "dataset_version")):
        audit.data_semantics_risk.append("dataset version must be a nonnegative integer")
    if _parse_fixed_timestamp(first_param(params, "at_timestamp")) is None:
        audit.data_semantics_risk.append("AT_TIMESTAMP must be a fixed timezone-aware ISO timestamp")
    if not _is_valid_timezone(first_param(params, "timezone")):
        audit.data_semantics_risk.append("timezone must be an IANA timezone")
    scd_predicate = params.get("scd2_predicate")
    table_version = params.get("table_version")
    if _is_string(scd_predicate):
        if "valid_from" not in scd_predicate or "valid_to" not in scd_predicate:
            audit.data_semantics_risk.append("SCD2 predicate must reference valid_from and valid_to")
    elif not _is_nonnegative_int(table_version):
        audit.data_semantics_risk.append("SCD2/table-version evidence is invalid")
    start_offset, end_offset = first_param(params, "train_start_offset"), first_param(params, "train_end_offset")
    start_date, end_date = params.get("training_start_date"), params.get("training_end_date")
    if start_offset is not None or end_offset is not None:
        if not _is_nonnegative_int(start_offset) or not _is_nonnegative_int(end_offset):
            audit.data_semantics_risk.append("training window offsets must be nonnegative integers")
        elif int(start_offset) * 86_400 < int(end_offset) * 3_600:
            audit.data_semantics_risk.append("training window offsets are in the wrong order")
    elif _parse_fixed_timestamp(start_date) is None or _parse_fixed_timestamp(end_date) is None:
        audit.missing_metadata.append("bounded train window offsets or explicit train start/end")
    elif _parse_fixed_timestamp(start_date) >= _parse_fixed_timestamp(end_date):
        audit.data_semantics_risk.append("training start timestamp must precede training end timestamp")
    for label, start_name, end_name in (
        ("validation", "validation_start_offset", "validation_end_offset"),
        ("test", "test_start_offset", "test_end_offset"),
    ):
        window_start, window_end = first_param(params, start_name), first_param(params, end_name)
        if window_start is None and window_end is None:
            continue
        if not _is_nonnegative_int(window_start) or not _is_nonnegative_int(window_end):
            audit.data_semantics_risk.append(f"{label} window offsets must be nonnegative integers")
        elif int(window_start) * 86_400 < int(window_end) * 3_600:
            audit.data_semantics_risk.append(f"{label} window offsets are in the wrong order")
    null_policy = first_param(params, "null_policy")
    if null_policy is None:
        audit.data_semantics_risk.append("table-backed run missing null policy")
    elif null_policy not in LOCAL_NULL_POLICIES:
        audit.data_semantics_risk.append("null policy is outside the local readiness policy")
    freshness = first_param(params, "source_freshness")
    if freshness is None:
        audit.data_semantics_risk.append("table-backed run missing source freshness evidence")
    elif _parse_fixed_timestamp(freshness) is None:
        audit.data_semantics_risk.append("source freshness must be a fixed timezone-aware ISO timestamp")
    if "skipped_null_rows" not in params and "unscorable_rows" not in params:
        audit.data_semantics_risk.append("table-backed run missing skipped/unscorable row counts")
    if any(marker in value.lower() for value in params.values() for marker in ("now", "current_timestamp", "current_date", "datetime.now", "today")):
        audit.inconsistent_values.append("MLflow parameter uses a moving time value")


def _reconcile_table_artifacts(
    audit: RunAudit,
    params: Mapping[str, str],
    entries: Sequence[ArtifactEntry],
    client: Any,
    run_id: str,
) -> None:
    if not audit.source_tables:
        return
    if first_param(params, "null_policy") is None:
        artifact = _find_required_artifact(entries, "null_policy")
        if artifact is not None:
            _download_and_validate_json(client, run_id, artifact, "null_policy", audit)
    if first_param(params, "source_freshness") is None:
        artifact = _find_required_artifact(entries, "source_freshness")
        if artifact is not None:
            _download_and_validate_json(client, run_id, artifact, "source_freshness", audit)
    if first_param(params, "null_policy") in LOCAL_NULL_POLICIES or "null_policy" in audit.json_evidence:
        audit.data_semantics_risk = [risk for risk in audit.data_semantics_risk if risk != "table-backed run missing null policy"]
    if "null_policy" in audit.json_evidence:
        audit.data_semantics_risk = [risk for risk in audit.data_semantics_risk if risk != "table-backed run missing skipped/unscorable row counts"]
    if _parse_fixed_timestamp(first_param(params, "source_freshness")) is not None or "source_freshness" in audit.json_evidence:
        audit.data_semantics_risk = [risk for risk in audit.data_semantics_risk if risk != "table-backed run missing source freshness evidence"]


def _audit_metrics(audit: RunAudit, params: Mapping[str, str], metrics: Mapping[str, float]) -> None:
    if not any_metric(metrics, ("accuracy", "f1", "precision", "recall")):
        audit.missing_metadata.append("standard evaluation metrics")
    if not any_metric(metrics, ("auc",)):
        audit.metric_provenance_risk.append("AUC/ROC AUC metric is absent when classifier scoring is audited")
    if any("precision" in name.lower() and "percent" in name.lower() for name in metrics):
        for name in ("threshold", "threshold_metric", "positive_class", "metric_averaging"):
            if not first_param(params, name):
                audit.metric_provenance_risk.append(f"precision percent metric missing {name}")


def _audit_registry(audit: RunAudit, params: Mapping[str, str], expected: str | None, stale_names: Sequence[str], code_scan: CodeScanResult) -> None:
    logged = first_param(params, "registered_model_name")
    if logged and not _is_uc_model_name(logged):
        audit.registry_drift.append("registered model name is not a three-part UC path")
    if expected and logged != expected:
        audit.registry_drift.append("registered model name does not match the expected UC model name")
    if expected and code_scan.status == "complete" and not code_scan.matches.get(expected):
        audit.registry_drift.append("expected registered model name is absent from scanned code")
    inference_loader = first_param(params, "inference_loader")
    if audit.run_stage in {"serving-candidate", "batch-inference-dependency"} and inference_loader:
        parsed_uri = _parse_models_uri(inference_loader)
        if parsed_uri is None:
            audit.registry_drift.append("inference loader is not a valid local-policy models URI")
        elif parsed_uri[0] != logged:
            audit.registry_drift.append("inference loader model name does not exactly match the logged registered model")
    for stale in stale_names:
        if any(stale in value for value in params.values()):
            audit.registry_drift.append("stale model name found in MLflow parameter content")
        if code_scan.status == "complete" and code_scan.matches.get(stale):
            audit.registry_drift.append("stale model name found in scanned code")


def _add_patch_locations(audit: RunAudit) -> None:
    if audit.missing_metadata:
        audit.recommended_patch_location.append("training entrypoint before MLflow model logging")
    if audit.missing_artifacts:
        audit.recommended_patch_location.append("training entrypoint after evaluation artifacts are computed")
    if audit.registry_drift:
        audit.recommended_patch_location.append("registered-model constants, job config, docs, and inference loader")
    if audit.job_readiness_risk:
        audit.recommended_patch_location.append("job entrypoint parameter contract and smoke evidence")
    if audit.metric_provenance_risk:
        audit.recommended_patch_location.append("evaluation block that logs metrics and metric formulas")
    if audit.data_semantics_risk:
        audit.recommended_patch_location.append("feature assembly and source freshness/null handling block")


def _audit_run_impl(
    run: Any,
    *,
    client: Any,
    experiment: Any,
    run_stage: str,
    expected_registered_model_name: str | None = None,
    stale_model_names: Sequence[str] = (),
    code_scan: CodeScanResult | None = None,
    model_info_loader: Callable[[str], Any] | None = None,
    input_example_loader: Callable[[str], object] | None = None,
) -> RunAudit:
    """Audit one run without MLflow UI state or unbounded artifact reads."""

    info, data = getattr(run, "info", None), getattr(run, "data", None)
    audit = RunAudit("(invalid-run-id)", "(unnamed)", "UNKNOWN", run_stage)
    audit.experiment_id = redact_text(getattr(experiment, "experiment_id", "")) if getattr(experiment, "experiment_id", None) is not None else None
    audit.experiment_path = redact_text(getattr(experiment, "name", "")) if getattr(experiment, "name", None) is not None else None
    audit.run_id = _safe_string(getattr(info, "run_id", None), "run ID", audit, "(invalid-run-id)")
    audit.run_name = _safe_string(getattr(info, "run_name", "(unnamed)"), "run name", audit, "(unnamed)")
    audit.status = _safe_string(getattr(info, "status", None), "run status", audit, "UNKNOWN").upper()
    if not _is_safe_run_id(audit.run_id):
        _mark_incomplete(audit, "unsafe run identity")
        _add_patch_locations(audit)
        return audit
    params = _normalize_string_map(getattr(data, "params", None), "params", audit)
    metrics = _normalize_metrics(getattr(data, "metrics", None), audit)
    audit.registered_model_name = first_param(params, "registered_model_name")
    audit.source_tables, audit.metrics_present = source_tables(params), sorted(metrics)
    inventory = list_artifacts_recursive(client, audit.run_id)
    audit.artifacts_present = [entry.path for entry in inventory.entries]
    audit.artifact_count, audit.artifact_bytes = len(inventory.entries), inventory.bytes_seen
    if not inventory.complete:
        audit.complete = False
        audit.incomplete_reasons.extend(inventory.incomplete_reasons)
    audit.warnings.extend(inventory.warnings)
    requirements = _effective_stage_requirements(run_stage)
    _audit_stage_metadata(audit, params, requirements)
    _audit_table_semantics(audit, params)
    _audit_required_artifacts(audit, client, audit.run_id, inventory.entries, requirements)
    _reconcile_table_artifacts(audit, params, inventory.entries, client, audit.run_id)
    input_examples_bounded = not any(
        reason in INPUT_EXAMPLE_UNPROVEN_INVENTORY_REASONS
        for reason in inventory.incomplete_reasons
    )
    _audit_models(
        audit,
        audit.run_id,
        inventory.entries,
        model_info_loader,
        input_example_loader,
        input_examples_bounded,
    )
    _audit_metrics(audit, params, metrics)
    _audit_registry(audit, params, expected_registered_model_name, stale_model_names, code_scan or CodeScanResult("not_requested"))
    _add_patch_locations(audit)
    audit.incomplete_reasons, audit.warnings = sorted(set(audit.incomplete_reasons)), sorted(set(audit.warnings))
    return audit


def _incomplete_audit_from_exception(run: Any, run_stage: str, exc: BaseException) -> RunAudit:
    """Return a safe incomplete record without touching remote evidence again."""

    audit = RunAudit("(unavailable-run-id)", "(unavailable)", "UNKNOWN", run_stage)
    try:
        candidate = getattr(getattr(run, "info"), "run_id")
        if isinstance(candidate, str) and _is_safe_run_id(candidate.strip()):
            audit.run_id = candidate.strip()
    except Exception:
        pass
    _mark_incomplete(audit, "audit execution error", exc)
    _add_patch_locations(audit)
    return audit


def audit_run(
    run: Any,
    *,
    client: Any,
    experiment: Any,
    run_stage: str,
    expected_registered_model_name: str | None = None,
    stale_model_names: Sequence[str] = (),
    code_scan: CodeScanResult | None = None,
    model_info_loader: Callable[[str], Any] | None = None,
    input_example_loader: Callable[[str], object] | None = None,
) -> RunAudit:
    """Contain every per-run operational failure in a redacted incomplete audit."""

    try:
        return _audit_run_impl(
            run,
            client=client,
            experiment=experiment,
            run_stage=run_stage,
            expected_registered_model_name=expected_registered_model_name,
            stale_model_names=stale_model_names,
            code_scan=code_scan,
            model_info_loader=model_info_loader,
            input_example_loader=input_example_loader,
        )
    except Exception as exc:
        return _incomplete_audit_from_exception(run, run_stage, exc)


def scan_code_path(code_path: str | None, names: Sequence[str]) -> CodeScanResult:
    """Scan only supplied code, bound files/bytes, and expose scan completeness."""

    names = sorted({name.strip() for name in names if isinstance(name, str) and name.strip()})
    if code_path is None:
        return CodeScanResult("not_requested", {name: [] for name in names})
    root = Path(code_path)
    try:
        if not root.exists():
            return CodeScanResult("failed", {name: [] for name in names}, errors=[{"type": "PathError", "code": "missing_path", "message": "supplied code path does not exist"}])
        if not names:
            return CodeScanResult("complete")
        if root.is_file():
            files, display_root = [root], root.parent
        else:
            display_root = root
            files = []
            walk_entries = 0
            for item in root.rglob("*"):
                walk_entries += 1
                if walk_entries > MAX_CODE_SCAN_WALK_ENTRIES:
                    return CodeScanResult("failed", {name: [] for name in names}, errors=[{"type": "LimitError", "code": "walk_limit", "message": "code scan directory traversal limit reached"}])
                if item.is_symlink():
                    return CodeScanResult("failed", {name: [] for name in names}, errors=[{"type": "PathError", "code": "symlink", "message": "code scan does not follow symbolic links"}])
                if not item.is_file() or item.suffix.lower() not in {".py", ".yml", ".yaml", ".json", ".md"}:
                    continue
                if len(files) >= MAX_CODE_SCAN_FILES:
                    return CodeScanResult("failed", {name: [] for name in names}, errors=[{"type": "LimitError", "code": "file_limit", "message": "code scan file limit reached"}])
                files.append(item)
            files.sort(key=lambda item: item.as_posix())
    except Exception as exc:
        return CodeScanResult("failed", {name: [] for name in names}, errors=[safe_error(exc)])
    result = CodeScanResult("complete", {name: [] for name in names})
    for file_path in files:
        try:
            if file_path.is_symlink():
                raise ValueError("code scan does not follow symbolic links")
            size = file_path.stat().st_size
            if size < 0 or result.bytes_scanned + size > MAX_CODE_SCAN_BYTES:
                raise ValueError("code scan byte limit reached")
            text = file_path.read_bytes().decode("utf-8")
            relative_path = file_path.relative_to(display_root).as_posix()
        except Exception as exc:
            result.status = "failed"
            result.errors.append(safe_error(exc))
            break
        result.files_scanned += 1
        result.bytes_scanned += size
        for name in names:
            if name in text:
                result.matches[name].append(relative_path)
    for paths in result.matches.values():
        paths.sort()
    return result


def find_experiment(mlflow: Any, name_or_id: str) -> Any:
    experiment = mlflow.get_experiment_by_name(name_or_id)
    if experiment is not None:
        return experiment
    experiment = mlflow.get_experiment(name_or_id)
    if experiment is not None:
        return experiment
    raise ValueError("experiment was not found")


def build_parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(description="Audit MLflow runs for operational readiness")
    parser.add_argument("experiment", help="Experiment name or ID")
    parser.add_argument("--profile", required=True, help="Databricks CLI profile for MLflow tracking")
    parser.add_argument("--last", default="5", help="Number of recent runs to audit (1-1000)")
    parser.add_argument("--json", action="store_true", help="Output the stable JSON envelope")
    parser.add_argument("--run-stage", choices=RUN_STAGES, default="prototype", help="Lifecycle stage to audit against")
    parser.add_argument("--expected-registered-model-name", help="Expected three-part UC registered model name")
    parser.add_argument("--stale-model-name", action="append", default=[], help="Old model name that must not remain in params or code")
    parser.add_argument("--code-path", help="Optional code file or directory to scan for model-name drift")
    return parser


def _parse_last(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ArgumentFailure("--last must be an integer from 1 through 1000")
    result = int(value)
    if result > 1000:
        raise ArgumentFailure("--last must be an integer from 1 through 1000")
    return result


def _parse_profile(value: object) -> str:
    pattern = rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_PROFILE_LENGTH - 1}}}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ArgumentFailure("--profile must be a 1-128 character named Databricks profile")
    return value


def _run_sort_key(run: Any) -> tuple[int, int, str]:
    try:
        info = getattr(run, "info", None)
        start_time = getattr(info, "start_time", None)
        run_id = getattr(info, "run_id", "")
        if not isinstance(run_id, str) or not _is_safe_run_id(run_id.strip()):
            return (2, 0, "")
        return (0, -start_time, run_id) if isinstance(start_time, int) and not isinstance(start_time, bool) else (1, 0, run_id)
    except Exception:
        return (2, 0, "")


def build_envelope(*, requested_count: int, audits: Sequence[RunAudit], code_scan: CodeScanResult, operational_errors: Sequence[dict[str, str]] = ()) -> dict[str, Any]:
    found_count = len(audits)
    clean_count = sum(audit.is_clean for audit in audits)
    complete = not operational_errors and code_scan.status != "failed" and all(audit.complete for audit in audits)
    if operational_errors:
        decision = "operational_error"
    elif not complete:
        decision = "incomplete"
    elif found_count == 0:
        decision = "no_qualifying_runs"
    elif clean_count == found_count:
        decision = "clean"
    else:
        decision = "findings"
    return {
        "schema_version": 2, "decision": decision, "complete": complete,
        "requested_count": requested_count, "found_count": found_count, "clean_count": clean_count,
        "code_scan": code_scan.to_dict(), "runs": [audit.to_dict() for audit in audits],
        "errors": list(operational_errors),
    }


def exit_code_for_envelope(envelope: Mapping[str, Any]) -> int:
    if envelope.get("decision") == "clean":
        return 0
    return 2 if envelope.get("decision") in {"findings", "no_qualifying_runs"} else 1


def print_human_report(envelope: Mapping[str, Any]) -> None:
    print(f"Decision: {envelope['decision']} | complete={envelope['complete']} | requested={envelope['requested_count']} found={envelope['found_count']} clean={envelope['clean_count']}")
    for error in envelope["errors"]:
        print(f"  ERROR {error['type']} ({error['code']}): {error['message']}")
    for run in envelope["runs"]:
        marker = "CLEAN" if run["is_clean"] else "ISSUES"
        print(f"  {marker} {run['run_name']} ({run['run_id'][:8]}) [{run['run_stage']}] status={run['status']}")
        for group, values in run["findings"].items():
            for value in values:
                print(f"      {group}: {value}")
        for warning in run["warnings"]:
            print(f"      warning: {warning}")


def emit_envelope(envelope: Mapping[str, Any], *, as_json: bool) -> None:
    redacted = redact_data(dict(envelope))
    if as_json:
        print(json.dumps(redacted, indent=2, sort_keys=True))
    else:
        print_human_report(redacted)


def main(
    argv: Sequence[str] | None = None,
    *,
    mlflow_module: Any | None = None,
    model_info_loader: Callable[[str], Any] | None = None,
    input_example_loader: Callable[[str], object] | None = None,
) -> int:
    """Run an audit and return 0 clean, 2 findings, or 1 incomplete/operational."""

    raw_args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in raw_args
    if "--help" in raw_args or "-h" in raw_args:
        if not as_json:
            build_parser().print_help()
            return 0
        envelope = build_envelope(
            requested_count=0,
            audits=[],
            code_scan=CodeScanResult("not_requested"),
            operational_errors=[{"type": "ArgumentError", "code": "help_with_json", "message": "help is unavailable with --json"}],
        )
        emit_envelope(envelope, as_json=True)
        return 1
    try:
        args = build_parser().parse_args(raw_args)
        requested_count = _parse_last(args.last)
        profile = _parse_profile(args.profile)
    except ArgumentFailure as exc:
        envelope = build_envelope(requested_count=0, audits=[], code_scan=CodeScanResult("not_requested"), operational_errors=[{"type": "ArgumentError", "code": "invalid_argument", "message": redact_text(exc)}])
        emit_envelope(envelope, as_json=as_json)
        return 1
    expected = args.expected_registered_model_name.strip() if isinstance(args.expected_registered_model_name, str) else None
    stale_names = sorted({name.strip() for name in args.stale_model_name if isinstance(name, str) and name.strip()})
    code_scan = scan_code_path(args.code_path, [name for name in (expected, *stale_names) if name])
    if mlflow_module is None:
        try:
            import mlflow as mlflow_module
        except Exception as exc:
            envelope = build_envelope(requested_count=requested_count, audits=[], code_scan=code_scan, operational_errors=[safe_error(exc)])
            emit_envelope(envelope, as_json=args.json)
            return 1
    try:
        mlflow_module.set_tracking_uri(f"databricks://{profile}")
        experiment = find_experiment(mlflow_module, args.experiment)
        client = mlflow_module.MlflowClient()
        runs_raw = mlflow_module.search_runs(experiment_ids=[experiment.experiment_id], max_results=requested_count, order_by=["start_time DESC"], output_format="list")
        if isinstance(runs_raw, (str, bytes)) or not isinstance(runs_raw, Iterable):
            raise TypeError("search_runs returned a non-iterable run collection")
        iterator = iter(runs_raw)
        runs = []
        for index in range(requested_count + 1):
            try:
                run = next(iterator)
            except StopIteration:
                break
            if index == requested_count:
                raise ValueError("search_runs exceeded the requested run limit")
            runs.append(run)
        runs.sort(key=_run_sort_key)
    except Exception as exc:
        envelope = build_envelope(requested_count=requested_count, audits=[], code_scan=code_scan, operational_errors=[safe_error(exc)])
        emit_envelope(envelope, as_json=args.json)
        return 1
    audits: list[RunAudit] = []
    for run in runs:
        try:
            audits.append(
                audit_run(
                    run,
                    client=client,
                    experiment=experiment,
                    run_stage=args.run_stage,
                    expected_registered_model_name=expected,
                    stale_model_names=stale_names,
                    code_scan=code_scan,
                    model_info_loader=model_info_loader,
                    input_example_loader=input_example_loader,
                )
            )
        except Exception as exc:
            audits.append(_incomplete_audit_from_exception(run, args.run_stage, exc))
    envelope = build_envelope(requested_count=requested_count, audits=audits, code_scan=code_scan)
    emit_envelope(envelope, as_json=args.json)
    return exit_code_for_envelope(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
