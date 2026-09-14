# Core ETL Patterns

Use this reference first for Delta write paths and table-contract changes.

## Contract Order

- Treat the target table DDL or existing table schema as the source of truth.
- If the task includes creating or changing a table contract, make that change in a standalone `.sql` DDL file or deployment step before touching notebook or Python logic.
- Validate the source schema before choosing a write mode.
- Keep catalog, schema, and table names fully qualified.
- Fail before the write if a required target column is missing or incompatible.

## Write Mode Selection

| Situation | Preferred write | Why |
|-----------|-----------------|-----|
| Existing table and matching contract | `writeTo(...).append()` or `INSERT INTO ... (explicit_columns)` / `BY NAME` | Keeps the table definition authoritative while making resolution explicit |
| Target table missing or contract creation required | standalone `.sql` DDL applied before the write | Keeps DDL out of business-logic notebooks and modules |
| Additive nullable columns | append with `mergeSchema` | Evolves the table without replacing it |
| Explicit full-contract replacement | overwrite with `overwriteSchema` | Replaces the contract intentionally |
| Upsert or current-row maintenance | `MERGE` | Makes row-matching rules explicit |

- Do not embed `CREATE TABLE`, `CREATE OR REPLACE TABLE`, or `ALTER TABLE` in notebooks or Python modules that also perform transforms or writes.
- Do not rely on implicit table creation via `saveAsTable`; logic code should assume the target exists or fail loudly.
- Do not default to `saveAsTable` when the table already exists and DDL is authoritative.
- Use `overwriteSchema` only when the replacement contract is explicit and approved.
- Do not use a bare `INSERT INTO ... SELECT` or `DataFrameWriter.insertInto(...)` unless the pre-write schema gate used `by-position` for the exact projection. `insertInto` resolves by position; prefer `writeTo(...).append()` or `INSERT` with an explicit column list or `BY NAME` when the runtime supports it.

## SCD2 Contract

- Keep the business key, `valid_from`, `valid_to`, and `is_current` explicit.
- Use `NULL` `valid_to` for the current version.
- Close the old version before inserting the new current version.
- Keep current-record and point-in-time filters in reusable logic instead of ad hoc query branches.
- For point-in-time work, state the timestamp being evaluated and use `valid_from <= point_in_time AND (valid_to > point_in_time OR valid_to IS NULL)`.

Start from [../assets/scd2-table-ddl.sql](../assets/scd2-table-ddl.sql) when you need a table contract.

### SCD2 Top-N And Backfill Candidate Pools

For ranked candidate pools, such as promotion candidates, SCD2 membership can change at every evaluation timestamp. Model the active ranked set at `at_timestamp`, not just the latest row state.

- Store the business key, category or group key, rank, score inputs, `valid_from`, `valid_to`, and `is_current` explicitly.
- At each evaluation timestamp, close rows that fall out of the top N by setting `valid_to = at_timestamp` and `is_current = false`.
- Insert rows that enter the top N with `valid_from = at_timestamp`, `valid_to = NULL`, and `is_current = true` only when they are active in the latest evaluated state.
- Treat `is_current` as latest-state convenience only. It does not answer historical membership at an arbitrary backfill timestamp.
- Historical promotion runs must use `valid_from <= at_timestamp AND (valid_to > at_timestamp OR valid_to IS NULL)`.
- Validate per-category cardinality at every `at_timestamp`, not only by checking current-row flags.

Current-state check:

```sql
SELECT
    category,
    COUNT(*) AS current_candidate_count
FROM catalog.schema.promotion_candidates
WHERE is_current = TRUE
GROUP BY category
HAVING COUNT(*) > 10;
```

Point-in-time top-N check:

```sql
WITH params AS (
    SELECT TIMESTAMP '2026-05-11 02:53:05' AS at_timestamp
)
SELECT
    c.category,
    COUNT(*) AS active_candidate_count
FROM catalog.schema.promotion_candidates AS c
CROSS JOIN params AS p
WHERE c.valid_from <= p.at_timestamp
    AND (c.valid_to > p.at_timestamp OR c.valid_to IS NULL)
GROUP BY c.category
HAVING COUNT(*) > 10;
```

## Temporal Preflight

Before proposing query, ingestion, rollback, or SCD2 fixes, write down:

- source timestamp column used for ordering or filtering
- date-filter column used by task parameters or batch windows
- bounded test window, preferably historical and reproducible rather than "right now"
- point-in-time timestamp for SCD2 validity
- lookback window and timezone
- dedupe key and tie-break ordering
- expected behavior for late updates and deletes

