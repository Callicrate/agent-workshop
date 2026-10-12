---
name: mlflow-run-auditor
description: "Use when auditing MLflow runs, input examples, signatures, UC registry metadata, promotion readiness, or provenance; produces gaps. Do not trigger for ETL, serving issues, or live training changes."
metadata:
  short-description: Audit MLflow run readiness.
---

# MLflow Run Auditor


## When to Use

- MLflow runs exist but lack consistent parameters, input examples, signatures, or artifacts
- Notebooks/modules register models without Unity Catalog metadata
- Transformers or ensemble models are logged inconsistently
- A training run must become a Databricks job, promotion candidate, serving candidate, or batch-inference dependency
- Table-backed training needs fixed point-in-time windows, SCD2/table-version evidence, source freshness, or null-policy review
- Model names, schemas, experiment paths, or UC registry targets changed and stale references may remain
- Custom metrics, thresholds, AUC, hyperparameter tuning, or stakeholder aliases need provenance before signoff
- A logged MLflow trace shows a flat span tree instead of nesting predecessors as ancestors and fan-out work under its spawning step

## When NOT to Use

- General ETL refactors (use `databricks-spark-etl`)
- Serving/endpoint issues (use `databricks-model-serving`)

## Workflow

1. Classify the target run stage: prototype, job-ready training, promotion candidate, serving candidate, or batch-inference dependency. Apply the inherited **local readiness policy** in [references/audit-output-contract.md](references/audit-output-contract.md) before deciding whether a run is complete; do not present it as a universal MLflow requirement.
2. Run the MLflow auditor from the target repository root with an explicit Databricks `--profile <profile>`. Treat its `complete` flag separately from its readiness findings: only a `FINISHED`, complete run with no findings is clean. Use `--json` for automation; it always emits one redacted envelope and uses exit 0 for clean, 2 for completed findings/no qualifying runs, and 1 for operational or incomplete evidence.
3. For table-backed runs, fail the audit when source tables are logged without `AT_TIMESTAMP`, bounded offsets or explicit start/end timestamps, timezone, SCD2/table-version semantics, and data freshness evidence.
4. Compare the intended UC model path against code constants, `registered_model_name`, MLflow params, job arguments, inference loader paths, promotion/serving references, and docs. Report stale schemas or old model names before continuing.
5. Check metric provenance: formulas, class/label mapping, threshold context, averaging mode, AUC where applicable, confusion matrix, hyperparameter search space, selected objective, and selected params.
6. Check table-derived feature semantics: source freshness, input-null policy, skipped/unscorable row counts, feature-list artifacts, and category/label coverage artifacts.
7. Generate code patches for the current notebook/module or job entrypoint to add the missing logging calls. Prefer job-ready script patches when the run is headed toward Databricks Jobs or DAB execution.
8. Re-run a quick smoke training execution only when it is cheap, isolated, and necessary to validate audit conclusions. Do not launch full training, production registration, promotion, alias movement, or serving changes from an audit unless explicitly requested. Confirm the auditor passes. For job-bound runs, include the script entrypoint, runtime parameters, inference stub, and smoke path in the run record.

## Required Gates

- Do not mark a run reproducible when it uses implicit current time. Require fixed `AT_TIMESTAMP`, source windows, offsets, timezone, and SCD2 predicate or table-version evidence.
- Do not treat a UC registered model name as one code constant. Sweep code, MLflow params, `registered_model_name`, job config, docs, inference loaders, promotion refs, and serving refs.
- Do not log custom metrics without formulas. Precision percent aliases need canonical metric, class label, denominator, threshold, and averaging mode.
- Do not claim hyperparameter tuning happened unless the run logs algorithm, search space, trial count, scoring metric, seed, best params, and whether this was a dev or full run.
- Do not leave notebook-only execution as the final audit state when the user needs a Databricks job. Require a script entrypoint, `argparse` or widget contract, runtime parameters, and a smoke path.
- Do not let missing input data become a valid model feature silently. Log null policy and skipped/unscorable row counts.
- Do not rely on MLflow UI visibility alone. Use programmatic experiment/run lookup as the fallback, and record exact experiment path and run ID.
- Do not accept a logged trace as valid when its spans form a flat tree. Require explicit `parent_id`s so predecessors nest as ancestors and spawned/parallel tasks fan out under the spawning span. See [references/trace-span-hierarchy.md](references/trace-span-hierarchy.md).

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/audit_mlflow_runs.py](scripts/audit_mlflow_runs.py) | You need to find missing logging fields, registry drift, metric provenance gaps, or job-readiness risk | JSON report with separate `missing_metadata`, `missing_artifacts`, `inconsistent_values`, `job_readiness_risk`, `registry_drift`, `metric_provenance_risk`, `data_semantics_risk`, and `recommended_patch_location` sections |
| [references/logging-patches.md](references/logging-patches.md) | You need copy-pasteable logging stubs | Ready-to-use logging snippets |

## References

- [references/logging-patches.md](references/logging-patches.md) — copy-pasteable MLflow logging snippets
- [references/audit-output-contract.md](references/audit-output-contract.md) — required audit fields for reproducible runs
- [references/job-ready-run-audit.md](references/job-ready-run-audit.md) — notebook-to-script and Databricks job-readiness checklist
- [references/metric-provenance.md](references/metric-provenance.md) — metric formulas, thresholds, AUC, tuning, and selected-model evidence
- [references/registry-consistency.md](references/registry-consistency.md) — UC model names, experiment paths, renames, and programmatic lookup checks
- [references/trace-span-hierarchy.md](references/trace-span-hierarchy.md) — auditing and fixing flat MLflow trace span trees with explicit parent spans
