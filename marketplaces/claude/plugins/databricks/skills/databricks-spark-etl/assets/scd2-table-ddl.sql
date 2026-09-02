-- =============================================================================
-- Purpose: Replace catalog_name, schema_name, and table_name before running.
-- Pattern: Minimal SCD2 contract with one current row per business key.
-- =============================================================================

CREATE TABLE IF NOT EXISTS catalog_name.schema_name.table_name (
    business_key STRING NOT NULL COMMENT 'Natural business key',
    business_value STRING,
    status STRING,
    category STRING,
    metadata MAP <STRING, STRING>,
    valid_from TIMESTAMP NOT NULL COMMENT 'Record validity start, inclusive',
    valid_to TIMESTAMP COMMENT 'Record validity end, exclusive',
    is_current BOOLEAN NOT NULL DEFAULT TRUE
    COMMENT 'True only for the active version',
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp()
    COMMENT 'Insert timestamp',
    updated_at TIMESTAMP NOT NULL DEFAULT current_timestamp()
    COMMENT 'Latest update timestamp',
    record_hash STRING COMMENT 'Hash of business columns for change detection'
)
USING DELTA
COMMENT 'SCD2 table starter for table_name'
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact' = 'true',
    'delta.logRetentionDuration' = 'interval 30 days',
    'delta.deletedFileRetentionDuration' = 'interval 7 days'
);

ALTER TABLE catalog_name.schema_name.table_name
        ADD CONSTRAINT table_name_current_row
        CHECK (is_current = FALSE OR valid_to IS NULL);

ALTER TABLE catalog_name.schema_name.table_name
        ADD CONSTRAINT table_name_validity_range
        CHECK (valid_to IS NULL OR valid_from < valid_to);

-- Current-state query
-- SELECT business_key, business_value, status, category
-- FROM catalog_name.schema_name.table_name
-- WHERE is_current = TRUE;

-- Point-in-time query
-- SELECT business_key, business_value, status, category
-- FROM catalog_name.schema_name.table_name
-- WHERE valid_from <= TIMESTAMP '2025-06-15 00:00:00'
--   AND (valid_to > TIMESTAMP '2025-06-15 00:00:00' OR valid_to IS NULL);
