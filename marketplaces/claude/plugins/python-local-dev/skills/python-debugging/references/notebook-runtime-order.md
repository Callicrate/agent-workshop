# Notebook Runtime Order

Use this reference for generated notebooks, transformed notebooks, Databricks notebooks, or any notebook whose failure may depend on cell order or stale kernel state.

Notebook artifacts use `.ipynb`. Treat `.py` as a Python script or module, not as a notebook output format.

## Generated Notebook Checklist

Before editing notebook logic, inspect the output notebook directly:

- install cells: `%pip`, `!pip`, `pip install`, or `python -m pip`
- restart cells: `dbutils.library.restartPython()` or equivalent kernel restart notes
- import cells and their first dependent uses
- class, function, and variable definitions before first calls
- cells generated out of original order
- symbols that appear only because a warm kernel already had them
- SQL, magic, or shell cells whose language metadata affects execution

When the notebook is generated, fix the generator or upstream transformation when possible, regenerate the notebook, and then check the original symptom again. Do not hand-patch generated output unless the user asked for a one-off repair.

## `%pip` And Restart Semantics

- Put `%pip` or install cells before imports and executable code.
- Restart the kernel after package installation when the notebook runtime requires it.
- After a restart, rerun setup cells in order before running dependent cells.
- Treat any value not recreated by the notebook after restart as missing.
- Do not rely on a variable from the interrupted or pre-restart kernel.

If the user explicitly says restarting after install is acceptable, prefer a clean restart plus ordered run over trying to preserve stale state.

## Symbol Inventory

For notebook `NameError`, generated output, or user-reported missing imports, build a small inventory:

- symbol name
- first-use cell
- import cell, if any
- definition cell, if any
- whether the definition appears after first use
- whether the symbol is likely injected by the platform, such as `spark`, `dbutils`, or `display`

Useful deterministic checks:

```powershell
python skills\python-debugging\scripts\check_notebook_runtime_order.py path\to\notebook.ipynb --json
```

The checker reads only bounded UTF-8 JSON. It validates file size before reading, then cell count, source size and line count, duplicate cell ids, and non-finite JSON values. Before parsing Python, it conservatively bounds bracket nesting, token/node estimate, and chained unary operators; after parsing, it enforces AST node/depth budgets. Use lower limits for an investigation, or explicit overrides no larger than the tool's documented hard caps. With multiple inputs, inspect the per-file result and aggregate: one malformed or oversized notebook must not suppress results for the others.

The checker evaluates only module-level execution order. Function, async-function, class, lambda, and parameter locals do not create global notebook state. It distinguishes conditional definitions, definite `del`, possible conditional deletion, star-import uncertainty, and structural restart calls from strings that merely contain restart words. A conditional `del` produces a state warning, not a false claim that the name is definitely absent. SQL and other whole-cell magics or language metadata are routed as non-Python, never parsed as Python.

Manual `nbformat` inspection is also acceptable when you need custom context:

```python
import nbformat

notebook = nbformat.read(open("notebook.ipynb", encoding="utf-8"), as_version=4)
for index, cell in enumerate(notebook.cells, start=1):
    if cell.cell_type == "code":
        print(index, cell.source.splitlines()[:5])
```

## Clean-Run Completion

A notebook fix is complete only when one of these is true:

- the notebook runs top-to-bottom from a clean or explicitly restarted kernel
- the generated notebook is regenerated and the same smoke path passes
- the remaining blocker is classified as environment, platform, dependency, policy, or unavailable compute

Do not call a notebook fixed because one cell works in the current interactive state.

## Snippet-Only Repair Requests

When the user asks for a cell to repair an existing variable in another notebook:

- return a self-contained Python cell
- assume the named variable already exists unless the user asks for setup code
- mutate or replace only the requested variable
- avoid repo edits, project-wide refactors, or broad tests
- include only the minimal imports needed by the snippet

The deliverable is the runnable cell, not a patch to the repository.
