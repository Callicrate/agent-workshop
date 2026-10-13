# Job-Ready Run Audit

Use this reference when an MLflow training run is expected to become a Databricks job, DAB task, promotion candidate, serving dependency, or batch-inference dependency.

## Notebook-To-Script Gate

Do not treat notebook-only execution as the final state for job-bound work. Require:

- a linear Python entrypoint such as `email_classifier.py`
- no hidden notebook cell state required for execution
- explicit imports and setup at module top level
- a `main()` or equivalent entrypoint
- a smoke command that exercises the entrypoint with a small fixed window
- MLflow logging calls in the script, not only in the original notebook

The notebook can remain useful for exploration, but the audit should mark the run job-ready only when the script path is logged and smoke-tested.
Notebook paths should end in `.ipynb`; `.py` paths are script or module entrypoints.

## Runtime Parameter Contract

Prefer `argparse` for Python job files and widgets only when the task remains notebook-backed. Log the resolved runtime contract to MLflow.

Required parameter families:

- `--at-timestamp` or `AT_TIMESTAMP`
- train/validation/test window offsets or explicit fixed start/end timestamps
- experiment path
- registered UC model name
- source table names
- output or artifact root when the project writes files outside MLflow
- dev/full-run marker when training can run in a reduced mode

Keep these names aligned across the script, DAB or job config, docs, MLflow params, and smoke command.

## Databricks Job And DAB Evidence

For job-ready training, the audit should record:

- job or DAB task key when known
- cluster or compute policy assumptions when relevant
- Python file path or notebook path used by the task
- exact parameter names and example values
- smoke execution command or run ID
- expected artifacts and registered model target

If the job config is not created yet, log the intended contract and mark the audit as a job-readiness risk until a smoke path exists.

## Inference Stub Expectations

When downstream inference is part of the handoff, require a small inference stub or loader contract that shows:

- model URI or UC model name to load
- input schema and required columns
- null or missing-source-data behavior
- output schema and predicted label/probability columns
- category or label mapping used at inference time
- example invocation with fixed inputs

The inference stub does not need to be production serving code, but it must prove the training run carries enough metadata for a future caller to load and score safely.

## Completion Rule

A run is job-ready only when the auditor can answer all of these without relying on memory:

- Which experiment path owns the run?
- Which UC model name will be registered or promoted?
- Which script or notebook produced the run?
- Which fixed timestamp and source windows created the data?
- Which parameters would a Databricks job pass?
- How was the job or script smoke-tested?
- How would downstream inference load the model and handle missing data?
