# Output Contract Verification

Use this after a Databricks run reaches terminal success and the job writes tables, registers models, changes aliases, or produces scored data.

## Output Inventory

Identify these roles before declaring the run verified:

| Role | Examples |
|------|----------|
| source | external feed, clean table, feature table |
| intermediate | staging table, prep table, task handoff table |
| audit | run ledger, scoring audit, MLflow run, promotion trace |
| main output | prediction table, promotion candidates, model registry alias |
| downstream consumer | serving endpoint, dashboard, next job task |

Name the main output table or registry object first. A terminal `SUCCESS` with no output check is not verified.

## Required Checks

- row counts for the requested date, partition, category, or source window
- expected changed rows versus unchanged rows
- key distribution and duplicate-key checks
- null and unscorable handling when missing source data changes business meaning
- score or label distribution when the output is scored data
- source freshness by max ingestion, update, or partition timestamp when source tables are external
- sample rows from the main output joined or compared to source fields

For SCD2 outputs, also check:

- exactly one current row per business key when that is the contract
- `valid_from` and `valid_to` ordering
- no overlapping validity windows per business key
- expected point-in-time rows for the requested window

## SQL Templates

```sql
-- Main output count by window
SELECT
    COUNT(*) AS row_count,
    COUNT(DISTINCT business_key) AS distinct_key_count
FROM catalog.schema.main_output
WHERE event_date = DATE '2026-03-04';
```

```sql
-- SCD2 current-row sanity check
SELECT
    business_key,
    COUNT(*) AS current_row_count
FROM catalog.schema.promotion_candidates
WHERE is_current = TRUE
GROUP BY business_key
HAVING COUNT(*) != 1;
```

```sql
-- SCD2 overlap check
SELECT
    a.business_key,
    a.valid_from,
    a.valid_to,
    b.valid_from AS overlapping_valid_from,
    b.valid_to AS overlapping_valid_to
FROM catalog.schema.promotion_candidates AS a
INNER JOIN catalog.schema.promotion_candidates AS b
    ON a.business_key = b.business_key
    AND a.valid_from < COALESCE(b.valid_to, TIMESTAMP '9999-12-31 00:00:00')
    AND b.valid_from < COALESCE(a.valid_to, TIMESTAMP '9999-12-31 00:00:00')
    AND a.valid_from != b.valid_from;
```

```sql
-- Score distribution and null output check
SELECT
    prediction_label,
    COUNT(*) AS row_count,
    AVG(score) AS avg_score,
    SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS null_score_count
FROM catalog.schema.scored_output
WHERE scored_date = DATE '2026-03-04'
GROUP BY prediction_label
ORDER BY row_count DESC;
```

## Handoff Rule

If output verification finds a semantic bug, switch to the owning skill for the code fix:

- `databricks-spark-etl` for Delta, SCD2, schema, or transformation errors
- `databricks-batch-inference` for scoring writes, null-source scoring, and reconciliation errors
- `databricks-ml-training` for training, promotion, threshold, or model-selection errors

Return to this deploy-monitor loop after the fix for validation, rerun if allowed, output verification, and live job-setting verification.