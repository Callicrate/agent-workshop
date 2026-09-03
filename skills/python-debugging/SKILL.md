---
name: python-debugging
description: "Use when debugging Python failures: tracebacks, type/import semantics, notebook order, pytest collection, or hangs. Do not trigger for Databricks runtime/compute failures; use databricks-runtime-doctor."
metadata:
  short-description: Debug Python failures.
---

# Python Debugging


## When to Use

- Debugging a Python traceback, runtime failure, or warning
- Fixing type mismatches, `None` handling, missing keys, import failures, and async issues
- Debugging notebook state problems or Databricks Python execution-order failures
- Adding temporary instrumentation when the failure is not yet localized
- Classifying symptom-only failures such as terminal exits, hangs, notebook output problems, import probes, or platform warnings
- Validating generated or transformed notebooks for imports, definitions, `%pip`, restart semantics, and clean ordered execution
- Fixing pytest collection-time import failures before broad test execution

## When NOT to Use

- Non-Python debugging tasks
- Databricks REST API shell-quoting problems better handled by `databricks-api-calls`
- Broad architecture or performance reviews that are not primarily failure-driven
- Shell, WSL, SSH, or terminal transport failures before the selected Python interpreter is known to own the failure

## Workflow

1. Capture the exact failure: full traceback or warning text when available, failing command or cell, execution environment, terminal output, notebook state, and user-observed symptom.
2. If no traceback exists, classify the observable failure from terminal output, notebook cell state, hang behavior, user-reported symptom, platform prompt, or platform warning. Do not wait for a traceback before inspecting the failing artifact.
3. If a traceback exists, follow [references/workflow-traceback.md](references/workflow-traceback.md).
4. Run [scripts/analyze_traceback.py](scripts/analyze_traceback.py) on a bounded local UTF-8 file or stdin, or inspect [assets/error-cheatsheet.json](assets/error-cheatsheet.json) to classify the error family. Do not use live clipboard input. Its output deliberately redacts credentials, control characters, machine paths, and source excerpts.
5. Apply the smallest root-cause branch from [references/error-patterns.md](references/error-patterns.md). For import hangs, load [references/import-hang-triage.md](references/import-hang-triage.md) before changing code.
6. For generated or transformed notebooks, load [references/notebook-runtime-order.md](references/notebook-runtime-order.md), run [scripts/check_notebook_runtime_order.py](scripts/check_notebook_runtime_order.py) when possible, and fix the generator or upstream transform when the output ordering is wrong. Use only explicit bounded limit overrides; the checker reports each input independently and never emits a traceback.
7. For request-shape, generated-helper, or shell-sensitive reproductions, load [references/repro-and-compile-preflights.md](references/repro-and-compile-preflights.md).
8. If the user asks only for code to repair an existing notebook variable, provide a self-contained cell that mutates or replaces that variable. Avoid repo edits, broad project refactors, or unrelated test runs unless requested.
9. If the failure crosses PowerShell, WSL, SSH, Bash, a remote interpreter, or a terminal-exit notification, hand off to `local-project-execution` until the command boundary, prompt behavior, timeout, and selected Python interpreter are known. Return here only if Python owns the failure.
10. If a notebook hangs on Spark/table access or emits compute warnings, hand off to Databricks runtime/training guidance to classify cluster mode, worker count, task compute, and job logs before rewriting Python dataflow.
11. If the failure still is not localized, add only the instrumentation from [references/logging-patterns.md](references/logging-patterns.md) that is needed to expose the bad value or call site.
12. If the failure is mainly Spark DataFrame, schema, or Delta behavior, switch to `databricks-spark-etl` after you capture the Python-side error.
13. For notebook failures, verify top-to-bottom execution order before changing logic: environment setup, imports, globals/constants, definitions, calls, and SQL or magic-cell language metadata. After kernel restart, interruption, `%pip`, or cell edits, rerun setup cells in order and treat any value not recreated by the notebook as missing.
14. On Windows PowerShell, use PowerShell-compatible multiline Python. Do not use Bash heredocs such as `python << 'EOF'`.
15. Re-run the same failing path after each fix. Do not mask the error with broad exception handling or silent defaults.

## Completion Rules

- For notebook fixes, done means the same notebook or generated notebook runs from a clean ordered state, or the remaining blocker is explicitly classified as environment, platform, dependency, or policy.
- A notebook fix is not complete just because the current warm kernel can run one cell.
- A terminal lifecycle message is not a root cause; report the first interpreter, tool, prompt, or platform failure inside the captured output.
- For generated notebooks, validate by regenerating the output and checking the original symptom, not only by patching the generated artifact.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/analyze_traceback.py](scripts/analyze_traceback.py) | You have a local UTF-8 traceback and need a quick failure classification | Source-free, privacy-safe traceback summary with stable JSON errors |
| [scripts/check_notebook_runtime_order.py](scripts/check_notebook_runtime_order.py) | A notebook may have `%pip`, restart, import-after-use, definition-after-call, or generated-cell ordering issues | Bounded per-file JSON or text report of setup cells, warm/restart segments, and runtime-order findings |
| [assets/error-cheatsheet.json](assets/error-cheatsheet.json) | You are mapping a recurring error to known fixes | Stable error-to-pattern lookup |

## References

- [references/workflow-traceback.md](references/workflow-traceback.md) — ordered traceback triage workflow
- [references/error-patterns.md](references/error-patterns.md) — root-cause branches by error family
- [references/notebook-runtime-order.md](references/notebook-runtime-order.md) — generated notebook ordering, `%pip`, restart, symbol inventory, and clean-kernel checks
- [references/import-hang-triage.md](references/import-hang-triage.md) — direct import probes, faulthandler, bisection, timeout, crash, and prompt classification
- [references/repro-and-compile-preflights.md](references/repro-and-compile-preflights.md) — small repro scripts and helper compile checks
- [references/logging-patterns.md](references/logging-patterns.md) — minimal instrumentation patterns during debugging
