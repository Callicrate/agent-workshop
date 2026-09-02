---
description: "Databricks standards for code stored in Databricks-named paths or files"
applyTo: '**/databricks/**/*.py,**/databricks/**/*.ipynb,**/databricks/**/*.sql,**/*databricks*.py,**/*databricks*.ipynb,**/*databricks*.sql'
---

# Databricks Development Standards

## Databricks CLI

- Every `databricks` CLI command must include `--profile`.
- Never rely on the default profile.
- Keep the project's required profile name documented in project docs or config.
- Treat Databricks UI URLs as context only. Do not fetch them directly over HTTP; extract IDs or names from the URL and use the Databricks CLI, SDK, or REST API.

```bash
databricks jobs list --profile my-project-profile
```

## Databricks URLs And API Access

- Browser links to jobs, runs, experiments, models, tables, and dashboards are not data APIs. They are useful for identifying the object the user wants.
- For local inspection, map the URL to an API call and use `databricks --profile <profile> api ...`, the Databricks SDK, or the `databricks-api-calls` skill.
- For Unity Catalog model-version tags, do not rely on `search_model_versions()` results. Enumerate versions with search if needed, then call `MlflowClient.get_model_version(name, version)` before reading tags or source metadata.

## Spark Session and DBUtils

- Do not create multiple Spark sessions.
- Do not call `spark.stop()`.
- Use `WorkspaceClient()` for Databricks SDK access.

### On-cluster (notebooks and jobs)

Databricks provides a pre-configured `spark` session. Use it directly; do not
recreate it. The `databricks-connect` package is **not** installed on the
runtime and conflicts with the built-in PySpark, so never import
`DatabricksSession` in code that runs on a cluster.

```python
# spark is already available; use it directly
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
dbutils = w.dbutils
```

### Remote (local IDE via Databricks Connect)

Use `DatabricksSession` from the `databricks-connect` package. Authentication
resolves through the standard config search order (explicit builder args →
named profile → env vars → DEFAULT profile).

```python
from databricks.connect import DatabricksSession
from databricks.sdk import WorkspaceClient

spark = DatabricksSession.builder.getOrCreate()
w = WorkspaceClient()
dbutils = w.dbutils
```

### Portable helper for code that runs in both environments

When a module must work both on-cluster and from a local IDE, use the
try/except pattern recommended by Databricks:

```python
def get_spark():
    """Return the active Spark session, regardless of environment."""
    try:
        from databricks.connect import DatabricksSession
        return DatabricksSession.builder.getOrCreate()
    except ImportError:
        from pyspark.sql import SparkSession
        return SparkSession.builder.getOrCreate()
```

Restrict this helper to `main()` or notebook entry cells; core functions
should still accept a `SparkSession` parameter for testability.

## Failure Semantics

- Never use `sys.exit()` or `exit()` in Databricks code paths.
- Never suppress exceptions that should fail the job.
- When an upstream artifact or tag is required by later stages, fail loudly if it is missing.

## Notebook Structure

Keep notebooks in this order when practical:

1. markdown header
2. parameters or widgets
3. imports and logging
4. configuration and table references
5. business logic
6. output or validation

Validate required widget values immediately.

## DataFrame Access and Evaluation

- Select only required columns.
- Filter before `limit()` so Spark can still push filters down.
- Treat `.count()`, `.collect()`, and `.show()` as expensive actions.
- Do not evaluate a DataFrame only for logging.
- Use `df.limit(1).count() == 0` for emptiness checks when needed.
- Cache only when reuse justifies it, and unpersist afterward.

```python
df = spark.table(source_table).select('id', 'status', 'created_at')

active_df = df.filter(F.col('status') == 'active').limit(1000)

if active_df.limit(1).count() == 0:
    raise ValueError('No active data found')
```

## Table and Environment Contracts

- Use fully-qualified Unity Catalog names.
- Assume tables are created by infrastructure unless the task explicitly includes DDL work.
- Keep table creation and schema changes in standalone `.sql` files or deployment steps, not inline in notebooks or Python modules that contain business logic.
- Do not use inline `CREATE TABLE` or `ALTER TABLE`, `spark.sql(...)` DDL, or implicit creation paths such as `saveAsTable` in non-DDL logic code. If a required table is missing, fail loudly or add the separate DDL work.
- Parameterize environment-specific values such as catalogs, schemas, endpoint names, and processing dates.
- Prefer SQL DDL or bundle config as the source of truth for schemas and deploy-time values.

## Testability and Reuse

- Restrict `DatabricksSession` acquisition to `main()` or notebook entry cells.
- Core functions should accept a `SparkSession` parameter.
- Prefer CLI-parameterized entry points (`parse_args()` then `main()`) for runnable modules.

## External Integrations

- Limit fields returned from external systems such as Elasticsearch.
- Use a stable unique tiebreaker in paginated sorts.
- Always set explicit timeouts and batch sizes for external calls.

## MLflow

- Do not disable MLflow to work around failures.
- Every ML pipeline must log runs, metrics, parameters, and artifacts coherently.
- If tracking fails, raise an exception rather than proceeding silently.
- Before expensive ML training, promotion, ensemble, or inference work, check whether the exact requested parameter set already produced the model, row, or artifact. Skip exact duplicates only when the project contract says reruns are no-ops; otherwise fail loudly.
- For SCD2-style promotion or candidate tables, maintain `valid_from`, `valid_to`, and `is_current` consistently so only the correct row is current for each key space.

## Detailed Domain Workflows

For task-specific operational detail, prefer the relevant skill:

- `databricks-spark-etl` for Delta, SCD2, and streaming patterns
- `databricks-ml-training` for MLflow and training structure
- `databricks-api-calls` for local Databricks API usage
- `databricks-asset-bundles` for `databricks.yml`
