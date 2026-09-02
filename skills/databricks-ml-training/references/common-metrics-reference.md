# Common Classification Metrics

Use this reference when writing ML review prompts or reports.

| Metric | What It Answers |
|--------|------------------|
| Accuracy | How often predictions are correct overall |
| Precision | Of predicted positives, how many were truly positive |
| Recall | Of actual positives, how many were found |
| F1 Score | Balance between precision and recall |
| PR-AUC | Ranking quality when the positive class is rare |
| ROC-AUC | Ranking quality across classification thresholds |
| Confusion Matrix | Raw true/false positive/negative counts |

For multi-class problems, report both macro and weighted metrics when available.

## Required Metric Context

Always label:

- positive class or class being reported
- averaging mode: binary, macro, micro, weighted, per-class, or samples
- threshold used for threshold-dependent metrics
- denominator and support count
- split: train, validation, test, holdout, or production replay
- confusion matrix counts used to derive the metric

Do not report aggregate precision, recall, F1, AUC, or false positive counts without class semantics.

## Binary Confusion Matrix Formulas

For the positive class:

- precision = `TP / (TP + FP)`
- recall = `TP / (TP + FN)`
- false positive count = `FP`
- false negative count = `FN`

For the negative class treated as its own reported class:

- precision for class 0 = `TN / (TN + FN)`
- recall for class 0 = `TN / (TN + FP)`

State which class the formula refers to before explaining large counts or stakeholder-facing percentages.

## Threshold And AUC Provenance

- Name threshold-specific metrics with the threshold or gate context, such as `precision_at_threshold_0_40`.
- AUC metrics are ranking metrics across thresholds unless computed on a filtered or gated candidate set; document that input set.
- For stakeholder aliases such as precision percent, log the canonical metric name and formula next to the alias.

## Controlled Metric Iteration

Before changing promotion, threshold, or ensemble logic:

1. Capture the baseline MLflow or table metrics.
2. Identify the category-level or label-level failure.
3. Record the objective metric and rollback note.
4. Change the smallest logic needed.
5. Re-run the target date or validation slice.
6. Compare before and after confusion matrices and affected coverage.
