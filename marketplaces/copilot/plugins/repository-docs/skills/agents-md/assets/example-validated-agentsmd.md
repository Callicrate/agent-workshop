# Ledger CLI

## Scope

This file applies to the entire repository.

## Context

- Purpose: command-line tool for validating ledger export files before upload.
- Primary stack: Python 3.12 from `pyproject.toml`.
- Entry point: `src/ledger_cli/cli.py`, exposed as `ledger-check` in `pyproject.toml`.

## Local Commands

```bash
python -m pytest tests
```

## Project Rules

- Add new CLI behavior through `src/ledger_cli/cli.py` and keep parser coverage in `tests/`.
- Keep ledger sample files under `tests/fixtures/`; tests should not read ad hoc files from the repository root.
- Use `python -m pytest tests` as the focused validation command after parser or CLI changes.

## Testing

- Tests live under `tests/` and use local fixtures from `tests/fixtures/`.
