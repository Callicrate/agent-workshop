# Error Pattern Routing

Use this file after you identify the exception class. Each branch tells you what to inspect first and which fix style to prefer.

## Start Here

- `AttributeError` on `NoneType`: trace the upstream function or lookup that returned `None`. Prefer raising a domain error at that boundary over guarding every downstream access.
- `KeyError` or `IndexError`: inspect the actual container contents before changing access logic. Fix the producer when the container shape is wrong; use guarded access only when missing data is valid.
- `NameError` or `ImportError`: verify execution order, scope, interpreter, and installed package before changing code.
- `TypeError`: inspect the runtime types on the failing line. Fix the caller or conversion boundary rather than forcing casts deep in the flow.

## No-Traceback Intake

When there is no conventional traceback, classify the observable symptom first:

- terminal exited: transport evidence; scan captured output for the first tool, interpreter, prompt, or platform failure
- hang: isolate with flushed markers, timeout, or faulthandler before editing logic
- notebook ordering: inspect cell order, imports, definitions, `%pip`, restart semantics, and warm-kernel reliance
- import failure or import hang: direct-import the suspect package in the selected interpreter
- environment prompt: identify prompts such as `sudo`, credential requests, SSO, or package-manager questions
- platform constraint: identify missing WSL distro, unavailable compute, worker-count limits, policy errors, or remote shell failures
- user-reported output bug: inspect the named cell, variable, file, or generated artifact even without traceback text

Do not wait for a traceback when the failing artifact can be inspected directly.

## Attribute And Type Failures

### `AttributeError: 'NoneType' has no attribute ...`

- Inspect: the function call or lookup immediately before the attribute access.
- Usual cause: a required value was allowed to become `None` upstream.
- Fix preference: raise an explicit error where the value becomes missing, or make the return contract non-optional.

### `AttributeError: 'list' has no attribute ...`

- Inspect: whether the code expects a single object but received a collection.
- Usual cause: query or helper returned many items instead of one.
- Fix preference: normalize the contract to one item or iterate intentionally.

### `TypeError: 'X' object is not subscriptable`

- Inspect: `type(value)` and `repr(value)` before the `[]` access.
- Usual cause: `None`, a scalar, or a custom object is being treated like a sequence or mapping.
- Fix preference: correct the return type or branch on the true runtime shape.

### `TypeError: unsupported operand type(s)`

- Inspect: both operand types and where they were sourced.
- Usual cause: string-number mixing, `None`, or a function returning the wrong type.
- Fix preference: convert once at the input boundary or reject invalid input early.

### `ValueError` from conversion or unpacking

- Inspect: the exact raw input being converted or unpacked.
- Usual cause: malformed data or a caller returning the wrong tuple/list shape.
- Fix preference: validate input before conversion and make the producer return a stable shape.

## Lookup And Scope Failures

### `KeyError`

- Inspect: available keys and the contract for required versus optional fields.
- Usual cause: schema drift, typo, or missing optional handling.
- Fix preference: if the key is required, fail explicitly near ingestion; if optional, use an explicit documented default after inspecting the producer. Do not change required access to `.get()` just to silence the error.

### `IndexError`

- Inspect: container length and the logic that produced the index.
- Usual cause: empty results, off-by-one logic, or stale assumptions about list size.
- Fix preference: make the indexing precondition explicit before access.

### `NameError`

- Inspect: assignment order, scope, notebook cell execution order, and spelling.
- Usual cause: variable defined in a branch that did not run, earlier cell not executed, or typo.
- Fix preference: define the value on every required path or make the dependency explicit.
- For generated notebooks, produce a symbol inventory of imports, definitions, and first-use cells before editing the failing cell. Fix the generator or transformation when the output was shuffled.

### `ImportError` and `ModuleNotFoundError`

- Inspect: active interpreter, installed package set, import path, and circular imports.
- Usual cause: wrong environment, incorrect import target, or local module shadowing a package.
- Fix preference: correct the environment or import boundary first; only move imports inside functions when breaking a real cycle.
- For CLI entrypoints, inspect when configuration is loaded relative to imports and command registration. Circular imports often come from module-level config, logger, or runner initialization that should move behind the CLI boundary.
- If a CLI option supplies a config path, flow it through one shared config module or context object. Do not let each imported module load static defaults before the entrypoint parses arguments.
- For import hangs or process exits during import, use [import-hang-triage.md](import-hang-triage.md): flushed before/after markers, direct import probes, `-X faulthandler`, and import bisection.

### Pytest collection `ImportError`

