# Shared Diagnostic Skill Patterns

Use this reference for cross-skill diagnostic habits that apply to Spark failures without taking over unrelated Databricks domain intent.

## Evidence First

- Capture the exact failing command, notebook cell, job task, stack trace, Spark version/runtime, compute type, table names, and input/output counts before changing code.
- Distinguish parser/planner errors, runtime errors, data-quality failures, resource pressure, unsupported API usage, permissions, and local command-surface failures.
- Prefer the smallest reproduction that still exercises the failing Spark path.

## Scope Boundaries

- Use `databricks-spark-etl` for ETL design and transformation semantics.
- Use `databricks-runtime-doctor` for package/runtime/library compatibility and compute environment diagnosis.
- Use `databricks-asset-bundles` for bundle configuration semantics.
- Use `databricks-deploy-monitor` for authorized live deployment, job execution, and monitoring.
- Use `local-project-execution` for local shell, cwd, quoting, line endings, and CLI command-surface issues.

## Safety

Do not repair, rerun, cancel, deploy, or mutate production resources from a diagnostic-only task unless the user explicitly requested that live action or the current task contract already grants it. Report the ready-to-run command or evidence-backed next action instead.
