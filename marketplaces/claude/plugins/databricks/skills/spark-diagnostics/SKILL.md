---
name: spark-diagnostics
description: "Use when Spark OOM/hang/collect/toPandas/context/RDD/DBFS or temporal/point-in-time/table-lineage/backfill issues need diagnosis/fixes. Do not trigger for Python/DBR/package failures, ETL design, training, deploys, or orchestration."
metadata:
  short-description: Diagnose Spark execution and data-contract failures.
  author: annie
  version: "0.3"
  source: new
---

# Spark Diagnostics

## Required Shared Guidance

Before following the rest of this skill, read and apply [references/shared-skill-patterns.md](references/shared-skill-patterns.md).

If a task-specific rule in this skill conflicts with that shared guidance, follow this skill's explicit rule.

## When to Use

- Spark driver restarts, OOMs, or unexplained job aborts
- Databricks `DRIVER_NOT_RESPONDING`, hanging data-loading phases, or warning messages about compute needing workers
- DAB/job cluster definitions may not match the intended worker count, node type, Spark runtime, GPU topology, or deployed UI-visible job config
- Notebooks/modules contain `collect()`/`toPandas()` or large actions used only for logging
- Broadcast variables or SparkContext used from worker functions
- Databricks serverless code fails on `.rdd`, `.rdd.isEmpty()`, `SparkContext`, DataFrame cache APIs, catalog cache APIs, or SQL cache commands
- DBFS root disabled errors when writing temporary files
- Spark/ML pipelines have unexplained intermediate tables, inconsistent table lineage, source exhaustion questions, stale feature feeds, or point-in-time backfill/model-version issues

## When NOT to Use

- Pure ETL design (use `databricks-spark-etl`)
- Training/MLflow structure (use `databricks-ml-training`)
- Bundle deploy issues (use `databricks-asset-bundles`)

## Workflow

1. If the failure happened in Databricks, collect the job run error, exact Databricks warning, cluster JSON, DAB job cluster config, Spark version, worker count, node types, cluster policy, task cluster mapping, and whether the task imports tables or runs Spark commands. Use [references/cluster-runtime-diagnostics.md](references/cluster-runtime-diagnostics.md). Resolve compute-contract errors before editing Spark code.
2. Run the static auditor to identify driver-hazard patterns in notebooks, Python modules, SQL, and Databricks YAML. Use [references/static-detection-examples.md](references/static-detection-examples.md) when reviewing patterns the auditor may miss.
3. If serverless is the target runtime, check for `.rdd`, `SparkContext`, DataFrame/catalog cache APIs, and `CACHE`/`UNCACHE`/`REFRESH`/`CLEAR CACHE` commands before tuning performance.
4. For long data-loading phases, compare a known-working query to the current query. Inspect query shape, generated SQL plan, temporal filters, partition pruning, source-table row counts, sample thresholds, source-table exhaustiveness, and downstream table consumers. Do not rewrite the pipeline until the failing contract is identified.
5. Audit table lineage before optimizing or deleting intermediates. Inventory producers and consumers for prep tables, temporary views, feature tables, prediction tables, and curated layers. Do not create or remove prep tables unless a downstream contract requires them.
6. For ML/backfill pipelines, verify point-in-time data and model-version contracts. Each inference date must use the intended model version for that cadence, and date coverage should be audited across feature, prediction, promotion, and inference tables. Use [references/table-coverage-diagnostics.md](references/table-coverage-diagnostics.md).
7. If hazards are found, create a remediation plan that moves heavy work into distributed Spark, stages intermediates to UC Volumes, and removes driver-only collections or unsupported serverless APIs.
8. If cluster topology changes to solve Spark availability, check for compute waste and runtime mismatch. Multi-node GPU training should use the GPUs through DDP, Trainer multi-GPU support, or an explicit reason not to use them.
9. If DBFS is disabled, switch any temporary paths to UC Volumes with a project-scoped mount.
10. Re-run with the auditor and any coverage SQL to confirm hazards or contract gaps were removed.

## Anti-Patterns

- Do not treat `DRIVER_NOT_RESPONDING` as automatically a memory bug. Check compute events, cluster policy, worker count, node type, runtime, and task cluster mapping first.
- Do not use single-node GPU settings blindly for Spark workloads. Some Databricks modes require at least one worker to run Spark commands or import tables.
- Do not configure multi-node GPU clusters without checking whether the training code actually uses multiple GPUs.
- Do not assume the job UI cluster matches intended YAML. Compare bundle YAML, deployed job JSON, and UI-visible cluster spec after cluster edits.
- Do not create prep tables unless a downstream contract requires them.
- Do not reduce sample targets before proving the source table is genuinely exhausted.
- Do not use `right now` for reproducible Spark/ML diagnostics. Prefer fixed dates, `AT_TIMESTAMP`, offsets, and SCD2 time-at semantics.
- Do not backfill historical dates with one current model unless that is the explicit contract.
- Do not trust stale external feeds or feature tables without freshness and coverage checks.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/audit_spark_antipatterns.py](scripts/audit_spark_antipatterns.py) | You need to scan .py/.ipynb/.sql/.yml/.yaml for driver, serverless, DAB cluster, SQL, and temporal-window hazards | Bounded schema-1 JSON report with findings, diagnostics, and complete state |
| [references/remediation-patterns.md](references/remediation-patterns.md) | You need concrete fixes | Patterns for staging, streaming inputs, and logging safely |
| [references/cluster-runtime-diagnostics.md](references/cluster-runtime-diagnostics.md) | A Databricks run hangs, restarts, or reports compute-contract warnings | Runtime checklist before Spark code rewrites |
| [references/table-coverage-diagnostics.md](references/table-coverage-diagnostics.md) | A pipeline has backfill gaps, stale joins, point-in-time model questions, or table-contract drift | Reusable SQL patterns for coverage and freshness |

## References

- [references/remediation-patterns.md](references/remediation-patterns.md) - concrete fixes for driver hazards
- [references/static-detection-examples.md](references/static-detection-examples.md) - multi-line Spark anti-pattern detection examples
- [references/static-auditor-contract.md](references/static-auditor-contract.md) - scanner invocation, bounded output contract, lexical limits, and authoritative runtime references
- [references/cluster-runtime-diagnostics.md](references/cluster-runtime-diagnostics.md) - Databricks compute topology and job-cluster checks
- [references/table-coverage-diagnostics.md](references/table-coverage-diagnostics.md) - stage coverage, source freshness, SCD2, and backfill SQL patterns
- [references/shared-skill-patterns.md](references/shared-skill-patterns.md) - universal skill defaults
