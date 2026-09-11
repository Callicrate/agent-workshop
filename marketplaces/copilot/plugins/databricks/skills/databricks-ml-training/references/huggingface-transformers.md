# HuggingFace Transformers on Databricks

Use this reference only for transformer fine-tuning on Databricks.

## Environment Contract

- Set the HuggingFace cache under the configured Unity Catalog Volume or project-owned artifact root before importing `transformers` or `datasets`. Do not use `/dbfs/Workspace/Shared`.
- Keep the foundation model, label mapping, text columns, output paths, and experiment path configurable.
- Start with a small dev subset before running the full training job.
- Run [training-compute-preflight.md](training-compute-preflight.md) before GPU training. Verify the deployed job cluster, not only the bundle YAML.

```python
import os

def configure_hf_cache(hf_cache_dir: str) -> None:
    """Run only at the executable entry point after path validation."""

    os.environ["HF_HOME"] = hf_cache_dir
    os.environ["TRANSFORMERS_CACHE"] = f"{hf_cache_dir}/transformers"
    os.environ["HF_DATASETS_CACHE"] = f"{hf_cache_dir}/datasets"

configure_hf_cache(str(run_context.model_output_dir.parent / "hf_cache"))
```

The entry point may create the validated cache directory immediately before a deliberate write. Imports and configuration modules must not create it.

## Data Contract

- Convert Spark data to a HuggingFace dataset only after selecting the minimal text and label columns.
- Keep tokenization batched and pure.
- Use `DataCollatorWithPadding` for dynamic padding.
- Keep `id2label` and `label2id` explicit.

## TrainingArguments Contract

- Keep `output_dir`, batch sizes, epoch count, logging cadence, and best-model metric explicit.
- Use a version-safe eval key because `transformers >= 4.46` renamed `evaluation_strategy` to `eval_strategy`.
- Set `ddp_find_unused_parameters=False` unless the model actually needs unused-parameter detection.
- If more than one GPU is provisioned, require DDP, Accelerate, or equivalent multi-GPU evidence. Log device count and process count.
- If Spark table reads are required, reject single-node GPU compute unless data has already been materialized to a local or shared cache.

```python
from packaging.version import Version
import transformers
from transformers import TrainingArguments

eval_key = "eval_strategy" if Version(transformers.__version__) >= Version("4.46.0") else "evaluation_strategy"

training_args = TrainingArguments(
    **{
        eval_key: "epoch",
        "output_dir": TRAINING_OUTPUT_DIR,
        "per_device_train_batch_size": 16,
        "per_device_eval_batch_size": 32,
        "num_train_epochs": 3,
        "save_strategy": "epoch",
        "load_best_model_at_end": True,
        "metric_for_best_model": "f1",
        "logging_steps": 100,
        "report_to": "mlflow",
        "ddp_find_unused_parameters": False,
    }
)
```

## MLflow and Model Logging

- Log foundation model, split sizes, learning rate, batch sizes, and epoch count.
- Save the tokenizer with the model.
- Log with `mlflow.transformers.log_model(...)` and register under Unity Catalog when the pipeline expects a registered model.
- Verify `Trainer` produced a non-null best model when `load_best_model_at_end=True`; fail before registration when it did not.

## Inference and Batch Scoring

- Load registered models through the model URI, not ad hoc local paths.
- For Spark batch scoring, load the pipeline once per worker inside the UDF.
- Return an explicit score extraction rule instead of assuming the top label is always the positive class.

## Runtime Drift

- If `TrainingArguments`, CUDA behavior, or package versions fail on a DBR image, switch to **databricks-runtime-doctor**.
- Keep runtime compatibility fixes separate from training-design edits.