- Inspect: the first `ERROR collecting` block and the first test module that failed to import.
- Confirm the package is installed in the selected interpreter, preferably editable for local source packages.
- Confirm package root and test root. Check whether `PYTHONPATH`, `src/` layout, or the working directory differs from CI.
- Direct-import the package module and the failing test module with the same interpreter before running broad tests.
- Check for local module shadowing and circular imports.
- Run the smallest affected collection or test selector after the import fix, then expand to broader tests only if needed.

Preferred sequence:

```powershell
python -c "import sys; print(sys.executable); import package_under_test; print(package_under_test.__file__)"
python -m pytest tests/path/to/test_file.py --collect-only -q
python -m pytest tests/path/to/test_file.py -q
```

### `UnicodeEncodeError` in a Windows console

- Inspect: console encoding, `PYTHONIOENCODING`, and any non-ASCII status symbols in quick verification output.
- Usual cause: printing glyphs such as checkmarks to a non-UTF-8 Windows console.
- Fix preference: use ASCII status text for short diagnostic scripts, or configure UTF-8 explicitly at the command boundary.

## Async Failures

### `RuntimeError: Event loop is closed`

- Inspect: platform, loop lifecycle, and whether tasks are still pending at shutdown.
- Usual cause: Windows event-loop policy mismatch or cleanup running after loop closure.
- Fix preference: set the Windows selector policy where required and ensure tasks finish before shutdown.

### `RuntimeWarning: coroutine was never awaited`

- Inspect: the call site that created the coroutine object.
- Usual cause: async function called from sync code without `await` or `asyncio.run()`.
- Fix preference: await it in async code or run it once from a sync boundary. Do not mix blocking libraries into an async path.

### Async retry, semaphore, or cleanup failures

- Inspect: which coroutine owns the retry loop, semaphore acquisition, cancellation, and cleanup.
- Usual cause: retry wrappers that hide cancellation, release a semaphore they did not acquire, or create tasks that outlive the owning context.
- Fix preference: keep retry ownership and resource cleanup in one async boundary; propagate cancellation and verify pending tasks at shutdown.

## Notebook And Databricks Failures

### Notebook `NameError` or stale state

- Inspect: execution order, redefined globals, and whether the kernel or cluster still holds old state.
- Fix preference: move imports and definitions before first use, rerun the setup path in order, or reset the session before changing logic. In shared notebooks, verify the first setup cell contains required imports rather than relying on prior interactive state.
- After kernel restart, interruption, `%pip`, or cell edits, rerun setup cells in order. Treat any value not recreated by the notebook as missing.
- For transformed notebooks, run the notebook runtime-order checker or an `nbformat` symbol scan before patching generated cells.

### Spark `AnalysisException` or unresolved column

- Inspect: `df.columns`, schema casing, and which DataFrame owns the column.
- Fix preference: correct the DataFrame expression or switch to `databricks-spark-etl` if the failure is primarily Spark-side.

### `'Column' object is not callable`

- Inspect: extra parentheses and Python methods accidentally applied to Spark columns.
- Fix preference: use Spark functions such as `F.lower(col("x"))`, not Python string methods on `Column` objects.

## Do Not

- Do not add broad `except Exception` blocks to make the traceback disappear.
- Do not replace a required value with `None`, `{}`, or `[]` unless the contract truly allows it.
- Do not keep debugging prints or temporary logging after the failing value is identified.
- Do not treat terminal lifecycle notices as root cause without reading the command output that preceded them.
- Do not rely on prior interactive notebook state when the deliverable must run from a clean kernel.
- Do not answer snippet-only notebook repair requests with project-wide edits.

### TypeError: expected string or bytes-like object

**Pattern:** Applying string method to Column object.

```python
# Problem
df = df.withColumn("lower_name", col("name").lower())  # .lower() is Python method

# Solution
from pyspark.sql.functions import lower
df = df.withColumn("lower_name", lower(col("name")))
```

---

## Quick Diagnostic Steps

1. **Read the full traceback** - The actual error is usually at the bottom
2. **Check types** - `print(type(variable))`
3. **Check for None** - inspect the producer and verify whether `None` is valid at that boundary
4. **Check contents** - `print(repr(variable))`
5. **Isolate the line** - Break complex expressions into parts
6. **Check the source** - Look at what the function actually returns

---

## Error Logging Pattern

```python
import logging
import traceback

logger = logging.getLogger(__name__)

try:
    result = risky_operation()
except Exception:
    logger.error("Operation failed", exc_info=True)  # Includes traceback
    raise  # Re-raise after logging
```
