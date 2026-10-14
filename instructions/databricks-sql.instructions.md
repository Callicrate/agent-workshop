---
description: "Databricks SQL and Delta Lake standards for explicitly named Databricks, Spark SQL, and Delta paths"
applyTo: '**/databricks/**/*.sql,**/*databricks*.sql,**/spark/**/*.sql,**/*spark-sql*.sql,**/*delta*.sql'
---

# Databricks SQL and Delta Lake Standards

Apply these rules in addition to the dialect-neutral SQL standards.

## Unity Catalog and DDL

- Use fully qualified Unity Catalog names: `catalog.schema.object`.
- Keep table creation and schema changes in dedicated SQL or deployment steps.
- Use `USING DELTA` when a Delta table is intended.
- Add table and non-obvious column comments when the owning project treats SQL as the schema contract.
- Parameterize environment-specific catalogs and schemas through the project's supported mechanism.

```sql
CREATE TABLE IF NOT EXISTS catalog_name.schema_name.events (
    event_id STRING NOT NULL,
    event_timestamp TIMESTAMP NOT NULL,
    event_date DATE NOT NULL,
    attributes MAP<STRING, STRING>
)
USING DELTA
COMMENT 'Normalized application events';
```

## Types and Missing Values

- Use `DATE` and `TIMESTAMP` for temporal values rather than storing epochs in core tables.
- Use `NULL` for missing values rather than sentinel strings or numbers.
- Use `ARRAY`, `MAP`, and `STRUCT` when nested data is part of the table contract.
- Preserve source field names during ingestion unless a documented normalization layer owns the rename.

## Delta Schema Evolution

- Prefer additive, nullable columns for compatible schema evolution.
- Treat column removal, rename, narrowing, and type-family changes as breaking changes.
- Coordinate destructive schema changes with downstream consumers.
- Do not use schema overwrite as a shortcut for an additive change.

## SCD2 Tables

- Use `valid_from`, `valid_to`, and `is_current` consistently.
- Define interval boundaries explicitly. Prefer half-open validity intervals where `valid_from` is inclusive and `valid_to` is exclusive.
- Include the full business key in current-row checks and merge predicates.
- Use one deterministic effective timestamp for all rows changed in the same operation.
- Verify that each business key has at most one current row after an upsert.

```sql
WHERE valid_from <= :point_in_time
  AND (valid_to > :point_in_time OR valid_to IS NULL)
```

## Performance

- Select only required columns and filter as early as semantics allow.
- Inspect the query plan before adding hints or physical-layout changes.
- Use liquid clustering only for verified access patterns on sufficiently large tables.
- Configure data-skipping statistics for columns used in selective filters, not every column.
- Run `ANALYZE TABLE` after material bulk changes when the project relies on optimizer statistics.

```sql
ALTER TABLE catalog_name.schema_name.events
CLUSTER BY (event_date, event_id);

ANALYZE TABLE catalog_name.schema_name.events
COMPUTE DELTA STATISTICS;
```

## Operational Safety

- Use the project's required Databricks profile for local CLI validation.
- Treat DDL, `MERGE`, `UPDATE`, and `DELETE` against shared tables as live data changes.
- Preview the affected key set and validate row counts before and after a mutation.
- Prefer the task-specific Databricks skill when the work includes ETL, SCD2 implementation, deployment, or workspace API operations.
