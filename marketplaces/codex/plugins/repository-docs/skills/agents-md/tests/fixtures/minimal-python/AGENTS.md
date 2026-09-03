# Ledger CLI

## Scope

This file applies to the entire fixture repository.

## Context

- **Purpose**: Command-line tool for validating ledger export files before upload.
- **Runtime**: Python 3.12 from `pyproject.toml`.
- **Entry point**: `src/ledger_cli/cli.py`, exposed as `ledger-check` in `pyproject.toml`.

## Repository Map

- `src/ledger_cli/` - package source and CLI command handlers.
- `tests/` - pytest coverage for CLI behavior and parser edge cases.
- `pyproject.toml` - package metadata, console script, and pytest settings.

## Local Commands

```bash
python -m pytest tests
```

## Project Rules

- Add new CLI commands in `src/ledger_cli/cli.py` and expose them through the existing parser function.
- Keep ledger fixture files under `tests/fixtures/`; tests should not read files from the project root.
- Use `python -m pytest tests` as the focused validation command for parser or CLI behavior.

## Testing

- Tests live under `tests/` and exercise parser behavior with local fixtures.

## Do / Don't

### Do

```python
from ledger_cli.cli import build_parser

parser = build_parser()
args = parser.parse_args(["validate", "tests/fixtures/small-ledger.csv"])
```

### Don't

```python
args = parse_args_from_sys_argv()
```
