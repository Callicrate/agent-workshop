# Table Coverage Diagnostics

Use this when Spark jobs hang during data loading, training samples are unexpectedly small, prep tables are confusing, source feeds may be stale, or ML backfills need point-in-time correctness.

## Stage Coverage Summary

Adapt this query for each stage table. Keep the date column explicit.

```sql
SELECT
  'features' AS stage,
  COUNT(*) AS row_count,
  COUNT(DISTINCT feature_date) AS distinct_dates,
  MIN(feature_date) AS min_date,
  MAX(feature_date) AS max_date,
  MAX(_loaded_at) AS latest_load_at
FROM catalog.schema.feature_table
WHERE feature_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'
UNION ALL
SELECT
  'predictions' AS stage,
  COUNT(*) AS row_count,
  COUNT(DISTINCT prediction_date) AS distinct_dates,
  MIN(prediction_date) AS min_date,
  MAX(prediction_date) AS max_date,
  MAX(_loaded_at) AS latest_load_at
FROM catalog.schema.prediction_table
WHERE prediction_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31';
```

Use fixed date windows. Do not diagnose reproducibility with `current_date()` or "right now" unless the production contract is explicitly wall-clock based.

## Missing-Date Anti-Join

```sql
WITH expected_dates AS (
  SELECT explode(sequence(DATE '2026-01-01', DATE '2026-01-31', INTERVAL 1 DAY)) AS run_date
), actual_dates AS (
  SELECT DISTINCT prediction_date AS run_date
  FROM catalog.schema.prediction_table
  WHERE prediction_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'
)
SELECT e.run_date
FROM expected_dates e
LEFT ANTI JOIN actual_dates a USING (run_date)
ORDER BY e.run_date;
```

Run this for features, predictions, promotions, inference output, and any downstream table that claims date coverage.

## Source Exhaustion Before Sample Reduction

Before lowering per-label sample targets, prove the source table does not contain enough data.

```sql
SELECT
  label,
  COUNT(*) AS source_rows,
  COUNT(DISTINCT event_date) AS source_dates,
  MIN(event_date) AS min_event_date,
  MAX(event_date) AS max_event_date
FROM catalog.schema.training_source
WHERE event_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'
GROUP BY label
ORDER BY source_rows ASC;
```

If the source has enough rows, inspect filters, joins, partition pruning, and temporal ordering before reducing targets.

## SCD2 Point-In-Time Filters

For SCD2 or slowly changing tables, lock the as-of date.

```sql
SELECT *
FROM catalog.schema.customer_dim
WHERE valid_from <= TIMESTAMP '2026-01-15 00:00:00'
  AND (valid_to > TIMESTAMP '2026-01-15 00:00:00' OR valid_to IS NULL);
```

For Delta time travel, prefer fixed timestamps in diagnostic repros.

```sql
SELECT *
FROM catalog.schema.feature_table TIMESTAMP AS OF '2026-01-15T00:00:00Z'
WHERE feature_date = DATE '2026-01-15';
```

## Point-In-Time Model Backfills

Backfills should match the training/inference cadence. If the contract says train every X days and infer for Y days, each inference date must use the model version active for that window.

Audit model usage by date:

```sql
SELECT
  inference_date,
  model_name,
  model_version,
  COUNT(*) AS scored_rows
FROM catalog.schema.inference_output
WHERE inference_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'
GROUP BY inference_date, model_name, model_version
ORDER BY inference_date, model_name, model_version;
```

Flag backfills where one current model scores all historical dates unless that is the explicit contract.

## Freshness Checks

For auxiliary feeds and feature joins, verify recency before adding Spark scorers or changing joins.

```sql
SELECT
  MAX(updated_at) AS latest_update,
  DATEDIFF(current_date(), CAST(MAX(updated_at) AS DATE)) AS days_stale,
  COUNT(*) AS rows
FROM catalog.schema.external_feed;
```

If a source is stale, record the stale-feed constraint before optimizing downstream Spark code.

## Table Contract Inventory

Before creating or deleting prep tables, inventory producers and consumers:

- table or temp view name
- producing notebook/job/function
- downstream readers
- date/window contract
- partition columns
- persistence contract: temp view, Delta table, UC Volume file, or generated feature table
- reason it cannot read directly from the canonical source table

If no consumer requires `prep_train` or `prep_val`, prefer direct reads from the canonical clean table or an explicitly named curated table.