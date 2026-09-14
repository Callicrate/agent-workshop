---
name: databricks-spark-etl
description: "Use when changing Spark ETL, Delta writes, schemas, timestamps, SCD2 filters, freshness, or streaming; guides table contracts. Do not trigger for bundle deploys, model work, or Python debugging."
metadata:
  short-description: Build Databricks Spark ETL.
---

# Databricks Spark ETL


## When to Use

- Changing Delta Lake write logic or target-table contracts
- Fixing schema evolution, merge-field, nullability, or unresolved-column failures in Spark ETL code
- Converting epoch seconds, epoch milliseconds, or ISO strings to match target timestamp columns
- Defining SCD2 tables or current-record and point-in-time filtering rules
- Validating temporal windows, source coverage, source freshness, and read-only upstream catalog constraints before writing ETL logic
- Designing feature/scoring ETL where missing source evidence must not become zero, max risk, or an emitted score without an explicit contract
- Verifying SCD2/backfill/write fixes with concrete SQL assertions after a job or one-time repair runs
- Auditing DDL clause provenance through git history, or a dataset's retention/compliance window across pipeline stages
- Wiring structured streaming or Auto Loader ingestion paths

## When NOT to Use

- Databricks Asset Bundle authoring or deployment
- Model training or serving work
- Generic Python traceback debugging once the main failure is no longer Spark or Delta specific
- Spark execution failures, unsupported APIs, cluster/runtime mismatches, OOMs, skew, or performance pathologies where `spark-diagnostics` should diagnose before ETL logic is rewritten

## Workflow

1. Read [references/core-etl-patterns.md](references/core-etl-patterns.md) first. It defines contract order, write-mode selection, SCD2 rules, and lineage materialization rules.
2. For temporal Databricks or Elasticsearch ingestion work, compute, echo, and validate source timestamp columns, date-filter columns, bounded windows, SCD2 point-in-time timestamps, and dedupe expectations before proposing a query or write fix.
3. Materialize or select temporal parameters first. Assert `window_start < window_end`, assert the duration is within the requested bound, inspect source min/max timestamps, and run cheap non-empty pre-join counts before building downstream CTEs.
4. For feature/scoring ETL, define null semantics before scoring. Missing source evidence must not become zero, a maximum score, or an emitted row unless the business contract explicitly says so.
5. When using shared Unity Catalog source catalogs, record allowed operation mode, source ownership/read-only status, freshness column or Delta metadata check, latest observed data timestamp, stale-data threshold, and fallback behavior before adding dependent transforms.
6. Before changing write logic, read [references/schema-write-contract.md](references/schema-write-contract.md) and run [scripts/validate_schema.py](scripts/validate_schema.py). Choose `by-name` or `by-position` explicitly. In notebooks it validates a DataFrame against a live table; locally it compares exported canonical `StructType.jsonValue()` files.
7. Open [references/delta-errors.md](references/delta-errors.md) only when the task is a Delta or AnalysisException failure, unexpected empty result set, or Spark SQL query diagnosis.
8. Open [references/streaming-patterns.md](references/streaming-patterns.md) only when streaming, checkpoints, watermarking, or `foreachBatch` logic is in scope. Do not expand streaming work from non-streaming evidence.
9. For idempotent Delta writes, schema drift, merge-field conflict work, SCD2 repairs, or one-time write/backfill fixes, load [references/delta-write-contracts.md](references/delta-write-contracts.md).
10. When a table must be created or altered, start from [assets/scd2-table-ddl.sql](assets/scd2-table-ddl.sql), keep the DDL in a standalone `.sql` file, and keep the write path aligned to that contract.
11. After any SCD2, backfill, or write fix, provide or run a validation query block that covers affected row counts, duplicate business keys per validity window, current-row counts per group, nulls in required columns, point-in-time sample rows, no rows attributable to this write outside its intended keys or window, and unchanged pre-existing out-of-window history.
12. If the task turns into training, serving, or bundle work, switch to the corresponding skill instead of expanding this one.

## Deterministic Tools

| Resource | Use When | Outcome |
|----------|----------|---------|
| [scripts/validate_schema.py](scripts/validate_schema.py) | You need to validate a live DataFrame or exported schema against a target contract | Explicit exact, compatible, conditional, incompatible, or invalid schema decision plus casts and warnings |
| [assets/scd2-table-ddl.sql](assets/scd2-table-ddl.sql) | You need a parseable SCD2 table starter | Stable SCD2 DDL kept outside notebook or module logic |

## References

- [references/core-etl-patterns.md](references/core-etl-patterns.md) - contract order, write-mode selection, and scale rules
- [references/schema-write-contract.md](references/schema-write-contract.md) - schema-resolution, nullability, type-policy, and safe live-table pre-write gate
- [references/delta-errors.md](references/delta-errors.md) - Delta and Spark SQL failure routing
- [references/delta-write-contracts.md](references/delta-write-contracts.md) - idempotent Delta writes, schema drift, and merge conflict checks
- [references/streaming-patterns.md](references/streaming-patterns.md) - structured streaming and Auto Loader rules
