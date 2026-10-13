---
description: "Python development standards: diagnostics, imports, typing, testing, formatting"
applyTo: '**/*.py,**/*.ipynb'
---

# Python Development Standards

## Problems Pane

- Resolve all VS Code diagnostics in every file you touch before finishing.

## Imports and Formatting

- Keep imports at the top of modules and in the first code cell of notebooks.
- Use f-strings for string interpolation.

## Logging

- Use the configured logger for operational output.
- Do not use `print()` for normal runtime behavior unless the user explicitly asks for it in a one-off script.

```python
from logging import Formatter, INFO, StreamHandler, getLogger

logger = getLogger(__name__)
logger.setLevel(INFO)

if not logger.handlers:
    handler = StreamHandler()
    handler.setFormatter(Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
```

## Type Hints

- Add type hints to function signatures.
- Use explicit types for complex values and callbacks when they improve readability.

## Exceptions and Failure Signaling

- Catch specific exceptions before generic `Exception`.
- Log and re-raise when the failure should stop the operation.
- Do not silently return `None` or `False` in place of a real failure.
- Do not use `exit(1)` to signal failure.

```python
try:
    response = client.fetch()
except TimeoutError as exc:
    logger.error(f'Request timed out: {exc}')
    raise
```

## Control Flow

- Avoid unbounded loops without a documented exit condition.
- Keep return types consistent.
- Prefer explicit failures over ambiguous sentinel values.

## External Calls

- Always set timeouts for external requests.
- Add retry logic for transient failures when the call is expected to be retriable.
- Validate response structure before processing nested fields.

```python
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(10))
def fetch_json(url: str) -> dict:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()
```

## Code Clarity

- Comment non-obvious calculations or business rules.
- Use `datetime` objects in core logic and convert to epochs only at API boundaries.
- Keep dependencies declared in project config such as `pyproject.toml`.

## Virtual Environments

- Always use a virtual environment for local Python projects. Never install packages into the system Python.
- Prefer `python -m venv .venv` for standard projects. Use `uv venv` when `uv` is available.
- Activate before running: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Unix).
- When a project has `pyproject.toml` or `requirements.txt`, install dependencies into the venv before running code.

## Script Organization

- Place standalone automation scripts in an `agent_scripts/` or `scripts/` directory, not loose in the project root.
- Each script should be runnable independently: include a `if __name__ == "__main__":` guard.
- Scripts that other scripts depend on should expose functions, not rely on being imported by path hacks.

## Notebook Role

- Treat every `.ipynb` notebook as a standalone primary execution entry point for its task or job.
- Do not design notebooks to be called from other notebooks.

## Execution Completeness

- When asked to implement something, implement it. Do not describe what you would do, list options, or ask for confirmation unless genuinely blocked.
- If a task has multiple steps, complete all of them before reporting back.
- Partial implementations are failures. If you cannot finish, say what remains and why.
- When fixing a bug, verify the fix works (run the code, check the output) before declaring it fixed.
