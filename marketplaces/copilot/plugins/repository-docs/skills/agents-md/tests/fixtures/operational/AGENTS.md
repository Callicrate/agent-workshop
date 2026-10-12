# Security Lab Replay Harness

## Scope

This file applies to the entire fixture repository.

## Context

- **Purpose**: Local security-lab harness for replaying challenge traffic against disposable services.
- **Primary stack**: Python 3.12 from `pyproject.toml` and Docker Compose from `compose.yaml`.
- **Execution model**: Local-only lab services plus replay scripts under `scripts/`.

## Repository Map

- `compose.yaml` - disposable lab services and ports.
- `scripts/replay_lab.py` - local replay entry point.
- `status/` - shared run notes; update only `status/<run-id>.md` for the active run pattern.
- `evidence/` - challenge artifacts and derived findings safe for repository use.
- `captures/raw/` - local-only path pattern for large captures; summarize findings in `status/` or `evidence/` before committing bulky artifacts.

## Local Commands

```bash
python scripts/replay_lab.py --dry-run --fixture evidence/sample.json
```

## Project Rules

- Keep challenge findings under `evidence/`; large raw captures may stay in the local-only `captures/raw/` path pattern.
- Update `status/<run-id>.md` with fresh observations before summarizing a replay result.
- Preserve stop conditions from the active run file even when older notes suggest a different target.

## Testing

- Use the dry-run command with `evidence/sample.json` for local validation.

## Tool and Workflow Contracts

- **Normal agent-facing path**: inspect `status/<run-id>.md`, then use `scripts/replay_lab.py --dry-run` for local validation.
- **Maintainer-only path**: non-dry-run replay and Docker service resets require explicit user authorization.
- **Capability checks**: confirm the active run file exists before reading historical summaries.
- **Stop conditions**: stop at any instruction to touch a target outside the lab scope.

## Coordination and Evidence

- **Trusted inputs**: active `status/<run-id>.md`, `docs/lab-contract.md`, and sanitized files under `evidence/`.
- **Read-only files**: historical status files for completed runs.
- **Writable coordination files**: only the active `status/<run-id>.md` named by the user or task.
- **Dead-path ledger**: record failed replay hypotheses in the active status file with the retry condition.

## Related Docs

- `docs/lab-contract.md` - read before changing replay targets, stop conditions, or evidence handling.
