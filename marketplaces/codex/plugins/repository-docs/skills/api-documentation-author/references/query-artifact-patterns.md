# Query Artifact Patterns

Use this reference when the deliverable is a standalone SQL file, SQLTools query, Databricks SQL statement, or query-backed interface contract.

## Required Contract

Document or encode:

- purpose and expected reader or runner
- target engine and client, such as SQLTools, Databricks SQL, Spark SQL, or PostgreSQL
- required catalog, schema, warehouse, connection, or profile
- canonical object list or source of table names
- parameters and defaults
- output columns and row shape
- expected non-empty signal when known
- zero-row diagnostics
- safety boundaries for state-changing statements

Use canonical object lists from project docs or source files when they exist. Do not discover objects by prefix unless the task explicitly asks for prefix discovery.

## Parameters And Time Windows

- Put parameters in one obvious block, such as a `params` CTE, variables section, or documented bind parameters.
- For time-bound queries, state and check `window_start < window_end`.
- Use inclusive/exclusive bounds deliberately, such as `timestamp >= window_start AND timestamp < window_end`, and document that choice.
- Include timezone assumptions when timestamps are not obviously UTC.
- Keep example values realistic enough to return rows when the user expects a smoke test.

## SQLTools-Friendly Files

- Make the file runnable as a standalone query when the user says they will run it in SQLTools.
- Avoid notebook-only syntax, shell commands, or separate setup cells.
- Return a single table when requested, using `UNION ALL` or explicit object lists instead of multiple result sets.
- Include column aliases that explain the result without extra prose.

## Validation

Prefer the lightest check that proves the query contract:

- Parse or format the SQL when a parser is available.
- Run a harmless `SELECT` or limited query when credentials and environment are available.
- For count or matrix queries, sanity-check that filters can return rows and that dimensions match the requested labels.
- When execution is unavailable, manually verify parameter order, table names, join keys, and time-window bounds.

## Zero-Row Diagnostics

When a query could return zero rows unexpectedly, include checks such as:

- verify `window_start < window_end`
- count rows before the most selective filter
- count rows per joined table
- inspect min and max timestamps
- list distinct expected labels or statuses
- verify the canonical object list matches the environment