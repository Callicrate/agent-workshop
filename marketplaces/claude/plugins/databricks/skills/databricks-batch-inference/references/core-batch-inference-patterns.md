# Core Batch Inference Patterns

Use this reference first for Databricks batch-scoring work.

## Scoring Contract

- Define the source table, target table, primary key, output columns, chunk boundary, and score filter before writing code.
- Filter to the target population before chunking, usually unscored rows or a bounded backfill window.
- Keep model metadata explicit: registered model name, alias or version, resolved version, run ID, and `scored_at` column.
- Pin the source Delta version, source window, and observation timestamp before scoring. Reuse all three on every retry.
- Carry the training handoff into scoring: feature list and order, label mapping or class order, threshold artifact, training data window, model signature, and prediction schema.
- Write governed results to the immutable prediction-history table through the DDL-complete insert-only merge contract.
- Name scoring population, holdout or review population, and promotion population separately. Do not assume training prep splits are valid scoring sources.

## Dataflow And Evidence Guardrail

- When more than one notebook, task, or table participates, read [scoring-dataflow-contract.md](scoring-dataflow-contract.md) before editing.
- Verify actual table reads, model loads, UDF definitions, writes, widgets, and task dependencies before simplifying notebooks.
- Do not infer a scoring pipeline from Databricks-looking structure, generic notebook stages, or file names alone.
- Justify every prep table by consumer, retention window, and reconciliation need. Remove stale temporary tables only after confirming no downstream task consumes them.

## Compute And Table Preflight

Run a small preflight before launching full scoring:

- confirm the selected job cluster or serverless environment can read the source Delta table
- confirm the target table exists and can be read with explicit columns
- resolve and load the model URI or alias
- execute the intended UDF shape on a limited row set
- check the job cluster mode when GPU, single-node, or Spark table reads are involved

Recognize this diagnostic as compute configuration, not data-loading logic:

```text
compute in this mode needs at least 1 worker to run Spark commands or import tables
```

## Chunking Rules

- Prefer a stable chunk boundary: date partition, hash bucket, or bounded ID range.
- Keep one writer per chunk or partition window when target rows can overlap.
- Skip empty chunks cheaply with `limit(1).count() == 0`.
- Avoid collecting the full target population. Materialize only the chunk keys needed for iteration.

## Continuous And Late-Arriving Rows

For streaming or continuous inference driven by a checkpoint, the checkpoint tracks stream
position, not source event time. Rows that land behind the current position are never revisited,
so scoring gaps grow silently.

- Add an explicit catch-up lookback window so rows that fall behind the checkpoint still get
  scored. Parameterize it, for example `continuous_catchup_lookback_minutes`, instead of trusting
  the raw checkpoint.
- Audit that lookback parameter before go-live; a missing or zero lookback is a common cause of
  unscored late-arriving rows in production.
- Reconcile scored-versus-source counts over the lookback window, not just the latest checkpoint,
  and count rows that arrived late but within the window.

## Spark Scoring Pattern

- Resolve an alias once and pass only the immutable `models:/name/version` URI to every loader and executor. Follow [uc-model-loading.md](uc-model-loading.md).
- Validate the model signature exactly before scoring and pass named column inputs as one struct in signature order. Follow [model-signature-contract.md](model-signature-contract.md).
- For pyfunc-compatible models without packaged feature lookups, use `mlflow.pyfunc.spark_udf(...)` or a pandas UDF that loads the immutable version once per worker.
- Select only the feature columns needed by the model.
- Add scoring metadata in the scored DataFrame: prediction columns, score columns, model version, model run ID, and `scored_at`.
- Keep the output schema explicit when the model returns structs or multiple scores.
- For pandas UDFs, pyfunc structs, and multi-column prediction output, declare the Spark result schema explicitly and run a limited-row schema smoke test before full scoring.

```python
from pyspark.sql import functions as F
import mlflow

immutable_model_uri = f"models:/{REGISTERED_MODEL_NAME}/{RESOLVED_MODEL_VERSION}"
score_udf = mlflow.pyfunc.spark_udf(spark, immutable_model_uri, result_type="double")

chunk_df = (
    spark.read.option("versionAsOf", SOURCE_DELTA_VERSION).table(source_table)
    .where(F.col("spam_score").isNull())
    .where(F.col("event_date") == F.lit(chunk_date))
    .select("message_id", *feature_cols)
)

scored_df = (
    chunk_df
    .withColumn(
        "raw_score",
        score_udf(F.struct(*[F.col(name).alias(name) for name in feature_cols])),
    )
    .withColumn("score", F.col("raw_score"))
    .withColumn("score_kind", F.lit("raw"))
    .withColumn("source_delta_version", F.lit(SOURCE_DELTA_VERSION))
    .withColumn("observation_timestamp", F.col(OBSERVATION_TIMESTAMP_COLUMN))
    .withColumn("model_run_id", F.lit(model_run_id))
    .withColumn("scored_at", F.current_timestamp())
)
```

