# MLflow Logging Patches

Ready-to-use code stubs for common MLflow logging gaps.

## Visualization Ownership

- Prefer MLflow's metrics view, comparison charts, model artifacts, and logged figures over hand-built chart files.
- Add custom charts only when the user explicitly asks for them or when MLflow cannot represent the required review artifact.
- When custom charts are required, log them as MLflow artifacts instead of leaving untracked local files.

## Experiment and Registry Setup

```python
import mlflow

experiment_path = "/Users/<user>/subjective/experiments/<project_name>"
registered_model_name = "<catalog>.<schema>.<model_name>"

mlflow.set_experiment(experiment_path)
mlflow.set_registry_uri("databricks-uc")

mlflow.log_params(
    {
        "experiment_path": experiment_path,
        "registered_model_name": registered_model_name,
        "databricks_workspace_path": "/Workspace/Users/<user>/subjective/<project_name>",
        "training_source_path": "email_classifier.py",
    }
)
```

Keep the experiment path, workspace path, and `registered_model_name` aligned with code constants, job arguments, docs, and inference loaders after any rename.

## Input Example and Signature

```python
import mlflow
from mlflow.models.signature import infer_signature

# After training, before logging the model:
sample_input = X_val[:5]  # small sample of validation input
predictions = model.predict(sample_input)
signature = infer_signature(sample_input, predictions)

mlflow.sklearn.log_model(
    model,
    artifact_path="model",
    input_example=sample_input,
    signature=signature,
    registered_model_name="<catalog>.<schema>.<model_name>",
)
```

## Dataset and Table Logging

```python
# Log the source table and dataset metadata
mlflow.log_param("source_table", f"{catalog}.{schema}.{table_name}")
mlflow.log_param("dataset_version", dataset_version)
mlflow.log_param("train_rows", train_df.count())
mlflow.log_param("val_rows", val_df.count())
mlflow.log_param("test_rows", test_df.count())

# Log dataset info using MLflow datasets API
import mlflow.data
dataset = mlflow.data.from_spark(
    train_df,
    table_name=f"{catalog}.{schema}.{table_name}",
    version=dataset_version,
)
mlflow.log_input(dataset, context="training")
```

## Point-in-Time Training Window

```python
from datetime import datetime, timezone

# Prefer fixed test windows over moving "now" windows.
at_timestamp = datetime.fromisoformat(args.at_timestamp).astimezone(timezone.utc)
train_start_offset_days = int(args.train_start_offset_in_days)
train_end_offset_hours = int(args.train_end_offset_in_hours)

mlflow.log_params(
    {
        "AT_TIMESTAMP": at_timestamp.isoformat(),
        "TRAIN_START_OFFSET_IN_DAYS": train_start_offset_days,
        "TRAIN_END_OFFSET_IN_HOURS": train_end_offset_hours,
        "timezone": "UTC",
        "scd2_predicate": "valid_from <= AT_TIMESTAMP AND (valid_to > AT_TIMESTAMP OR valid_to IS NULL)",
        "source_freshness_checked_at": source_freshness_checked_at.isoformat(),
        "source_max_event_timestamp": source_max_event_timestamp.isoformat(),
    }
)
```

For development runs, use a fixed December-style timestamp when that matches the project history instead of `datetime.now()` or `current_timestamp()`.

## Lifecycle Handoff Fields

Log these when the run feeds promotion, batch inference, serving, or monitoring:

```python
mlflow.log_params(
    {
        "registered_model_name": registered_model_name,
        "training_start_date": training_start_date,
        "training_end_date": training_end_date,
        "as_of_date": as_of_date,
        "target_column": target_column,
        "positive_class": positive_class,
        "feature_source_version": feature_source_version,
    }
)

mlflow.log_dict({"features": feature_columns}, "feature_list.json")
mlflow.log_dict({"label_mapping": label_mapping}, "label_mapping.json")
mlflow.log_metric("decision_threshold", decision_threshold)
mlflow.log_param("threshold_metric", threshold_metric)
```

The downstream contract should not depend on remembered feature order, class order, thresholds, or data windows.

## Transformer Model Logging

```python
import mlflow.transformers

# Log HuggingFace model with pipeline
components = {
    "model": model,
    "tokenizer": tokenizer,
}
mlflow.transformers.log_model(
    transformers_model=components,
    artifact_path="transformer-model",
    input_example=["sample text for classification"],
    registered_model_name="<catalog>.<schema>.<model_name>",
    task="text-classification",
)
```

