# HuggingFace TrainingArguments Compatibility

Quick reference for `TrainingArguments` fields that change across HF `transformers` versions.
Use this file for constructor compatibility only; general training design belongs in the training skill.

## Field Renames (transformers 4.46+)

| Old Name (< 4.46) | New Name (>= 4.46) | Type | Default |
|-------------------|--------------------|------|---------|
| `evaluation_strategy` | `eval_strategy` | str | "no" |

## Safe TrainingArguments Template

This template keeps the compatibility logic in one place:

```python
import transformers
from packaging.version import Version

_HF_VER = Version(transformers.__version__)

def build_training_args(
    output_dir: str,
    train_batch_size: int = 16,
    eval_batch_size: int = 32,
    epochs: int = 3,
    use_bf16: bool = True,
) -> "transformers.TrainingArguments":
    """Build TrainingArguments compatible with the installed HF version."""
    eval_key = "eval_strategy" if _HF_VER >= Version("4.46.0") else "evaluation_strategy"

    args = {
        "output_dir": output_dir,
        "per_device_train_batch_size": train_batch_size,
        "per_device_eval_batch_size": eval_batch_size,
        "num_train_epochs": epochs,
        eval_key: "epoch",
        "save_strategy": "epoch",
        "logging_steps": 100,
        "ddp_find_unused_parameters": False,
    }

    # Use bf16 only on Ampere+ GPUs. Otherwise fall back to fp16.
    if use_bf16:
        args["bf16"] = True
    else:
        args["fp16"] = True

    return transformers.TrainingArguments(**args)
```

## Deprecated / Removed Fields

| Field | Deprecated In | Removed In | Replacement |
|-------|---------------|------------|-------------|
| `evaluation_strategy` | 4.46.0 | 4.50.0 (planned) | `eval_strategy` |
| `logging_first_step` | 4.47.0 | 4.48.0 | (removed, no replacement) |
| `push_to_hub_model_id` | 4.40.0 | 4.46.0 | Use `hub_model_id` |
| `adafactor` | 4.30.0 | 4.40.0 | Use `optim="adafactor"` |
