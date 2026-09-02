# Imbalanced Classification Reference

Use this reference when class skew or threshold objectives materially change the training design.

## Pick The Smallest Strategy

| Situation | Start with | Escalate only if |
|-----------|------------|------------------|
| Mild or moderate skew | `class_weight` or `scale_pos_weight` | validation PR metrics remain poor |
| LightGBM | exactly one of `isUnbalance` or `scalePosWeight` | a different algorithm is needed |
| High precision or recall target | threshold tuning on validation probabilities | the model cannot separate classes |
| Severe skew and data fits single-node memory | SMOTE or EasyEnsemble | weighting and thresholding failed |
| Label `0` means unknown, not negative | PU learning | the problem statement matches PU assumptions |

The config schema accepts only `none`, `class_weight`, `scale_pos_weight`, `smote`, `easy_ensemble`, and `pu_learning`. `scale_pos_weight` is binary-only and is required only for its strategy. `class_weight` requires structured `{label, weight}` records; for multiclass, provide every configured class exactly once. `smote_k_neighbors` is required only for `smote`. Do not pass strategy-specific fields with a different strategy.

## Class Contract

The model's class order is data, not a convention. Never assume the positive probability is `predict_proba(...)[..., 1]`.

```python
from imbalance_contract import resolve_positive_class_index

positive_index = resolve_positive_class_index(model.classes_, positive_class)
positive_probabilities = model.predict_proba(X_validation)[:, positive_index]
```

For binary thresholding, require exactly two unique fitted classes, require the configured positive class to occur once, and validate positive and negative support minima in every relevant split. A single-class validation split, all-negative validation split, multiclass estimator, unknown label, or non-finite probability is a failure, not a zero metric.

## Deterministic Threshold Tuning

Tune only on the validation split. Keep final test data untouched until the selected threshold and model are fixed.

```python
from imbalance_contract import tune_binary_threshold

optimal_threshold = tune_binary_threshold(
    y_validation,
    positive_probabilities,
    classes=model.classes_,
    positive_class=positive_class,
    minimum_positive=5,
    minimum_negative=5,
    objective="precision",  # or "f1" / "recall"
)
```

The helper uses exact definitions: `precision = TP / (TP + FP)`, `recall = TP / (TP + FN)`, and `F1 = 2 * precision * recall / (precision + recall)`; a zero denominator scores zero. It chooses the lowest threshold for an exact objective tie. Log the class order, resolved positive index, support counts, objective, threshold, and threshold-specific precision, recall, F1, and confusion matrix.

## Spark ML Probability Extraction

Spark vectors also have class order. Resolve the configured positive label against the fitted model's labels or metadata, persist that mapping, then extract the matching vector position. Do not hard-code `element_at(probability, 2)`.

When the model or pipeline does not expose a stable label-to-vector mapping, fail before threshold tuning. A visually plausible probability column with unknown class order is not a valid positive-class score.

## Metrics And Escalation

- Do not rely on accuracy alone.
- Prefer precision, recall, F1, PR-AUC, ROC-AUC, support, and confusion matrix.
- Use [common-metrics-reference.md](common-metrics-reference.md) for metric names and reporting context.
- Keep resampling separate from runtime compatibility work. Use `databricks-runtime-doctor` for DBR, CUDA, or package issues.
