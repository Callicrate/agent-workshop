# Registry Consistency

Use this reference after any Unity Catalog schema change, model rename, experiment path move, or downstream serving/promotion handoff.

## What To Compare

Compare the intended model identity against every surface that can retain an old value:

- `registered_model_name` in the model logging call
- MLflow params such as `registered_model_name`, `uc_model_name`, and `unity_catalog_model_name`
- constants in training code
- Databricks job or DAB task parameters
- inference loader paths
- docs, README examples, and smoke commands
- promotion aliases and serving endpoint configs when present
- batch inference configs and monitoring jobs when present

Report every stale schema, stale model name, two-part non-UC model path, or experiment path mismatch as registry drift.

## Logged Model Versus Registered Model

Distinguish these concepts in the audit:

- logged model artifact: the model stored under the run, such as `runs:/<run_id>/model`
- registered model: the Unity Catalog name used for model registry, such as `<catalog>.<schema>.<model>`
- model version or alias: registry state created after registration or promotion
- inference loader: the downstream path or alias that consumers use

A run can have a valid logged model artifact and still fail registry consistency if the registered UC name is stale or downstream loaders point to the old name.

## Rename Sweep

After a rename, do a pause-and-audit pass before continuing:

1. Search code and configs for the old model name and old schema.
2. Search docs and smoke commands for the old name.
3. Check MLflow params on the new run.
4. Check `registered_model_name` in the model logging call.
5. Check inference stub or loader paths.
6. Check job/DAB task parameters.
7. Re-run a smoke training execution and confirm the new name appears in the run record.

Do not assume a single constant update fixed all surfaces.

## Experiment Path Drift

When users ask for a personal or project-specific experiment folder, the audit should verify:

- `mlflow.set_experiment` uses the intended path
- `experiment_path` is logged as a param
- the run ID resolves under that experiment programmatically
- docs and job parameters reference the same path
- workspace artifacts written outside MLflow use the intended folder root

MLflow UI visibility is not enough. If the UI is confusing or inaccessible, use programmatic lookup by experiment path and run ID.

## Programmatic Lookup Fallback

Use this flow when validating run identity:

```python
import mlflow

experiment = mlflow.get_experiment_by_name(experiment_path)
if experiment is None:
    raise RuntimeError(f"Experiment not found: {experiment_path}")

run = mlflow.get_run(run_id)
if run.info.experiment_id != experiment.experiment_id:
    raise RuntimeError("Run ID does not belong to the expected experiment")
```

Record exact experiment path and run ID in the audit report.