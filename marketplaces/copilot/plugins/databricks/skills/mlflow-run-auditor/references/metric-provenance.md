# Metric Provenance

Use this reference when auditing classifier metrics, custom stakeholder metrics, thresholds, model selection, or hyperparameter tuning metadata.

## Required Metric Context

For every nontrivial metric, record:

- canonical metric name
- stakeholder alias, if any
- formula or library implementation
- class label or target category
- denominator
- averaging mode
- threshold or threshold-selection rule
- split or data window where it was computed
- whether the metric was used for model selection

Do not accept a custom metric such as a precision percent alias unless it points back to the canonical metric, class label, denominator, threshold, and averaging mode.

## Classifier Metrics

For classification runs, prefer a compact bundle:

- accuracy
- precision
- recall
- F1
- AUC or ROC AUC when probabilities are available
- confusion matrix artifact
- label map or category map artifact
- threshold artifact when any non-default threshold is selected

For multi-class models, include per-class precision/recall or explain why only aggregate metrics are appropriate.

## Thresholds

Threshold metadata should answer:

- Which metric selected the threshold?
- Which class label is the positive class?
- Which data split selected the threshold?
- Was the threshold fixed, tuned, or inherited from a previous run?
- Does inference use the same threshold?

Log both a metric value such as `decision_threshold` and a parameter such as `threshold_metric` so reviewers can understand why the threshold exists.

## Hyperparameter Tuning

Do not claim tuning happened unless the run logs the search contract. Required evidence:

- tuning algorithm
- search space artifact
- trial count
- scoring metric
- random seed
- dev or full-run marker
- best params artifact
- selected model objective
- trial results or summary artifact

When tuning is skipped, log that explicitly if the stakeholder asked whether tuning happened.

## Selected Model Metadata

The selected model should carry enough context to explain why it won:

- selected objective metric
- selected threshold, if relevant
- selected params or default params
- evaluation split or point-in-time window
- model framework and package versions
- category coverage or unsupported category policy

Missing selected-model evidence should be reported as metric provenance risk, not as a generic MLflow failure.