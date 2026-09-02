# Streaming Patterns

Use this reference only for structured streaming or Auto Loader work.

## Base Contract

- Keep the input schema or schema hints explicit.
- Keep `checkpointLocation`, target table, trigger mode, and backfill rules explicit.
- Use one stable checkpoint location per query.
- Prefer `availableNow=True` for scheduled catch-up jobs and `processingTime=...` for recurring micro-batches.
- Do not use deprecated `once=True` for new work.

## Auto Loader Pattern

```python
query = (
    spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("cloudFiles.schemaLocation", schema_location)
    .schema(schema)
    .load(source_path)
    .writeStream
    .option("checkpointLocation", checkpoint_path)
    .trigger(availableNow=True)
    .toTable(target_table)
)
```

- Keep `cloudFiles.schemaLocation` and `checkpointLocation` on durable storage.
- Use schema evolution only when additive columns are part of the contract.

## `foreachBatch` Rules

- Use `foreachBatch` when the write path needs `MERGE`, SCD2 logic, or conditional batch-level behavior.
- Make the batch function idempotent by `batch_id`, natural key, or target-table logic.
- Return early on empty batches.

```python
def process_batch(batch_df, batch_id):
    if batch_df.isEmpty():
        return
    target.alias("t").merge(batch_df.alias("s"), merge_condition).execute()
```

## Watermarks And Dedup

- Use watermarks only for stateful operations such as aggregations or event-time deduplication.
- Tie lateness windows to the business or backfill contract, not an implicit "current time" assumption.
- Use `dropDuplicatesWithinWatermark(...)` for intra-stream dedup and `MERGE` for target-table dedup across batches.

## Recovery And Monitoring

- If a checkpoint is corrupted, call out that resetting it loses state continuity.
- Inspect `query.lastProgress`, `query.status`, and `query.isActive` before changing logic.
- Log batch identifiers, rows processed, and processing time when you need durable observability.

## Failure Routing

- Missing checkpoint state, schema drift, or sink-table failures often reduce to contract issues in [core-etl-patterns.md](core-etl-patterns.md).
- If the stream fails inside Delta writes, follow [delta-errors.md](delta-errors.md) after capturing the streaming-specific context.
