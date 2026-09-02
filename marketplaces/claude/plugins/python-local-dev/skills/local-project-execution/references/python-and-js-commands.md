# Python And JavaScript Commands

## Python Selection

- If `.venv`, `venv`, or a documented interpreter exists, use it before global Python.
- Before using global `python` or `pip`, inspect `pyproject.toml`, `.python-version`, `uv.lock`, `poetry.lock`, `requirements*.txt`, existing virtualenvs, and `py -0p` on Windows when available.
- If default Python is very new, known broken for the dependency set, or missing project packages, choose the repo venv, `uv run`, Poetry environment, or documented interpreter instead.
- Never install into global Python when a repo venv, `uv`, Poetry, or documented environment exists unless the user explicitly asks for global installation.
- If `pyproject.toml` declares a tool section for pytest, ruff, mypy, or coverage, use that tool through the project environment.
- Use `uv run`, `poetry run`, or the repo virtual environment according to lockfiles and docs.
- Prefer targeted pytest selectors first, then expand to the smallest suite that covers the change.

## Codebase Scans

When writing a helper that walks a repository (counting files, searching content, building an inventory), exclude version-control and dependency directories or the scan is dominated by `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, and `build`.

`pathlib.Path.rglob` descends into every one of them. Prune with an explicit skip set during an `os.walk`, or filter rglob results against it.

```python
import os

# WRONG - traverses .git, node_modules, .venv, __pycache__
for path in root.rglob("*.py"):
    ...

# CORRECT - prune ignored directories during the walk
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    ...
```

## CLI Help Or Entrypoint Hangs

Local execution owns the command shape, timeout, selected interpreter, cwd, and success markers even when Python debugging owns the import root cause.

When a Python CLI, `--help`, or entrypoint hangs:

1. Confirm the selected interpreter and cwd.
2. Run direct import probes as separate timed commands before broad tests.
3. Use flushed before/after markers around each suspect import.
4. Add `-X faulthandler` or `faulthandler.dump_traceback_later(...)` when stack visibility matters.
5. Hand off to Python debugging only after the failing import boundary is identified.

PowerShell probe shape:

```powershell
Set-Location -LiteralPath 'C:\path\to\repo'
python -X faulthandler -c "import faulthandler; faulthandler.dump_traceback_later(10, repeat=False); print('before package', flush=True); import package; print('after package', flush=True)"
```

For multiple imports, split probes or use a small helper script with `print(..., flush=True)` before and after each import. A stopped output after `before pydantic_ai` means the hang boundary is inside that import or its import-time side effects.

## JavaScript Selection

- Respect lockfile ownership:
  - `pnpm-lock.yaml` -> `pnpm`
  - `yarn.lock` -> `yarn`
  - `package-lock.json` or `npm-shrinkwrap.json` -> `npm`
- Without a lockfile, respect the supported `packageManager` field before falling back to npm.
- Use `package.json` scripts for test, lint, typecheck, build, and dev server commands.
- In monorepos, run from the package root or use the workspace filter syntax already present in scripts or docs.

## Node Package Installability

`npm run build` usually transpiles, bundles, or generates output. It does not by itself prove the package is installable.

To answer whether a Node package is installable:

1. Inspect `package.json` fields: `name`, `version`, `type`, `bin`, `main`, `exports`, `files`, and `scripts`.
2. Identify the package manager from lockfiles and workspace config.
3. Confirm declared `bin`, `main`, and `exports` targets exist after build.
4. Inspect build output directories such as `dist`, `build`, `lib`, or documented output paths.
5. Run `npm pack --dry-run` or the repo's documented packaging command from the package root.
6. Report the packed files and any missing declared entrypoints.

For pnpm or yarn workspaces, use the repo's documented pack command when present; otherwise run the package manager from the package root with workspace filters only if the repo already uses them.

## Validation Ladder

1. Static check or focused test for the changed file or package.
2. Package-level test/build when the change crosses module boundaries.
3. Workspace-level test/build only when shared contracts, dependency versions, or generated outputs changed.

Report the exact command that was run and what remains unverified.
