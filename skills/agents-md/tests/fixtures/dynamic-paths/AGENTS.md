# Dynamic Paths Fixture

## Context

- **Purpose**: Demonstrates dynamic path and glob handling.

## Repository Map

- `configs/train/*.yaml` - pattern for training configs.
- `status/<run-id>.md` - task-specific coordination file pattern.
- **Planned**: `scripts/export_metrics.py` is referenced by the roadmap but is not present; do not use until implemented.
- **External**: `/Shared/example/model` is a platform path, not a repository path.

## Project Rules

- Keep real training configs under `configs/train/`.
