# Delta Write Contracts

Use this reference when changing Delta write logic, fixing schema drift, or diagnosing `DELTA_FAILED_TO_MERGE_FIELDS` and related merge conflicts.

## Idempotent Write Contract

Name the contract before editing code:

- target table
- primary or merge keys
- partition columns
- data window
- dedupe rule
- insert/update/delete semantics
- expected row-count reconciliation
- retry behavior after partial failure

Prefer `MERGE` for upserts into existing tables and append-only writes for immutable event or scoring history.

## Schema Drift

Before enabling schema evolution, compare:

- source DataFrame schema
- target table schema
- nullable fields
- numeric and timestamp types
- nested field shape
- column casing

Fail loudly when a required target field is missing upstream.
Do not paper over cross-stage contract breaks with silent defaults.

## Merge Field Conflicts

For merge-field errors:

1. identify the conflicting column or nested field
2. print both Spark types
3. decide whether to cast, rename, split, or update DDL
4. keep DDL changes in standalone SQL or deployment steps
5. rerun schema validation before the write

## Reconciliation

After SCD2, backfill, or write fixes, provide or run a validation query block. Include these checks:

- affected row count scoped to the batch window or repair key set
- duplicate business keys per target grain and validity window
- current-row cardinality per business key or group
- historical point-in-time cardinality for each evaluation timestamp
- nulls in required columns
- no rows attributable to this write outside its intended keys or window, plus unchanged pre-existing out-of-window history. Prove this with actual commit or batch metadata, an affected-key manifest, change data feed when available, or explicit before-and-after evidence. Do not require legitimate historical or SCD2 rows to be absent or remove them to satisfy the check.
- sample changed rows with business keys, timestamps, write metadata, and reason/status columns

Example validation block:

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-05-10 00:53:05' AS window_start_timestamp,
        TIMESTAMP '2026-05-11 02:53:05' AS window_end_timestamp,
        TIMESTAMP '2026-05-11 02:53:05' AS at_timestamp
), scoped AS (
    SELECT t.*
    FROM catalog.schema.target_table AS t
    CROSS JOIN params AS p
    WHERE t.updated_at >= p.window_start_timestamp
        AND t.updated_at < p.window_end_timestamp
)
SELECT 'affected_rows' AS check_name, COUNT(*) AS check_value
FROM scoped
UNION ALL
SELECT 'required_null_rows', COUNT(*)
FROM scoped
WHERE business_key IS NULL OR valid_from IS NULL;
```

Duplicate validity-window check:

```sql
SELECT
    business_key,
    valid_from,
    COALESCE(valid_to, TIMESTAMP '9999-12-31 00:00:00') AS validity_end,
    COUNT(*) AS row_count
FROM catalog.schema.target_table
GROUP BY business_key, valid_from, COALESCE(valid_to, TIMESTAMP '9999-12-31 00:00:00')
HAVING COUNT(*) > 1;
```

Historical point-in-time sample:

```sql
WITH params AS (
    SELECT TIMESTAMP '2026-05-11 02:53:05' AS at_timestamp
)
SELECT t.*
FROM catalog.schema.target_table AS t
CROSS JOIN params AS p
WHERE t.valid_from <= p.at_timestamp
    AND (t.valid_to > p.at_timestamp OR t.valid_to IS NULL)
ORDER BY business_key
LIMIT 50;
```
