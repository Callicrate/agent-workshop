# Databricks and Spark AGENTS.md Guidance

Do not duplicate platform-wide Databricks guidance that is already available in the current environment.
Use this reference to record repository-specific Databricks contracts, names, commands, and exceptions.
Document resource names only when they are evidenced by repository files or user-provided current facts and are necessary for agent work.
Prefer variable names or config-file locations over hardcoded resource names when the repository already uses that pattern.

## Context Facts To Verify

Check these before writing Databricks AGENTS.md guidance:

- `databricks.yml`, bundle resource files, job definitions, and target names
- notebook paths, Python package entry points, wheel tasks, SQL files, and DLT or workflow definitions
- catalog, schema, volume, model, experiment, and service-principal naming conventions
- local profile names, deployment commands, smoke tests, and runbook docs

## High-Value AGENTS.md Entries

```markdown
## Tool and Workflow Contracts
- **Bundle profile**: use `--profile default` for local CLI commands.
- **Defined deployment command**: `databricks bundle deploy -t dev --profile default`; document only, do not run during AGENTS.md authoring unless the user explicitly asks.
- **Table namespace**: dev tables live under `acme_dev.pipeline_*`; prod tables live under `acme_prod.pipeline_*`.
- **DDL source**: table definitions are in `sql/ddl/`; Python modules must not create tables implicitly.
```

The names above are examples only.
Replace them with verified repository names or omit them.

```markdown
## Project Rules
- Keep notebook widgets aligned with `resources/jobs.yml` parameters.
- Put reusable Spark logic in `src/acme/`; notebooks should only parse widgets and call package functions.
- Update `docs/data-contract.md` when changing output columns consumed by downstream jobs.
```

## What To Omit

Do not copy broad Databricks guidance already supplied elsewhere, such as:

- general `spark.stop()` warnings
- generic Unity Catalog, DBUtils, or MLflow rules
- generic Delta write advice
- expensive-action warnings without a repository-specific data scale or failure history

Include those only when this repository has a concrete local exception, path, profile, table, or job contract agents must know.

## Useful Do / Don't Pair

### Do

```python
catalog = dbutils.widgets.get("catalog")
source_table = f"{catalog}.bronze.events"
```

### Don't

```python
source_table = "prod.bronze.events"  # Hardcodes the environment and bypasses job parameters.
```
