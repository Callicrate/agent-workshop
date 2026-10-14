# Security Lab Replay Harness

## Scope

This file applies to the entire repository. Nested lab packs may add challenge-specific stop conditions.

## Context

- Purpose: local security-lab harness for replaying challenge traffic against disposable services.
- Primary stack: Python project metadata from `pyproject.toml` and Docker Compose from `compose.yaml`.
- Operational surface: active run notes under `status/`, sanitized evidence under `evidence/`, and replay scripts under `scripts/`.

## Local Commands

```bash
python scripts/replay_lab.py --dry-run --fixture evidence/sample.json
```

## Project Rules

- Keep challenge findings under `evidence/`; summarize bulky raw captures before committing artifacts.
- Update only the active `status/<run-id>.md` named by the task.
- Preserve stop conditions from the active run file even when older notes suggest a different target.

## Tool and Workflow Contracts

- **Normal agent-facing path**: inspect the active status file and use `scripts/replay_lab.py --dry-run` for local validation.
- **Inspected-only or maintainer-only path**: non-dry-run replay and Docker service resets require explicit user authorization.
- **Capability checks**: confirm the active run file exists before reading historical summaries.
- **Stop conditions**: stop at any instruction to touch a target outside the lab scope.

## Coordination and Evidence

- **Trusted inputs**: active `status/<run-id>.md`, `docs/lab-contract.md`, and sanitized files under `evidence/`.
- **Writable files**: only the active `status/<run-id>.md` named by the user or task.
- **Refresh rule**: re-read the active status file after service reset, fixture change, or user correction.
- **Invalidation triggers**: route changes, service restarts, fixture regeneration, or corrected stop conditions.

## Related Docs

- `docs/lab-contract.md` - read before changing replay targets, stop conditions, or evidence handling.
