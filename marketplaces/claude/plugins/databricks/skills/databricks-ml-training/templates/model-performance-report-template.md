# Model Performance Report

> Generated: {timestamp}
> Source: {registry version or run ID}
> Environment: {dev or prod}

## Model Summary

{plain-language summary}

- **Framework:** {framework}
- **Feature Approach:** {feature approach}
- **Target Column:** `{target_column}`
- **Target Classes:** {target classes}
- **Positive Class:** {positive_class}
- **Primary Metric:** {primary metric}
- **Training Samples:** {count}
- **Evaluation Samples:** {count}

## Classification Metrics

| Metric | Value |
|--------|-------|
| Accuracy | {value} |
| Precision | {value} |
| Recall | {value} |
| F1 Score | {value} |
| PR-AUC | {value} |
| ROC-AUC | {value} |

## Per-Class Breakdown

| Class | Precision | Recall | F1 | Support |
|-------|-----------|--------|----|---------|
| {class} | {value} | {value} | {value} | {count} |

## Confusion Matrix

|  | Predicted {class_0} | Predicted {class_1} |
|--|---------------------|---------------------|
| **Actual {class_0}** | {count} | {count} |
| **Actual {class_1}** | {count} | {count} |

## Selection And Eligibility

- **Selection metric and split:** {selection_metric_and_split}
- **Selection tie-break:** {selection_tie_break}
- **Eligibility status:** {eligible or ineligible}
- **Eligibility evidence:** {full-mode, baseline, slice, calibration, and approval result}

## Key Training Parameters

| Parameter | Value |
|-----------|-------|
| {parameter} | {value} |

## Recommendations

### 1. {short title} - {impact}

**File:** `{path}`

{actionable recommendation}

## Appendix

- **Run ID:** `{run_id}`
- **Model URI:** `{model_uri}`
- **Experiment:** `{experiment_path}`
- **Review workflow:** `databricks-ml-training` with `references/common-metrics-reference.md`
