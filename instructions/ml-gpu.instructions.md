---
description: "Machine learning and GPU standards for explicit ML paths and training files"
applyTo: '**/ml/**/*.py,**/ml/**/*.ipynb,**/machine-learning/**/*.py,**/machine-learning/**/*.ipynb,**/training/**/*.py,**/training/**/*.ipynb,**/train*.py,**/train*.ipynb,**/*_train*.py,**/*_train*.ipynb,**/*-train*.py,**/*-train*.ipynb,**/*transformer*.py,**/*transformer*.ipynb,**/*gpu*.py,**/*gpu*.ipynb'
---

# Machine Learning and GPU Development Standards

## GPU Resource Management

### Task Separation

Training transformers and other GPU-intensive models requires GPU clusters, which are underpowered for general ETL tasks. **Separate work into GPU-required and CPU-only tasks:**

```python
# ✅ CORRECT - separate GPU and CPU workloads
# Task 1 (CPU cluster): Data preparation, feature engineering
def prepare_training_data(spark: SparkSession) -> DataFrame:
    """CPU-bound data preparation - runs on standard cluster."""
    df = spark.table(source_table).select("text", "label")
    return df.filter(F.col("text").isNotNull())

# Task 2 (GPU cluster): Model training only
def train_transformer_model(training_data: DataFrame) -> None:
    """GPU-bound training - requires GPU cluster."""
    verify_gpu_available()  # Fail fast if no GPU
    # ... training code ...
```

```yaml
# databricks.yml - separate job clusters by workload
job_clusters:
  - job_cluster_key: etl_cluster
    new_cluster:
      node_type_id: "i3.xlarge"  # CPU-optimized for ETL
      num_workers: 4
      
  - job_cluster_key: gpu_cluster
    new_cluster:
      node_type_id: "g4dn.xlarge"  # GPU for training
      num_workers: 1
```

### Strict GPU Enforcement

When a task requires GPU acceleration, **never silently fall back to CPU**. If a GPU is expected but unavailable, raise an exception immediately:

```python
import torch

def verify_gpu_available() -> None:
    """Verify GPU is available and active. Raises if not."""
    if not torch.cuda.is_available():
        raise RuntimeError(
            "GPU required but not available. "
            "Ensure this task runs on a GPU-enabled cluster."
        )
    
    device_count = torch.cuda.device_count()
    if device_count == 0:
        raise RuntimeError("No CUDA devices found despite CUDA being available")
    
    # Log GPU info for debugging
    for i in range(device_count):
        gpu_name = torch.cuda.get_device_name(i)
        logger.info(f"GPU {i}: {gpu_name}")


def train_model(model, data) -> None:
    """Train model on GPU - fails if GPU unavailable."""
    verify_gpu_available()
    
    device = torch.device("cuda")
    model = model.to(device)
    
    # Verify model is actually on GPU
    if not next(model.parameters()).is_cuda:
        raise RuntimeError("Model failed to move to GPU")
    
    # ... training loop ...
```

### GPU Verification Patterns

```python
# ✅ CORRECT - explicit GPU check before training
def run_gpu_training(config: dict) -> None:
    verify_gpu_available()
    
    # Set device explicitly
    device = torch.device("cuda")
    torch.cuda.set_device(0)
    
    # Verify tensors are on GPU during training
    sample_tensor = torch.zeros(1).to(device)
    assert sample_tensor.is_cuda, "Tensor not on GPU"
    
    train(config, device)

# ❌ WRONG - silent fallback to CPU
def run_training(config: dict) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # This silently uses CPU if GPU unavailable - BAD for GPU-required tasks
    train(config, device)
```

---

## Transformer Training

### Resource Planning

| Task Type | Cluster Type | Notes |
|-----------|--------------|-------|
| Data loading/preprocessing | CPU cluster | Standard ETL nodes |
| Tokenization (large scale) | CPU cluster | Memory-optimized preferred |
| Model training | GPU cluster | Single or multi-GPU |
| Inference (batch) | GPU cluster | Can use smaller GPUs |
| Result post-processing | CPU cluster | Standard nodes |

### Training Pipeline Structure

```python
# Pipeline orchestration separates GPU and non-GPU work
tasks:
  - task_key: prepare_data
    job_cluster_key: etl_cluster  # CPU
    
  - task_key: train_model
    job_cluster_key: gpu_cluster  # GPU
    depends_on:
      - task_key: prepare_data
      
  - task_key: evaluate_results
    job_cluster_key: etl_cluster  # CPU
    depends_on:
      - task_key: train_model
```

---

## Common GPU Issues

### Memory Management

```python
# Clear GPU memory between runs
torch.cuda.empty_cache()

# Use gradient checkpointing for large models
model.gradient_checkpointing_enable()

# Monitor memory usage
logger.info(f"GPU memory allocated: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
logger.info(f"GPU memory reserved: {torch.cuda.memory_reserved() / 1e9:.2f} GB")
```

### Driver OOM Prevention

The Spark driver is a single JVM process with limited memory. These patterns cause driver OOMs:

```python
# ❌ WRONG - materializes entire DataFrame on driver
full_df = spark.table("large_table").toPandas()

# ❌ WRONG - collects all rows to driver for logging
all_rows = df.collect()
logger.info(f"Sample: {all_rows[:5]}")

# ✅ CORRECT - bounded collection
sample = df.limit(5).toPandas()

# ✅ CORRECT - stage to Parquet on UC Volumes, then stream into training
vol_path = "/Volumes/<catalog>/<schema>/<volume>/training_data/"
df.write.mode("overwrite").parquet(vol_path)
# Then use HuggingFace datasets.load_dataset("parquet", data_files=..., streaming=True)
```

