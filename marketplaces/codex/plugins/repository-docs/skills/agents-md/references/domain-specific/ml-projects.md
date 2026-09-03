# ML and AI Project AGENTS.md Guidance

Use this to capture model, data, and evaluation contracts that are local to a repository.
Do not duplicate general ML training, serving, runtime, and MLflow guidance that is already available in the current environment.
Document model, experiment, dataset, and service identifiers only when they are evidenced by repository files or user-provided current facts and are necessary for agent work.
Prefer variable names or config-file locations over hardcoded resource names when the repository already uses that pattern.

## Context Facts To Verify

Read these before drafting ML guidance:

- training, inference, evaluation, feature, and data-prep entry points
- config files, model registry names, experiment names, feature lists, and dataset locations
- test fixtures, smoke datasets, threshold files, and expected artifacts
- runbooks that define promotion, rollback, monitoring, or approval criteria

## High-Value AGENTS.md Entries

```markdown
## Tool and Workflow Contracts
- **Training entry point**: `src/acme/train.py`; configs live in `configs/train/*.yaml`.
- **Feature contract**: `features/email_v3.json` is the source of truth for training and inference.
- **Experiment tracking**: MLflow experiment path is `/Shared/acme/email-classifier`.
- **Promotion gate**: update `model_card.md` and `configs/thresholds.yml` with every promoted model.
```

The names above are examples only.
Replace them with verified repository names or omit them.

```markdown
## Project Rules
- Keep training and inference preprocessing in `src/acme/features.py`; do not duplicate tokenization in notebooks.
- Tests use the tiny fixture dataset under `tests/fixtures/email_sample.parquet`.
- Batch inference writes only to `predictions_staging`; promotion to prod tables is handled by `jobs/promote_predictions.yml`.
```

## What To Omit

Do not copy generic ML safety rules unless the repository has a local command, file, table, or approval workflow attached:

- random seed boilerplate
- generic "never train on test data" warnings
- broad GPU memory tips
- generic MLflow logging advice
- provider-specific LLM advice without local configuration

## Useful Do / Don't Pair

### Do

```python
features = load_feature_spec(Path("features/email_v3.json"))
train_df, validation_df, test_df = load_split_tables(config.dataset_version)
```

### Don't

```python
features = ["subject", "body"]  # Drifts from the shared training/inference feature spec.
```