Do not use moving `current_timestamp()`, `now()`, or right-now windows for development, backfills, model training sets, or retrospective comparisons unless the production contract explicitly requires a rolling window. Use explicit ISO timestamps or date literals so the result is reproducible.

### Window Parameter Validation

Before building downstream CTEs, materialize or select the window parameters and prove they are valid.

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-05-10 00:53:05' AS window_start_timestamp,
        TIMESTAMP '2026-05-11 02:53:05' AS window_end_timestamp
)
SELECT
    *,
    window_end_timestamp > window_start_timestamp AS window_is_valid,
    (unix_timestamp(window_end_timestamp) - unix_timestamp(window_start_timestamp)) / 3600.0 AS window_hours
FROM params
WHERE window_start_timestamp < window_end_timestamp;
```

Stop if this returns no rows. Then assert the duration is within the requested bound and run source coverage checks before complex joins:

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-05-10 00:53:05' AS window_start_timestamp,
        TIMESTAMP '2026-05-11 02:53:05' AS window_end_timestamp
)
SELECT
    'source_table_a' AS source_name,
    COUNT(*) AS pre_join_count,
    MIN(add_timestamp) AS min_add_timestamp,
    MAX(add_timestamp) AS max_add_timestamp
FROM catalog.schema.source_table_a AS s
CROSS JOIN params AS p
WHERE s.add_timestamp >= p.window_start_timestamp
    AND s.add_timestamp < p.window_end_timestamp;
```

If an analysis query unexpectedly returns zero rows, do not trust the empty result until the params CTE, source min/max timestamps, pre-join counts, post-join counts, and anti-join counts prove there is genuinely no overlap.

For Elasticsearch-backed ingestion, distinguish event time from insert/update time. If an `elastic_index_timestamp` or equivalent index timestamp is the insertion/update ordering source, use it consistently for incremental extraction and dedupe ordering instead of mixing it with event timestamps.

Rollback and delete-after-time operations must preserve SCD history unless the task explicitly asks for destructive history removal. Prefer closing or superseding validity windows over deleting historical rows.

## Source Feed Freshness And Ownership

Before depending on shared Unity Catalog source tables, especially read-only feeds, record:

- source catalog, schema, and table
- owning team or source system when known
- allowed operation mode, such as read-only, append-only target, or owned write target
- freshness column, ingestion timestamp, partition column, or Delta history field used for freshness
- freshness threshold from the task or business contract
- latest observed timestamp and whether it is stale
- fallback behavior when the feed is stale or empty

For read-only upstream catalogs, inspect schemas, sample bounded rows, freshness, and allowed operations before adding transforms. Do not edit or backfill shared source catalogs unless the user explicitly changes the ownership contract.

## Feature And Scoring Null Semantics

Define missing-evidence behavior before calculating feature scores.

- Treat absent source evidence separately from an observed value of zero.
- Do not use `coalesce(feature, 0)` unless zero is a real observed value under the business contract.
- Individual feature scorers should emit no row for missing source evidence, or emit a nullable score with an explicit `reason` such as `missing_source_evidence`.
- Aggregation should ignore absent signals or adjust denominators instead of treating missing evidence as zero or maximum risk.
- Add null-count assertions for each scorer input and output.

Suggested scorer output contract:

```text
entity_key
scorer_name
score
evidence_value
evidence_timestamp
reason
scored_at
```

Invalid entity classes should be filtered near extraction before scoring. For example, reverse-DNS domains such as `in-addr.arpa` should not enter domain scoring tables unless the business contract explicitly includes them. When feasible, carry an `excluded_reason` report for excluded entities.

## Table Inventory And Data Selection Notes

Every ETL project should keep a compact table inventory, especially when the user asks which table is the main output. Name:

- source tables and read/write ownership
- intermediate tables
- main output tables
- table purpose in one sentence
- allowed operations for each table
- timestamp columns and temporal filters
- SCD2 anchor timestamp or point-in-time predicate
- excluded entity classes or row-selection caveats

Table-making notebooks or jobs should include a compact markdown cell or module doc block that states source table, target table, time window, timestamp column, SCD2 `at_timestamp`, filters, and row-selection caveats. Keep this concise and human-facing.

For offset-based runtime parameters, parse one ISO `AT_TIMESTAMP`, derive train and inference windows from offsets, log resolved start/end values, and validate the windows before querying.

## DDL Clause Provenance Audit

