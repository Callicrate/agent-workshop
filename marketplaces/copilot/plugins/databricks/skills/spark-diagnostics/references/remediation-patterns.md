# Spark Remediation Patterns

## Remove Driver-Only Collections

- Replace `df.collect()`/`toPandas()` used for logging with:
  - `df.select(columns).orderBy(sort_cols).limit(n).toJSON().take(k)` for bounded previews
  - or write to a small Parquet sample in UC Volumes and log the path

## Stage Intermediates to UC Volumes

- Use Unity Catalog Volumes for scratch space when DBFS root is disabled:

```python
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
vol_path = "/Volumes/<catalog>/<schema>/<volume>/scratch/"
(df
  .coalesce(16)
  .write.mode("overwrite")
  .parquet(vol_path + "prep_sample"))
```

## Streaming-friendly Inputs

- For training that fails due to driver memory:
  - Write Parquet chunks to UC Volumes
  - Use HuggingFace streaming dataset to iterate over Parquet files without loading all into memory

## Broadcast/Context Misuse

- Do not capture `SparkContext` inside UDFs or worker functions
- Pass configuration via literals/params; avoid broadcasting large objects

## Databricks Serverless Compatibility

Serverless Spark does not support every API available on classic clusters.
When the target runtime is serverless, remove RDD and unsupported SQL patterns before chasing performance tuning.

### Empty checks

```python
# WRONG - .rdd is not supported on Databricks serverless
if df.rdd.isEmpty():
    return

# CORRECT - bounded DataFrame action
if not df.limit(1).take(1):
    return
```

### Custom row logic

```python
# WRONG - dropping to RDDs for row transforms
result = df.rdd.map(parse_row).toDF()

# CORRECT - stay in DataFrame APIs when possible
result = df.selectExpr("id", "transform(values, x -> upper(x)) as values")
```

Use `mapInPandas` or `mapInArrow` only when the logic cannot be expressed cleanly with SQL or DataFrame functions.
Keep the batch schema explicit and test on a small bounded input first.

### Cache APIs

Databricks serverless rejects DataFrame cache APIs (`cache`, `persist`,
`unpersist`, and `checkpoint`), catalog cache methods, and SQL `CACHE TABLE`,
`UNCACHE TABLE`, `REFRESH TABLE`, and `CLEAR CACHE` commands.

Prefer a Delta table, temporary view, or UC Volume file only when it matches an
actual pipeline contract. Do not replace a cache call mechanically with a new
persisted object.
If the table is part of the pipeline contract, create or alter it through the infrastructure SQL path before the Spark job writes to it.

## Runtime Contract Before Code Rewrites

When a Databricks run hangs before useful logs or reports `DRIVER_NOT_RESPONDING`, first verify the compute contract:

- task cluster key matches the intended job cluster
- deployed job JSON and UI-visible cluster spec match the edited YAML
- worker count is compatible with Spark commands and table imports
- node type and Spark runtime match the workload
- GPU workers are actually used by the training stack when present

Treat `num_workers: 0` as a topology fact, not an automatic defect. Escalate it
only after the assigned task, access mode, and Spark/table-work requirement are
established from configuration or runtime evidence.

## Data Loading Hangs

When data loading hangs, compare the last known working query to the current one:

- table names and lineage
- filters and joins
- date windows and SCD2 as-of semantics
- ordering and limits
- partition pruning
- source row counts by label/category/date
- sample thresholds and source exhaustion

Do not reduce sample targets until the source table is proven insufficient.

## Point-In-Time Backfills

Backfills that train and infer over history need a model/data cadence contract. Verify each inference date uses the intended model version for that date range. If the job loads one champion model at startup and scores all historical dates, confirm that is explicitly desired before treating output as valid.

## Logging Without Actions

- Avoid actions solely for logging. Use `explain()` or bounded `limit()` previews.
