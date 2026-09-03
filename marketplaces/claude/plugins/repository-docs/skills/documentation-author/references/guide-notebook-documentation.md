# Notebook Documentation Workflow

Use this guide when adding explanations to notebooks, documenting generated notebooks, summarizing table-producing notebooks, or repairing notebook Markdown after code changes.

Notebook artifacts use `.ipynb`. If the artifact is `.py`, document it as a Python script or module unless the project explicitly says otherwise.

## Source Trust Model

- Treat executable cells as authoritative over generated README text, comments, review packets, and AI commentary.
- Inspect notebook structure and all relevant cells in execution order before writing explanations.
- Identify imports, widgets, configs, SQL cells, table-producing cells, and output cells that affect the documented behavior.
- Verify date/window filters from code, widgets, SQL, configs, and generated parameters.
- Preserve existing explanatory Markdown unless it is proven wrong or the user asked to remove it.
- Remove or rewrite meta labels such as `review packet`, `AI commentary`, or generated summary when they are not part of the expected notebook audience.

## Table-Producing Notebook Notes

For notebooks that create or update tables, add a compact explanation near the relevant setup or write cells:

- source table or files
- target table
- time window and timestamp column
- filters and excluded entity classes
- SCD2 anchor timestamp or point-in-time predicate, if any
- row-selection caveats
- whether upstream sources are read-only

## Runnable-Order Preservation

- Do not move imports below first use while editing Markdown.
- Do not reorder setup, install, restart, import, definition, or execution cells unless that is the task.
- After edits, validate the notebook JSON parses or use the notebook summary tool when available.
- For generated notebooks, fix the generator or transform when possible, regenerate, and inspect the generated output.

## Bad And Good

Wrong: summarize from `README.md` or a generated review block without checking code.

Correct: trace imports, widgets, SQL, table writes, execution cells, and outputs; then write the shortest explanation that matches the executable notebook.
