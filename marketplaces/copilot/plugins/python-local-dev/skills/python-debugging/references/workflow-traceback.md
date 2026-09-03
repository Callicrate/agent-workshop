# Traceback Workflow

Use this workflow when a traceback or notebook error output exists.

## Required Inputs

- Full traceback text, not just the final exception line
- Failing entrypoint: script command, test, task, or notebook cell
- Execution environment: local Python, notebook, Databricks cluster, or job

## Steps

1. Read the final line first.
   Identify the exception class and exact message.
2. Find the last frame in user code.
   Ignore library frames until you know where your code first supplied a bad value or bad call.
3. Inspect the failing state.
   Capture `type(...)`, `repr(...)`, lengths, keys, or `df.columns` for the values used on the failing line.
4. Classify the failure.
   Run [../scripts/analyze_traceback.py](../scripts/analyze_traceback.py) on a local UTF-8 file or bounded stdin, or match the error against [../assets/error-cheatsheet.json](../assets/error-cheatsheet.json). Do not pass a live clipboard. The helper returns only a basename or allowed repo-relative location, no source line, and redacted bounded messages. It preserves cookie names while redacting cookie, session, authorization, and token values.
5. Choose the smallest root-cause fix.
   Prefer fixing the upstream contract over adding a fallback at the crash site.
6. Rerun the same failing path.
   If the traceback changes, restart classification from the new final exception.

## Notebook And Databricks Checks

- If the traceback points to `<command-...>` or notebook cells, verify execution order before changing logic.
- For notebook execution-order failures, check that setup and environment loading happen before dependent code, imports appear before first use, definitions stay before calls, and SQL or magic cells have the correct language metadata.
- If the user names a notebook as evidence, inspect every cell that defines imports, globals, widgets, helpers, or execution order before patching the failing cell.
- If imports or globals changed recently, restart the kernel or detach and reattach the cluster before trusting stale state.
- If the user interrupted execution, restarted the kernel, installed packages, or edited earlier cells, rerun setup cells in order and treat missing recreated state as the bug.
- For generated notebooks, use the runtime-order checker or a symbol inventory before fixing a downstream `NameError`; imports and definitions must appear before first use in the generated output.
- If the failure is mainly Spark schema, Delta write, or DataFrame expression behavior, switch to `databricks-spark-etl` after capturing the traceback.

## Windows PowerShell Command Checks

- Do not run Bash heredocs such as `python << 'EOF'` from PowerShell. Use a PowerShell here-string piped to Python, or run a small script through the project interpreter.

```powershell
$code = @'
import json
print("ok")
'@
$code | python
```

- Prefer ASCII status text in quick Python verification scripts on Windows, or configure UTF-8 explicitly before printing non-ASCII symbols.

## Do Not

- Do not hide the failure with broad `except Exception` blocks.
- Do not replace a required value with `None`, `{}`, or `[]` unless that is part of the real contract.
- Do not rewrite unrelated code until the failing value or call site is identified.
- Do not call a notebook fixed until it survives a clean ordered run or the remaining blocker is classified as environment, platform, dependency, or policy.
- Do not paste diagnostic output into durable logs or tickets until you independently confirm its source and the helper's redaction metadata. A safe summary is not authority to disclose the original traceback.
