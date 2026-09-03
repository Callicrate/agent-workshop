# Tabular Training Patterns

Use this reference only for tree-based models or Spark ML classifiers.

## Choose the Family

### XGBoost

- Strong default for tabular binary classification.
- Keep worker count, tree depth, and positive-class weighting explicit.

### LightGBM

- Use when the project already standardizes on LightGBM or it wins on validation.
- Do not set `isUnbalance=True` and `scalePosWeight` together.

### CatBoost

- Use as a strong baseline when categorical handling matters.
- Keep iterations and class-weighting choices explicit.

### Spark ML

- Use when the full feature pipeline must stay distributed.
- Keep feature assembly and probability extraction explicit because downstream metric logic often depends on them.

## Feature Contract

- Convert timestamps to explicit numeric or derived calendar features before sending them into Spark ML or tree models.
- Cast booleans to integer flags.
- Keep null handling explicit; do not let missing values silently leak into downstream metrics.
- Freeze a feature-schema artifact with version, SHA-256 fingerprint, logical type, nullability, and null policy for every selected feature and target. Compute its fingerprint with `compute_feature_schema_fingerprint`: SHA-256 of compact UTF-8 JSON with lexical keys over `version`, the declared feature list in configured order, and the target declaration. Each declaration contains exactly `name`, `logical_type`, `nullable`, and `null_policy`. Validate the exact supplied fingerprint and final positional feature order with `validate_feature_schema_before_fit` before fitting.
- Treat a feature add/remove/rename, logical-type or nullability change, target change, or null-policy change as schema evolution. Publish a new artifact version and rerun training/evaluation; do not silently cast or impute around the drift.

## Tuning and Evaluation

- Use `CrossValidator` or another deterministic grid-search strategy for Spark ML estimators.
- Keep the grid size aligned with cluster capacity; very large grids multiply quickly with folds.
- Record the exact parameter map and metric for the best run.
- Keep a stable validation split for model selection and threshold tuning.
- Keep hyperparameter tuning explicit, bounded, and repeatable: algorithm, search space, trial count, split, scoring metric, seed, selected params, and dev/full differences.
- When the user asks for additional tuning rounds, preserve the previous baseline report before changing the search space.

## Probability and Threshold Outputs

- Resolve the positive-class probability from the fitted class mapping before threshold analysis. Do not assume the positive class is vector index `1`.
- Tune thresholds on the validation split, not the training split or final test split.
- Persist `optimal_threshold` when downstream scoring depends on it.
- Do not change threshold constants or ensemble gates without logging the validation set, objective metric, before and after confusion matrix, and affected category coverage.

Spark ML vector positions are zero-based internally while `element_at` is one-based. Persist a validated label-to-vector mapping and derive the one-based position from the configured positive class. If that mapping is unavailable, fail before threshold tuning.

```python
from pyspark.sql import functions as F

positive_vector_index = label_to_probability_index[positive_class]
predictions = model.transform(validation_df).withColumn(
    "prob_positive",
    F.element_at(F.col("probability"), positive_vector_index + 1),
)
```

## Guardrails

- Keep the feature list, class order, and selected threshold as explicit artifacts.
- Use [imbalanced-classification.md](imbalanced-classification.md) only when class skew changes the training plan.
- Use [ensemble-training.md](ensemble-training.md) when training multiple members per label, category, fold, or seed.