## Threshold and Feature List Artifacts

```python
import json

# Log optimal threshold
mlflow.log_metric("optimal_threshold", best_threshold)
mlflow.log_param("threshold_metric", "f1_score")

# Log feature list as artifact
feature_list = {"features": list(X_train.columns)}
with open("feature_list.json", "w") as f:
    json.dump(feature_list, f, indent=2)
mlflow.log_artifact("feature_list.json")
```

## Metric Provenance

```python
metric_formulas = {
    "precision_percent": {
        "canonical_metric": "precision",
        "formula": "100 * true_positives / (true_positives + false_positives)",
        "class_label": positive_class,
        "threshold": decision_threshold,
        "averaging": "binary",
        "denominator": "true_positives + false_positives",
    },
    "auc": {
        "canonical_metric": "roc_auc",
        "class_label": positive_class,
        "uses_probabilities": True,
    },
}

mlflow.log_metric("auc", auc)
mlflow.log_metric("precision_percent", precision * 100.0)
mlflow.log_param("positive_class", positive_class)
mlflow.log_param("metric_averaging", "binary")
mlflow.log_dict(metric_formulas, "metric_formulas.json")
mlflow.log_dict(confusion_matrix_payload, "confusion_matrix.json")
```

Custom stakeholder metric names must point back to the canonical metric, denominator, threshold, class label, and averaging mode.

## Hyperparameter Tuning Results

```python
mlflow.log_params(
    {
        "tuning_algorithm": "random_search",
        "tuning_trials": trial_count,
        "tuning_scoring_metric": scoring_metric,
        "tuning_seed": random_seed,
        "tuning_run_mode": "dev" if is_dev_run else "full",
    }
)
mlflow.log_dict(search_space, "tuning/search_space.json")
mlflow.log_dict(best_params, "tuning/best_params.json")
mlflow.log_dict(trial_results, "tuning/trial_results.json")
```

Do not claim tuning happened from only a best score. Log the search contract and selected parameters.

## Source Freshness and Null Policy

```python
null_policy = {
    "policy": "short_circuit_unscorable",
    "reason": "missing source data must not become zero-valued model features",
    "unscorable_rows": unscorable_rows,
    "skipped_null_rows": skipped_null_rows,
    "required_source_columns": required_source_columns,
}

mlflow.log_params(
    {
        "source_freshness_checked_at": source_freshness_checked_at.isoformat(),
        "source_max_updated_at": source_max_updated_at.isoformat(),
        "null_policy": null_policy["policy"],
        "unscorable_rows": unscorable_rows,
        "skipped_null_rows": skipped_null_rows,
    }
)
mlflow.log_dict(null_policy, "null_policy.json")
```

For table-derived features, missing input data should be explicit and auditable, not silently coerced to zero or a perfect score.

## Job Entrypoint Parameters

```python
import argparse
import json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train and log the classifier")
    parser.add_argument("--at-timestamp", required=True)
    parser.add_argument("--train-start-offset-in-days", type=int, required=True)
    parser.add_argument("--train-end-offset-in-hours", type=int, required=True)
    parser.add_argument("--experiment-path", required=True)
    parser.add_argument("--registered-model-name", required=True)
    parser.add_argument("--source-table", required=True)
    return parser


args = build_parser().parse_args()

mlflow.log_dict(
    {
        "entrypoint": "email_classifier.py",
        "parameters": vars(args),
        "smoke_command": "python email_classifier.py --at-timestamp 2025-12-15T00:00:00+00:00 ...",
    },
    "job_parameter_contract.json",
)
mlflow.log_param("entrypoint", "email_classifier.py")
mlflow.log_param("job_parameters", json.dumps(sorted(vars(args).keys())))
```

When converting from notebook to job, keep widget names, argparse names, DAB task parameters, and MLflow param names aligned.

## Ensemble Metadata

```python
# Log ensemble component runs
mlflow.log_param("ensemble_type", "voting")
mlflow.log_param("n_estimators", len(estimators))
for i, (name, est) in enumerate(estimators):
    mlflow.log_param(f"component_{i}_name", name)
    mlflow.log_param(f"component_{i}_type", type(est).__name__)
```
