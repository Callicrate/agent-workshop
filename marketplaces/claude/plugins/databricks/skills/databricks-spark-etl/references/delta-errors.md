# Delta Error Routing

Use this reference only when the ETL task is already failing in Delta or Spark SQL.

## Schema And Contract Failures

### Unexpected empty result set

- Inspect: params CTE values, window order, window duration, timezone assumptions, source min/max timestamps, pre-join counts, post-join counts, and anti-join counts for each source.
- Fix: correct reversed bounds, missing overlap, stale sources, join keys, or source filters before changing business logic.
- Do not trust a zero-row result until window parameters and source coverage are proven.

Diagnostic shape:

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-05-10 00:53:05' AS window_start_timestamp,
        TIMESTAMP '2026-05-11 02:53:05' AS window_end_timestamp
)
SELECT *
FROM params
WHERE window_start_timestamp < window_end_timestamp;
```

Then run source-by-source counts before and after joins, plus anti-joins for missing coverage.

### `DELTA_FAILED_TO_MERGE_FIELDS`

- Inspect: source schema versus target schema, especially numeric width, decimal precision and scale, timestamps, and nested field types.
- Fix: cast to the target types before the write.
- Escalate to `mergeSchema` only for additive compatible columns.
- Use `overwriteSchema` only when the table contract is being replaced intentionally.

### `DELTA_SCHEMA_CHANGE_SINCE_ANALYSIS`

- Inspect: whether a long-lived DataFrame or cached relation was created before the target schema changed.
- Fix: rebuild the DataFrame or re-read the target table after the schema change.
- Do not keep cached pre-DDL DataFrames around a contract change.

### `UNRESOLVED_COLUMN` or `AnalysisException: cannot resolve ...`

- Inspect: `df.columns`, DataFrame aliases, case sensitivity, and nested field paths.
- Fix: correct the DataFrame expression or rename earlier in the flow.
- Do not assume the target DDL creates a missing source column.

### `TABLE_OR_VIEW_NOT_FOUND`

- Inspect: whether the target table was created by the separate DDL step the pipeline expects.
- Fix: add or update a standalone `.sql` DDL file, apply it, then re-run the write.
- Do not patch around this with inline `CREATE TABLE` in notebook or module logic.

## Constraint Failures

### `NOT NULL` violation

- Inspect: null counts before the write.
- Fix: filter invalid rows, fill with an explicit default, or fail loudly before the write.

### `CHECK` constraint violation

- Inspect: the constraint definition and the rows that violate it.
- Fix: align the transformation with the table rule before writing.

## Concurrency And Storage Failures

### `CONCURRENT_WRITE_FAILED` or `ConcurrentAppendException`

- Capture the full error and its condition, then record the target table and version, isolation level, read scope, conflicting operation, and the affected run or writer. Do not reduce a concurrency failure to a generic "another writer exists" explanation.
- Interpret `WHOLE_TABLE_READ` literally: the transaction read the whole table. Under that read scope, `MERGE`, `UPDATE`, and `DELETE` can still conflict even when the intended writes appear separate.
- First narrow target reads with contract-valid batch, window, or partition predicates. When concurrent operations must be disjoint, include those target predicates in the operation condition, such as the `MERGE` match condition. Never change the idempotency contract or make historical target rows miss a match merely to avoid a conflict.
- Treat row-level concurrency and deletion vectors as conditional capabilities, not a default repair. Verify the runtime, table layout, deletion-vector state, and operation predicates first. The table owner must approve enabling deletion vectors, changing isolation, or repartitioning.
- Retry only an idempotent operation under a bounded, owner-approved policy. Recompute from the current snapshot and reconcile the batch or idempotency key before retrying. Serialize repeated conflicts when that policy does not apply or reconciliation is inconclusive.
- After a successful write, prove the committed history and table version, contract-scoped counts, and duplicate business or idempotency keys. Prove that no rows attributable to this write occurred outside the intended keys or window, and that pre-existing out-of-window history is unchanged. Use commit or batch metadata, affected-key reconciliation, change data feed when available, or an explicit before-and-after comparison. Do not require legitimate historical or SCD2 rows to be absent or remove them to satisfy the check.

### Missing Delta files or checkpoint state

- Inspect: whether files were deleted or checkpoints moved outside Delta operations.
- Fix: restore or rebuild the managed state before retrying.
- Do not treat missing storage state as a transient Spark error.

### Large-file output or multipart upload failure

- Inspect: repartitioning, coalesce usage, and partition layout.
- Fix: repartition before write and avoid `coalesce(1)` on large datasets.

## Lineage And Performance Failures

### Indeterminate shuffle after `sample()` or repeated actions

- Inspect: joined or heavily transformed DataFrames that are sampled or reused multiple times.
- Fix: materialize with `localCheckpoint(eager=True)` or checkpoint before sampling or repeated downstream actions.

### OOM, skew, or runaway shuffle

- Inspect: column projection, filter position, join strategy, and shuffle width.
- Fix: filter earlier, reduce wide shuffles, disable accidental broadcast, and repartition deliberately.

## Fix Order

1. Compare source and target schemas.
2. Confirm the write mode matches the contract.
3. Rebuild stale DataFrames created before DDL or schema changes.
4. Re-run the smallest failing write path.