When DBFS root is disabled, use Unity Catalog Volumes for all temporary files:

```python
# ❌ WRONG - fails when DBFS root is disabled
df.write.parquet("/dbfs/tmp/staging")

# ✅ CORRECT - UC Volumes
df.write.parquet("/Volumes/<catalog>/<schema>/<volume>/staging/")
```

### Multi-GPU Training

```python
# Verify all expected GPUs are available
expected_gpus = int(os.environ.get("EXPECTED_GPU_COUNT", 1))
actual_gpus = torch.cuda.device_count()

if actual_gpus < expected_gpus:
    raise RuntimeError(
        f"Expected {expected_gpus} GPUs but found {actual_gpus}. "
        f"Check cluster configuration."
    )
```

---

## Mixed Precision Training

### When to Use

Mixed precision reduces memory usage and speeds up training on GPUs with Tensor Cores (Volta/V100 and newer). Use it for any model that fits in GPU memory at fp32 but would benefit from faster throughput.

### bf16 vs fp16

| Format | Range | Precision | Best For |
|--------|-------|-----------|----------|
| fp16 | Narrow (6e-8 to 65504) | Higher mantissa bits | Older GPUs (V100), inference |
| bf16 | Same as fp32 | Lower mantissa bits | Training (A100, H100) - fewer overflow issues |

Prefer **bf16** for training when hardware supports it (Ampere+). It matches fp32 dynamic range so loss scaling is unnecessary. Use **fp16** on V100 or for inference where precision matters more than range.

### PyTorch AMP

```python
from torch.cuda.amp import autocast, GradScaler

# fp16 with loss scaling (required for fp16 to avoid underflow)
scaler = GradScaler()

for batch in dataloader:
    optimizer.zero_grad()
    with autocast(dtype=torch.float16):
        loss = model(batch)
    scaler.scale(loss).backward()
    scaler.step(optimizer)
    scaler.update()

# bf16 - no scaler needed
for batch in dataloader:
    optimizer.zero_grad()
    with autocast(dtype=torch.bfloat16):
        loss = model(batch)
    loss.backward()
    optimizer.step()
```

### HuggingFace Trainer

```python
from transformers import TrainingArguments

args = TrainingArguments(
    bf16=True,              # Use bf16 on Ampere+ GPUs
    # fp16=True,            # Use fp16 on V100 or older
    bf16_full_eval=True,    # Also use bf16 during evaluation
    output_dir="./output",
)
```

---

## Distributed Training

### When to Scale Beyond One GPU

If your model does not fit in a single GPU's memory even with mixed precision and gradient checkpointing, use distributed training. For models that fit on one GPU, `DataParallel` or single-GPU training is simpler and preferred.

### DeepSpeed (recommended for large models)

DeepSpeed ZeRO partitions optimizer states, gradients, and parameters across GPUs:

```python
# deepspeed_config.json
{
    "zero_optimization": {
        "stage": 2  # Stage 1: optimizer states, Stage 2: + gradients, Stage 3: + parameters
    },
    "bf16": {"enabled": true},
    "train_batch_size": "auto"
}
```

```bash
# Launch with DeepSpeed
deepspeed --num_gpus=4 train.py --deepspeed deepspeed_config.json
```

### FSDP (PyTorch native)

Fully Sharded Data Parallel is PyTorch's built-in alternative to DeepSpeed ZeRO-3. Prefer FSDP when you want to avoid external dependencies:

```python
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP

model = FSDP(model, use_orig_params=True)
```

### Choosing Between Them

- **DeepSpeed ZeRO-2/3**: Better tooling, offloading to CPU/NVMe, more mature for very large models
- **FSDP**: No extra dependency, good PyTorch integration, sufficient for most multi-GPU jobs
- Both integrate with HuggingFace Trainer via config flags

---

## GPU Memory Estimation

### Quick Heuristic

Estimate GPU memory needed before selecting instance types:

| Component | fp32 | fp16/bf16 |
|-----------|------|-----------|
| Model parameters | params x 4 bytes | params x 2 bytes |
| Optimizer (Adam) | params x 8 bytes (momentum + variance) | params x 8 bytes (kept in fp32) |
| Gradients | params x 4 bytes | params x 2 bytes |
| Activations | Varies (batch size dependent) | ~half of fp32 |

**Example:** A 1B parameter model in fp32 training:
- Parameters: 1B x 4B = 4 GB
- Adam optimizer: 1B x 8B = 8 GB
- Gradients: 1B x 4B = 4 GB
- Total (before activations): ~16 GB - needs at least a 24 GB GPU (e.g., A10G)

**With bf16 mixed precision**, the same model drops to ~12 GB before activations, fitting more comfortably on a 24 GB GPU.

### Instance Type Reference

| Instance | GPU | VRAM | Use Case |
|----------|-----|------|----------|
| g4dn.xlarge | T4 | 16 GB | Inference, small model fine-tuning |
| g5.xlarge | A10G | 24 GB | Fine-tuning models up to ~1B params |
| g5.12xlarge | 4x A10G | 96 GB | Multi-GPU training, medium models |
| p4d.24xlarge | 8x A100 | 320 GB | Large model training (7B+ params) |
| p5.48xlarge | 8x H100 | 640 GB | Very large models, fastest training |