## Source And Feature Snapshot Contract

Before the first attempt:

1. read `DESCRIBE HISTORY <source>` and choose one retained Delta version
2. read the source with `spark.read.option("versionAsOf", source_delta_version).table(source_table)`
3. define the inclusive start, exclusive end, and observation-timestamp column or literal
4. inventory every packaged feature table, Feature entity/view, and on-demand feature function
5. freeze the resolved model version, feature strategy, ordered feature dependency snapshot pins, threshold version, label-map version and canonical expected-label digest, unscorable-policy version, fixed one-hour gate contract, and target-population contract in canonical run-contract JSON

For models logged with Feature Engineering metadata, inventory the packaged dependencies first, then use `FeatureEngineeringClient.score_batch(immutable_model_uri, inference_df)` and provide entity keys plus the observation timestamp with the same name and type as the training `timestamp_lookup_key`. This gives point-in-time feature lookup from packaged metadata.

`score_batch` does not by itself promise a physically identical feature-table snapshot on a later retry. Record the ordered dependency snapshot pins observed for the packaged lookup and compare them before retry; if any dependency advanced, fail rather than claim replay. When byte-for-byte replay is required, pin every feature table version, perform explicit as-of joins using the observation timestamp, and score the assembled DataFrame with the immutable model URI. Record which strategy was used; never switch strategies during a retry.

Every partial or scheduler retry byte-compares canonical run-contract JSON and uses the same source Delta version, source window, observation-time definition, model version, feature lookup strategy, and ordered feature pins. If a dependency differs or a retained snapshot is no longer readable, fail the run instead of silently reading current data.

Score once into a dedicated staged-candidate Delta table, record its table name, Delta version, and snapshot digest, and gate that exact `versionAsOf` snapshot. Publication rereads the same staged version and never recomputes `scored_df` after the gate; a later staging-table commit is outside the pinned snapshot.

Official contracts:

- [Delta time travel and `versionAsOf`](https://docs.databricks.com/aws/en/tables/history)
- [Feature Engineering `score_batch`](https://docs.databricks.com/aws/en/machine-learning/feature-store/train-models-with-feature-store)
- [Point-in-time feature lookups](https://docs.databricks.com/aws/en/machine-learning/feature-store/time-series)

## Null And Unscorable Inputs

- Feature-null rows should be filtered out, scored only with a model-supported missing-value strategy, or written to an audit table with `unscorable_reason`.
- Never coerce missing source evidence to `0`, `100`, empty text, or a default class unless that behavior was part of the trained model contract.
- Reconciliation must count skipped or unscorable rows separately from successfully scored rows and unexpected null predictions.
- If no source evidence exists, prefer no score row or an explicit unscorable audit row over a valid-looking prediction.

## Reconciliation Contract

- Count target rows before and after the scoring job.
- Check remaining null scores when full coverage is expected, separating explicitly unscorable rows from NULL scores that have no unscorable reason.
- Count NULL keys separately from duplicate values for each reviewed single equality key. This does not prove a composite key, source-to-target cardinality, or general merge safety.
- Count skipped or unscorable rows separately from unexpected null predictions.
- Check stale source tables by ingestion or update timestamp when scoring a recent window.
- Check unresolved or mixed model versions in the scored window.
- Review score distributions by label and source window before promoting downstream consumers.
- Sample recent scored rows for sanity checks.
- Persist model version, run ID, score window, and threshold version with output rows or job logs so monitoring and serving comparisons can trace the scored population.
- Use [reconciliation-sql-contract.md](reconciliation-sql-contract.md) before [../scripts/emit_reconciliation_sql.py](../scripts/emit_reconciliation_sql.py). The helper prints review-only SQL, accepts typed structural identifiers, and requires an explicit acknowledgement for any raw predicate or expression; otherwise persist the same metrics in the job log or MLflow.

## Improvement Loop

Before changing thresholds, feature transforms, or ensemble logic, review live output against source fields:

- false positives
- false negatives
- null predictions
- unscorable rows
- recent scored rows in the target window

Use a bounded date or partition window for the next run instead of changing all historical output at once.

## Failure Routing

- Model loading or alias resolution issue: [uc-model-loading.md](uc-model-loading.md)
- Signature, named input, tensor, or multi-output issue: [model-signature-contract.md](model-signature-contract.md)
- Unsafe overwrite or write conflict: [safe-write-patterns.md](safe-write-patterns.md)
- General Delta schema or SCD2 issue: switch to `databricks-spark-etl`
- Real-time or endpoint task: switch to `databricks-model-serving`