To decide whether a physical-layout clause such as `CLUSTER BY` or `PARTITIONED BY` was original design or later reactive tuning, trace the DDL file's git history instead of guessing.

- Follow the file across renames and restrict the log to commits that touched the clause:

```bash
git log --follow -p -G "CLUSTER BY" -- path/to/table.sql
```

- Read the introducing commit's message and date to separate initial design from a performance patch.
- Report the clause's origin commit before recommending a layout change.

## Data Retention And Compliance Audit

When a dataset has a maximum retention window, such as 30 days, audit every stage it touches before removing anything.

- Enumerate each stage that holds the data: source ingestion, intermediate or cached tables, model training holdouts, validation backfills, and exports.
- For each stage, record the timestamp column, oldest retained row, and row count older than the retention threshold.
- Quantify out-of-compliance volume per stage before proposing deletion.
- Assess removal impact on dependent jobs, models, and point-in-time history. Prefer SCD-preserving supersession over hard deletes unless the task explicitly requires a purge.

Out-of-compliance volume per stage, using an explicit cutoff so the audit is reproducible:

```sql
WITH params AS (
    SELECT TIMESTAMP '2026-06-20 00:00:00' AS retention_cutoff  -- audit run time minus retention window
)
SELECT
    'stage_table' AS stage,
    COUNT(*) AS rows_over_retention,
    MIN(event_timestamp) AS oldest_retained
FROM catalog.schema.stage_table AS t
CROSS JOIN params AS p
WHERE t.event_timestamp < p.retention_cutoff;
```

## Timestamp Conversion

When target DDL expects `TIMESTAMP`, convert source values before the write.
Do not assign raw epoch integers or unchecked strings directly into Spark `TimestampType` fields.

Use the target column name from the DDL and keep the source column name explicit in the transform:

```python
from pyspark.sql import functions as F

# Epoch seconds to TIMESTAMP.
df = df.withColumn(
    "target_ts",
    F.expr("timestamp_seconds(CAST(source_epoch_seconds AS BIGINT))"),
)

# Epoch milliseconds to TIMESTAMP.
df = df.withColumn(
    "target_ts",
    F.expr("timestamp_millis(CAST(source_epoch_millis AS BIGINT))"),
)

# ISO-8601 text to TIMESTAMP. Use an explicit format when the source format is stable.
df = df.withColumn(
    "target_ts",
    F.to_timestamp(F.col("source_iso_ts"), "yyyy-MM-dd'T'HH:mm:ssXXX"),
)
```

Before writing, run a bounded null check on converted timestamp columns so malformed source values fail loudly:

```python
bad_rows = df.filter(F.col("source_iso_ts").isNotNull() & F.col("target_ts").isNull())
if bad_rows.limit(1).take(1):
    raise ValueError("source_iso_ts contains values that cannot be converted to target_ts")
```

## Materialization And Scale

- Filter early and project only the columns the next stage needs.
- Apply filters before `limit`, `display`, `count`, or sampling when the task is to inspect matching records. A limit before the filter only proves the sample did not contain matches.
- Do not use notebook `display()` or an unbounded `count()` as the only validation for large tables. Use bounded filters, `limit(1).take(1)`, `isEmpty()` where the active runtime supports it, partition predicates, Delta metadata, and reconciled counts scoped to the batch.
- Materialize fragile lineages before sampling or heavy reuse. Prefer `localCheckpoint(eager=True)` or checkpointing when lineage stability matters more than recomputation.
- Broadcast only genuinely small lookup tables.
- Keep file size, partitioning, and clustering aligned with downstream access patterns.

## MERGE And Concurrency

- Before a `MERGE`, prove the source has at most one row per target match key for the batch. Dedupe or rank the source explicitly when late updates, retries, or SCD windows can produce duplicates.
- Make concurrent append expectations explicit. If multiple writers can append or merge into the same Delta table, define partition scope, isolation assumptions, retry behavior, and reconciliation checks before changing the write. Treat `DELTA_CONCURRENT_APPEND` as a write-conflict signal that needs partitioning, serialization, or idempotent retry design, not a random transient error.
- For append-only pipelines, record the idempotency key or batch key used to detect duplicate loads.

## Failure Routing

- Use [delta-errors.md](delta-errors.md) for Delta or Spark SQL failures.
- Use [streaming-patterns.md](streaming-patterns.md) for Auto Loader, checkpoints, watermarks, or `foreachBatch` work.
- Use [../scripts/validate_schema.py](../scripts/validate_schema.py) before changing the write path.
