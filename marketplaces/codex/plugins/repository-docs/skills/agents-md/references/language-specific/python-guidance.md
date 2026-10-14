# Python AGENTS.md Guidance

Use this only to decide what Python facts belong in a repository AGENTS.md.
Do not copy general Python standards unless the repository config, source, tests, or docs prove a local rule or exception.

## Context Facts To Verify

Look for these sources before drafting Python guidance:

- `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements.txt`, `uv.lock`, `poetry.lock`, or `Pipfile.lock`
- `pytest.ini`, `tox.ini`, `noxfile.py`, `conftest.py`, and CI workflow files
- package entry points, console scripts, task runners, and import package names
- representative modules that show error handling, logging, config loading, and test style

## High-Value AGENTS.md Entries

Prefer entries like these when they are verified:

```markdown
## Context
- **Python**: 3.12 from `.python-version`
- **Package manager**: `uv`; sync with `uv sync --dev`
- **Package root**: `src/acme/`; tests import the installed package, not local path hacks
- **Test command**: `uv run pytest tests/unit`
```

```markdown
## Project Rules
- Register new CLI commands in `src/acme/cli.py`; console scripts are declared in `pyproject.toml`.
- Keep generated schemas under `src/acme/generated/`; update them with `uv run python scripts/build_schemas.py`.
- Use the repository logger from `src/acme/logging.py` so JSON log fields stay consistent.
```

## What To Omit

Omit rules that merely repeat ordinary Python guidance unless the repo has a local twist:

- import grouping, type hints, f-strings, pathlib, or docstring style with no local exception
- generic testing advice such as "write unit tests"
- broad quality claims such as "write clean code" or "handle errors properly"

## Useful Do / Don't Pair

### Do

```python
from acme.config import load_settings
settings = load_settings(profile="local")
```

### Don't

```python
settings = json.loads(Path("settings.json").read_text())  # Bypasses env/profile precedence.
```
