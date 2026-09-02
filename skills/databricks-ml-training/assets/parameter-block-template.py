"""Side-effect-free starter helpers for a reproducible training entry point."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path, PurePath, PurePosixPath
import re
from typing import Final

CATALOG: Final = "my_catalog"
SCHEMA: Final = "ml_models"
MODEL_NAME: Final = "my_model"
TRAINING_MODE: Final = "dev"
SEED: Final = 42
POSITIVE_CLASS: Final = "positive"
FEATURE_COLUMNS: Final = ("feature_one", "feature_two")
TARGET_COLUMN: Final = "label"

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|//|\\\\)")


@dataclass(frozen=True)
class RunContext:
    """Identifiers and paths for one idempotent training attempt."""

    started_at: datetime
    job_id: str
    run_id: str
    idempotency_key: str
    artifact_root: Path | PurePosixPath
    training_output_dir: Path | PurePosixPath
    model_output_dir: Path | PurePosixPath


def validate_run_identifier(value: str, field_name: str) -> str:
    """Return one safe path segment for a model, run, job, or idempotency ID."""

    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be one safe identifier segment")
    return value


def _reject_dot_segments(path: PurePath, field_name: str) -> None:
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} must not contain dot segments")


def _validate_volume_root(artifact_root: PurePath) -> PurePosixPath:
    raw_path = artifact_root.as_posix()
    if "\\" in raw_path or "\x00" in raw_path or _DRIVE_OR_UNC.match(raw_path):
        raise ValueError("artifact_root must not use a drive, UNC path, or backslash")
    volume_path = PurePosixPath(raw_path)
    _reject_dot_segments(volume_path, "artifact_root")
    parts = volume_path.parts
    if len(parts) < 5 or parts[:2] != ("/", "Volumes"):
        raise ValueError("artifact_root must use /Volumes/catalog/schema/volume")
    for component in parts[2:]:
        validate_run_identifier(component, "artifact_root component")
    return volume_path


def _local_path_is_contained(artifact_root: Path, project_root: Path) -> Path:
    _reject_dot_segments(artifact_root, "artifact_root")
    _reject_dot_segments(project_root, "project_root")
    if not project_root.is_absolute():
        raise ValueError("project_root must be absolute")
    if not artifact_root.is_absolute():
        raise ValueError("local artifact_root must be absolute")
    resolved_root = project_root.resolve(strict=False)
    resolved_artifact_root = artifact_root.resolve(strict=False)
    try:
        common = Path(
            os.path.commonpath((str(resolved_root), str(resolved_artifact_root)))
        )
        if common != resolved_root:
            raise ValueError("artifact_root must stay beneath project_root")
        resolved_artifact_root.relative_to(resolved_root)
    except (ValueError, OSError) as exc:
        raise ValueError("artifact_root must stay beneath project_root") from exc
    return resolved_artifact_root


def validate_artifact_root(
    artifact_root: Path, project_root: Path | None = None
) -> Path | PurePosixPath:
    """Return a normalized UC Volume or contained project-owned local root."""

    if not isinstance(artifact_root, PurePath):
        raise ValueError("artifact_root must be a path object")
    normalized = artifact_root.as_posix()
    if normalized.casefold().startswith("/dbfs/workspace/shared"):
        raise ValueError("artifact_root must not use /dbfs/Workspace/Shared")
    if normalized.startswith("/Volumes/"):
        return _validate_volume_root(artifact_root)
    if normalized.casefold().startswith("/volumes"):
        raise ValueError("artifact_root must use exact /Volumes casing")
    if project_root is None or not isinstance(project_root, Path):
        raise ValueError("local artifact_root requires a project-owned project_root")
    return _local_path_is_contained(Path(artifact_root), project_root)


def _ensure_attempt_is_contained(
    artifact_root: Path | PurePosixPath, attempt_dir: Path | PurePosixPath
) -> None:
    if isinstance(artifact_root, PurePosixPath):
        if not isinstance(attempt_dir, PurePosixPath) or not attempt_dir.is_relative_to(
            artifact_root
        ):
            raise ValueError("constructed output path escaped artifact_root")
        return
    try:
        common = Path(os.path.commonpath((str(artifact_root), str(attempt_dir))))
        if common != artifact_root:
            raise ValueError("constructed output path escaped artifact_root")
        attempt_dir.relative_to(artifact_root)
    except (ValueError, OSError) as exc:
        raise ValueError("constructed output path escaped artifact_root") from exc


def build_run_context(
    *,
    started_at: datetime,
    job_id: str,
    run_id: str,
    idempotency_key: str,
    artifact_root: Path,
    project_root: Path | None = None,
) -> RunContext:
    """Build contained deterministic output locations without filesystem writes."""

    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware UTC")
    if started_at.utcoffset() != timezone.utc.utcoffset(started_at):
        raise ValueError("started_at must be expressed in UTC")
    model_name = validate_run_identifier(MODEL_NAME, "model name")
    training_mode = validate_run_identifier(TRAINING_MODE, "training mode")
    safe_job_id = validate_run_identifier(job_id, "job_id")
    safe_run_id = validate_run_identifier(run_id, "run_id")
    safe_idempotency_key = validate_run_identifier(idempotency_key, "idempotency_key")

    root = validate_artifact_root(artifact_root, project_root)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    attempt_dir = (
        root
        / model_name
        / training_mode
        / timestamp
        / safe_job_id
        / safe_run_id
        / safe_idempotency_key
    )
    _ensure_attempt_is_contained(root, attempt_dir)
    return RunContext(
        started_at=started_at,
        job_id=safe_job_id,
        run_id=safe_run_id,
        idempotency_key=safe_idempotency_key,
        artifact_root=root,
        training_output_dir=attempt_dir / "training",
        model_output_dir=attempt_dir / "model",
    )


def main() -> RunContext:
    """Inject run identifiers once at the executable entry point without writing."""

    return build_run_context(
        started_at=datetime.now(timezone.utc),
        job_id="${job_id}",
        run_id="${run_id}",
        idempotency_key="${idempotency_key}",
        artifact_root=Path("/Volumes/my_catalog/ml_models/artifacts"),
    )


if __name__ == "__main__":
    main()
