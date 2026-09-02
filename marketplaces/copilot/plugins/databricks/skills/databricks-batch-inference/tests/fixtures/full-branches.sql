-- GENERATED RECONCILIATION SQL. Review against the scoring contract before execution.
-- Typed table and column arguments were validated and canonicalized as quoted identifiers.
-- This generator does not execute SQL and does not prove cross-table reconciliation, key cardinality, or MERGE safety.

-- UNSAFE SQL FRAGMENTS: copied verbatim from trusted expert input after explicit acknowledgement.
-- Do not put secrets in unsafe SQL fragments. Human review is required before execution.
-- The generator does not parse, validate, or sanitize those fragments.

-- Before count for the target population, run before the scoring job
SELECT
    COUNT(*) AS total_rows,
    COUNT(`score`) AS already_scored_rows
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `event_date` = DATE '2026-08-29';

-- After count for the target population, run after the scoring job
SELECT
    COUNT(*) AS total_rows,
    COUNT(`score`) AS scored_rows,
    COUNT(*) - COUNT(`score`) AS null_score_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `event_date` = DATE '2026-08-29';

-- NULL-key count for the target population
SELECT
    COUNT(*) AS null_key_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `target id` IS NULL
    AND (`event_date` = DATE '2026-08-29');

-- Single non-NULL equality-key duplicate diagnostic for target.
-- It is not proof of a composite key, source-to-target cardinality, or general MERGE safety.
SELECT
    `target id`,
    COUNT(*) AS row_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `target id` IS NOT NULL
    AND (`event_date` = DATE '2026-08-29')
GROUP BY `target id`
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 100;

-- NULL-key count for the source population
SELECT
    COUNT(*) AS null_key_count
FROM `source_cat`.`raw`.`messages`
WHERE `source_id` IS NULL
    AND (`ingested_at` >= TIMESTAMP '2026-08-28 00:00:00');

-- Single non-NULL equality-key duplicate diagnostic for source.
-- It is not proof of a composite key, source-to-target cardinality, or general MERGE safety.
SELECT
    `source_id`,
    COUNT(*) AS row_count
FROM `source_cat`.`raw`.`messages`
WHERE `source_id` IS NOT NULL
    AND (`ingested_at` >= TIMESTAMP '2026-08-28 00:00:00')
GROUP BY `source_id`
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 100;

-- Source rows with required evidence missing in the source population
SELECT
    COUNT(*) AS null_source_row_count
FROM `source_cat`.`raw`.`messages`
WHERE (`subject` IS NULL OR `body text` IS NULL)
    AND (`ingested_at` >= TIMESTAMP '2026-08-28 00:00:00');

-- Source freshness for the selected source population
WITH source_freshness AS (
    SELECT
        COUNT(*) AS source_row_count,
        COUNT(`ingested_at`) AS nonnull_timestamp_count,
        MAX(`ingested_at`) AS max_source_timestamp
    FROM `source_cat`.`raw`.`messages`
WHERE `ingested_at` >= TIMESTAMP '2026-08-28 00:00:00'
)
SELECT
    source_row_count,
    nonnull_timestamp_count,
    max_source_timestamp,
    CASE
        WHEN source_row_count = 0 THEN 'empty_source'
        WHEN nonnull_timestamp_count = 0 THEN 'missing_timestamps'
        WHEN max_source_timestamp < current_timestamp() - INTERVAL 2 DAYS THEN 'stale'
        ELSE 'fresh'
    END AS source_freshness_status
FROM source_freshness;

-- Target outcome accounting. Without an unscorable reason column, every NULL score is unexpected.
SELECT
    'target population with the acknowledged target predicate' AS population_scope,
    COUNT(CASE WHEN `score` IS NULL AND NULLIF(TRIM(CAST(`unscorable_reason` AS STRING)), '') IS NOT NULL THEN 1 END)
        AS unscorable_count,
    COUNT(CASE WHEN `score` IS NULL AND NULLIF(TRIM(CAST(`unscorable_reason` AS STRING)), '') IS NULL THEN 1 END)
        AS unexpected_null_score_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `event_date` = DATE '2026-08-29';

-- Recorded unscorable reasons in the target population
SELECT
    NULLIF(TRIM(CAST(`unscorable_reason` AS STRING)), '') AS unscorable_reason,
    COUNT(*) AS row_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `score` IS NULL
    AND NULLIF(TRIM(CAST(`unscorable_reason` AS STRING)), '') IS NOT NULL
    AND (`event_date` = DATE '2026-08-29')
GROUP BY NULLIF(TRIM(CAST(`unscorable_reason` AS STRING)), '')
ORDER BY row_count DESC;

-- Unresolved model versions among scored target rows only
SELECT
    COUNT(*) AS unresolved_model_version_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `score` IS NOT NULL
    AND (`model_version` IS NULL OR TRIM(CAST(`model_version` AS STRING)) = '')
    AND (`event_date` = DATE '2026-08-29');

-- Model versions present among scored target rows only
SELECT
    `model_version` AS model_version,
    `model_run_id` AS model_run_id,
    COUNT(*) AS row_count
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `score` IS NOT NULL
    AND (`event_date` = DATE '2026-08-29')
GROUP BY `model_version`, `model_run_id`
ORDER BY row_count DESC;

-- Score distribution in the target population
SELECT
    `label`,
    `event_date`,
    COUNT(*) AS row_count,
    AVG(`score`) AS avg_score,
    MIN(`score`) AS min_score,
    MAX(`score`) AS max_score
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `score` IS NOT NULL
    AND (`event_date` = DATE '2026-08-29')
GROUP BY `label`, `event_date`
ORDER BY `label`, `event_date`;

-- Bounded key-ordered sample; nondeterministic among duplicate keys
SELECT
    `target id`,
    `score`
FROM `pred``ictions`.`prod schema`.`scores`
WHERE `score` IS NOT NULL
    AND (`event_date` = DATE '2026-08-29')
ORDER BY `target id` ASC NULLS LAST
LIMIT 25;

-- Expected versus actual validated source and target table roles
WITH expected_tables AS (
    SELECT *
    FROM VALUES
    ('source', 'source_cat', 'raw', 'messages', '`source_cat`.`raw`.`messages`'),
    ('target', 'pred`ictions', 'prod schema', 'scores', '`pred``ictions`.`prod schema`.`scores`')
    AS t(table_role, table_catalog, table_schema, table_name, fully_qualified_name)
),
existing_tables AS (
    SELECT
        table_catalog,
        table_schema,
        table_name
    FROM `pred``ictions`.`information_schema`.`tables`
UNION ALL
    SELECT
        table_catalog,
        table_schema,
        table_name
    FROM `source_cat`.`information_schema`.`tables`
)
SELECT
    e.table_role,
    e.fully_qualified_name,
    CASE WHEN x.table_name IS NULL THEN FALSE ELSE TRUE END AS table_exists
FROM expected_tables AS e
LEFT JOIN existing_tables AS x
    ON e.table_catalog = x.table_catalog
    AND e.table_schema = x.table_schema
    AND e.table_name = x.table_name
ORDER BY e.table_role;
