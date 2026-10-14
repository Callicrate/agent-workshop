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

- the declared current-row policy for every complete composite business key
- `valid_from` and `valid_to` ordering
- no overlapping validity windows per business key
- expected point-in-time rows for the requested window

## SCD2 Verification Routing

Before claiming an SCD2 output is verified:

- Name the full composite business key and use it consistently for scope, joins, partitions, groups, and samples.
- Persist the affected-key manifest that defines the expected verification scope, then validate the full retained history for those keys. A write-window filter cannot prove temporal integrity.
- Declare the current-row policy as `at_most_one` or `exactly_one`. If the policy or expected key scope is unknown, report SCD2 verification as unverified.
- For `exactly_one`, provide a separately declared, deduplicated required-current-key manifest that includes keys with zero observed current rows. One current row passes; zero, two, or an absent target key fail. The manifest and target names are bound roles, not claims that tenant objects exist, and the query cannot prove the manifest itself is complete.
- Use the discovered `databricks-spark-etl` skill and its plain resource path `references/delta-write-contracts.md` for the full proof. Use its optional required-current-manifest supplement for an `exactly_one` contract; do not recreate that SQL here.
- Preserve half-open interval endpoints and checks for exact duplicates, same-start rows, overlaps, invalid ranges, open ends, and point-in-time cardinality. Under `at_most_one`, deleted keys may have zero current rows and temporal gaps remain allowed unless the table contract forbids them.
- Enforce `is_current = TRUE` implies `valid_to IS NULL` only when the table declares that profile. Do not infer the converse for non-current rows.

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
