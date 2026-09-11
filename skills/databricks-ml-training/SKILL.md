---
name: databricks-ml-training
description: "Use when training Databricks models with reproducible contracts or checking promotion eligibility offline. Do not trigger for serving, bundles, ETL-only work, or runtime/package failures."
metadata:
  short-description: Train Databricks ML models.
---

# Databricks ML Training

## When to Use

- Training or refactoring a Databricks ML pipeline
- Defining the training contract: experiment path, model name, data window, split sizes, feature list, and threshold artifact
- Making training, promotion, ensemble, or inference steps idempotent after partial failures or reruns
- Defining point-in-time table-backed training with immutable Delta versions, per-example timestamps, and SCD2 predicates
- Auditing MLflow run provenance, logged models, UC model versions, and best-model selection gates
- Fine-tuning transformers or training tabular classifiers on Databricks
- Comparing MLflow runs or reviewing reproducibility gaps in training outputs

## When NOT to Use

- Deploying or monitoring serving endpoints
- Editing Databricks Asset Bundle deployment config
- General Spark ETL work that is not a training pipeline
- Runtime or package-compatibility failures where the main problem is DBR, CUDA, or library drift

## Related Skills

- databricks-spark-etl - Delta table reads/writes, Spark DataFrame transforms, schema evolution, SCD2 patterns
- databricks-model-serving - Deploying models to endpoints, batch inference, A/B traffic splitting
- databricks-asset-bundles - Creating/editing databricks.yml, job resources, dev/prod targets, bundle deployment
- databricks-api-calls - Calling Databricks REST APIs from local terminal, querying SQL warehouses
- databricks-runtime-doctor - HF and torch compatibility issues, DBR drift, CUDA problems, and DDP flags

## Workflow

1. Start from [assets/parameter-block-template.py](assets/parameter-block-template.py) and [assets/training-config-schema.json](assets/training-config-schema.json). Validate a config before adding model code:

   ```powershell
   python skills/databricks-ml-training/scripts/validate_training_config.py --config training-config.json
   ```

2. Load [references/core-training-patterns.md](references/core-training-patterns.md) first. It defines the shared contract for MLflow, Unity Catalog, required artifacts, reproducibility, idempotency, and the post-training selection gate.
3. If training data is table-backed, load [references/point-in-time-training.md](references/point-in-time-training.md). Record every source's immutable Delta version and commit ID, then join SCD2 features at each example's prediction timestamp. Do not use current time implicitly.
4. If Databricks compute, GPU, Spark table import, or deployed job clusters are in scope, load [references/training-compute-preflight.md](references/training-compute-preflight.md) and verify the actual job cluster after bundle deploy, not only YAML.
5. Training reruns default to dev or smoke scale unless the user explicitly asks for full retraining or the task contract says full retraining is required.
6. When MLflow run completeness or reproducibility is in scope, load [references/mlflow-dataset-metadata.md](references/mlflow-dataset-metadata.md). Keep experiment path, run IDs, logged models, UC model versions, dataset, feature, threshold, and artifact metadata visible. Input examples must be bounded synthetic or redacted data.
7. If the work crosses training, promotion, batch inference, or serving, also load [references/ml-lifecycle-handoffs.md](references/ml-lifecycle-handoffs.md). Keep deterministic selection separate from promotion eligibility and make the downstream contract explicit before changing code.
8. Then load only the model-family reference the task needs: [references/tabular-training-patterns.md](references/tabular-training-patterns.md) for XGBoost, LightGBM, CatBoost, or Spark ML; [references/huggingface-transformers.md](references/huggingface-transformers.md) for HuggingFace fine-tuning.
9. Load [references/ensemble-training.md](references/ensemble-training.md) when the model is an ensemble, per-label model bundle, or promotion unit composed of multiple members.
10. Load [references/imbalanced-classification.md](references/imbalanced-classification.md) when skew handling, class weights, or threshold tuning are part of the task. Use its helper to resolve the positive-class index from `classes_`; never assume a probability-vector position.
11. Before calling training complete, run the post-training gate: exactly one best model, non-null model object, logged selection metric and params, MLflow run ID, UC model version only when explicitly requested, required artifacts, and durable promotion or handoff rows.
12. If the failure is mainly runtime or package compatibility rather than training design, switch to databricks-runtime-doctor.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/validate_training_config.py](scripts/validate_training_config.py) | You need an offline, value-free config check | Strict schema and semantic validation |
| [scripts/point_in_time_contract.py](scripts/point_in_time_contract.py) | You need split or feature-validity checks | Pairwise-disjoint split and per-example SCD2 checks |
| [scripts/imbalance_contract.py](scripts/imbalance_contract.py) | You need positive-class or threshold checks | Class-order-safe binary validation and deterministic thresholding |
| [scripts/promotion_eligibility.py](scripts/promotion_eligibility.py) | You need a selection or eligibility decision | Separate deterministic selection from promotion gating |
| [scripts/mlflow_example_privacy.py](scripts/mlflow_example_privacy.py) | You need to scan MLflow input-example columns | Direct-identifier and token-name rejection |
| [scripts/feature_schema_contract.py](scripts/feature_schema_contract.py) | You need a pre-fit schema check | Frozen feature/target type and nullability validation |
| [assets/parameter-block-template.py](assets/parameter-block-template.py) | You need a stable training configuration block | Side-effect-free run context |
| [assets/training-config-schema.json](assets/training-config-schema.json) | You need the machine-readable config contract | Closed Draft 2020-12 parameter surface |
| [templates/model-performance-report-template.md](templates/model-performance-report-template.md) | You need a result-review starter | Model-performance report shell |

## References

- [references/core-training-patterns.md](references/core-training-patterns.md) - shared training contract, required artifacts, reproducibility rules, and idempotency checks
- [references/point-in-time-training.md](references/point-in-time-training.md) - immutable Delta snapshots, per-example SCD2 joins, and split disjointness
- [references/training-compute-preflight.md](references/training-compute-preflight.md) - Databricks job cluster, GPU runtime, Spark import, and multi-GPU checks
- [references/mlflow-dataset-metadata.md](references/mlflow-dataset-metadata.md) - MLflow dataset, feature, threshold, privacy, and run metadata checklist
- [references/ml-lifecycle-handoffs.md](references/ml-lifecycle-handoffs.md) - selection, eligibility, and training-to-inference handoff fields
- [references/tabular-training-patterns.md](references/tabular-training-patterns.md) - tree-model and Spark ML training rules
- [references/huggingface-transformers.md](references/huggingface-transformers.md) - transformer-specific training contract
- [references/ensemble-training.md](references/ensemble-training.md) - ensemble member logging, packaging, promotion, and metric provenance
- [references/imbalanced-classification.md](references/imbalanced-classification.md) - skew handling and class-order-safe thresholding
- [references/common-metrics-reference.md](references/common-metrics-reference.md) - classification metrics for result review and reporting
