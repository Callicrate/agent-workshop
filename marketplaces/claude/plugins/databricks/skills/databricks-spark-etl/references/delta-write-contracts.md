# Delta Write Contracts

Use this reference when changing Delta write logic, fixing schema drift, or diagnosing `DELTA_FAILED_TO_MERGE_FIELDS` and related merge conflicts.

## Contents

- [Idempotent write contract](#idempotent-write-contract)
- [Schema drift](#schema-drift)
- [Merge field conflicts](#merge-field-conflicts)
- [Reconciliation](#reconciliation)
- [SCD2 temporal invariants](#scd2-temporal-invariants)

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

## SCD2 Temporal Invariants

Use these checks after an SCD2 write or repair. They distinguish exact duplicate
tuples from overlapping validity intervals, which can inflate counts even when
the duplicate query returns no rows.

Use a persisted affected-key manifest containing every complete business key
touched by the write or repair. Join the target to that manifest, then validate
the full retained history for those keys. Do not restrict temporal validation to
the write timestamp window, because an older interval can overlap a newly
written one.

The examples use composite key `(tenant_id, business_key)`. Replace those with
the table's complete business key consistently in the manifest, joins,
partitions, groups, and samples.

### Exact Duplicate Tuples

This check only detects identical `(business key, valid_from, valid_to)` tuples.
Keep it separate from the overlap check below. SQL groups `NULL` values, so it
does not need a far-future sentinel timestamp.

```sql
WITH affected_keys AS (
    SELECT DISTINCT
        tenant_id,
        business_key
    FROM catalog.schema.repair_affected_keys
), duplicate_groups AS (
    SELECT
        t.tenant_id,
        t.business_key,
        t.valid_from,
        t.valid_to,
        COUNT(*) AS row_count
    FROM catalog.schema.target_table AS t
    LEFT SEMI JOIN affected_keys AS k
        ON t.tenant_id = k.tenant_id
        AND t.business_key = k.business_key
    GROUP BY
        t.tenant_id,
        t.business_key,
        t.valid_from,
        t.valid_to
    HAVING COUNT(*) > 1
)
SELECT COUNT(*) AS exact_duplicate_violation_count
FROM duplicate_groups;
```

For a bounded diagnostic sample, replace the final `SELECT COUNT(*)` in the
preceding statement with the following terminal query. This sample does not
prove that no further violations exist.

```sql
SELECT
    tenant_id,
    business_key,
    valid_from,
    valid_to,
    row_count
FROM duplicate_groups
ORDER BY
    tenant_id,
    business_key,
    valid_from,
    valid_to NULLS LAST
LIMIT 100;
```

### No Overlapping Half-Open Intervals

The no-overlap invariant is separate from exact duplicates. It uses a running
maximum over every preceding finite `valid_to`, plus an indicator that any
preceding interval is open ended. It is linear after the partitioned sort and
avoids an adjacent-only `LAG`, a quadratic self join, and a sentinel timestamp.

Adjacent half-open intervals are valid: `[start, end)` followed by `[end, next)`
does not overlap. Gaps are valid unless the table contract explicitly forbids
them. Multiple rows with the same `valid_from` are violations when their ranges
are otherwise valid, because both include that start instant.

```sql
WITH affected_keys AS (
    SELECT DISTINCT
        tenant_id,
        business_key
    FROM catalog.schema.repair_affected_keys
), affected_history AS (
    SELECT
        t.tenant_id,
        t.business_key,
        t.valid_from,
        t.valid_to
    FROM catalog.schema.target_table AS t
    LEFT SEMI JOIN affected_keys AS k
        ON t.tenant_id = k.tenant_id
        AND t.business_key = k.business_key
), start_groups AS (
    SELECT
        tenant_id,
        business_key,
        valid_from,
        COUNT(*) AS row_count_at_start,
        MAX(CASE WHEN valid_to IS NOT NULL THEN valid_to END) AS max_finite_end_at_start,
        MAX(CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END) AS has_open_end_at_start
    FROM affected_history
    GROUP BY
        tenant_id,
        business_key,
        valid_from
), ordered_starts AS (
    SELECT
        *,
        MAX(max_finite_end_at_start) OVER (
            PARTITION BY tenant_id, business_key
            ORDER BY valid_from
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS max_preceding_finite_end,
        MAX(has_open_end_at_start) OVER (
            PARTITION BY tenant_id, business_key
            ORDER BY valid_from
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS has_preceding_open_end
    FROM start_groups
), overlap_violations AS (
    SELECT
        tenant_id,
        business_key,
        valid_from,
        row_count_at_start,
        max_preceding_finite_end,
        has_preceding_open_end
    FROM ordered_starts
    WHERE row_count_at_start > 1
        OR valid_from < max_preceding_finite_end
        OR has_preceding_open_end = 1
)
SELECT COUNT(*) AS overlap_violation_count
FROM overlap_violations;
```

For a bounded diagnostic sample, replace the terminal count in the preceding
statement with:

```sql
SELECT
    tenant_id,
    business_key,
    valid_from,
    row_count_at_start,
    max_preceding_finite_end,
    has_preceding_open_end
FROM overlap_violations
ORDER BY
    tenant_id,
    business_key,
    valid_from
LIMIT 100;
```

The scalar count is the zero-violation proof. The `LIMIT` query only identifies
where to investigate. Run the invalid-range check first or alongside this one:

```sql
WITH affected_keys AS (
    SELECT DISTINCT
        tenant_id,
        business_key
    FROM catalog.schema.repair_affected_keys
)
SELECT COUNT(*) AS invalid_interval_count
FROM catalog.schema.target_table AS t
LEFT SEMI JOIN affected_keys AS k
    ON t.tenant_id = k.tenant_id
    AND t.business_key = k.business_key
WHERE t.valid_from IS NULL
    OR (t.valid_to IS NOT NULL AND t.valid_from >= t.valid_to);
```

### Current And Point-In-Time Cardinality

Current-row checks allow zero current rows for deleted keys. A zero violation
count proves that each affected composite key has at most one current row and
that every row marked current has `valid_to IS NULL`. It does not require a
current row to exist, and it does not require every non-current row to have a
finite end. Do not add that converse rule unless the owning table contract
explicitly adopts it.

```sql
WITH affected_keys AS (
    SELECT DISTINCT
        tenant_id,
        business_key
    FROM catalog.schema.repair_affected_keys
), current_row_violations AS (
    SELECT
        t.tenant_id,
        t.business_key,
        COUNT(*) AS current_row_count,
        COUNT(CASE WHEN t.valid_to IS NOT NULL THEN 1 END)
            AS finite_end_current_row_count
    FROM catalog.schema.target_table AS t
    LEFT SEMI JOIN affected_keys AS k
        ON t.tenant_id = k.tenant_id
        AND t.business_key = k.business_key
    WHERE t.is_current = TRUE
    GROUP BY
        t.tenant_id,
        t.business_key
    HAVING COUNT(*) > 1
        OR COUNT(CASE WHEN t.valid_to IS NOT NULL THEN 1 END) > 0
)
SELECT COUNT(*) AS current_row_violation_count
FROM current_row_violations;
```

Use an explicit, bounded evaluation-point manifest for point-in-time
cardinality. Again, read all history for the affected keys, not only rows from
the write window.

```sql
WITH evaluation_points AS (
    SELECT TIMESTAMP '2026-05-11 02:53:05' AS at_timestamp
), affected_keys AS (
    SELECT DISTINCT
        tenant_id,
        business_key
    FROM catalog.schema.repair_affected_keys
), affected_history AS (
    SELECT
        t.tenant_id,
        t.business_key,
        t.valid_from,
        t.valid_to
    FROM catalog.schema.target_table AS t
    LEFT SEMI JOIN affected_keys AS k
        ON t.tenant_id = k.tenant_id
        AND t.business_key = k.business_key
), point_in_time_violations AS (
    SELECT
        p.at_timestamp,
        h.tenant_id,
        h.business_key,
        COUNT(*) AS active_row_count
    FROM affected_history AS h
    CROSS JOIN evaluation_points AS p
    WHERE h.valid_from <= p.at_timestamp
        AND (h.valid_to > p.at_timestamp OR h.valid_to IS NULL)
    GROUP BY
        p.at_timestamp,
        h.tenant_id,
        h.business_key
    HAVING COUNT(*) > 1
)
SELECT COUNT(*) AS point_in_time_cardinality_violation_count
FROM point_in_time_violations;
```

Use a separate `LIMIT` sample from `point_in_time_violations` when the count is
nonzero. Do not infer that a zero-count key is invalid, because deletion and
contract-permitted gaps may legitimately leave no active row at a point in time.
