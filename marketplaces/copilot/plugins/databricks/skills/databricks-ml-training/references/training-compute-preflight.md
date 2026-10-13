# Training Compute Preflight

Use this before Databricks training jobs that read Spark tables, use GPUs, or run as bundle jobs.

## Verify Actual Deployed Compute

Check the job cluster or all-purpose cluster that will actually run the training code after deployment. Do not rely only on local YAML or an outdated design note.

Record:

- DBR version and whether it is an ML runtime
- node type and GPU type
- worker count and driver type
- Spark mode and whether Spark table imports are required
- cluster policy constraints
- expected training mode: dev or full

If the job cluster differs from the bundle file, review the deployed job settings before diagnosing training code.

## GPU Runtime Rules

- GPU training should use a Databricks ML GPU runtime unless the project has a tested exception.
- A GPU node type without the ML GPU runtime is a compute mismatch, not a model-quality issue.
- Single-node GPU clusters can be valid for local-file training, but reject them for Spark table import unless data is already materialized locally or the cluster mode supports the required Spark commands.
- If more than one GPU is provisioned, require evidence that training uses multiple GPUs through DDP, Accelerate, Spark-distributed training, or framework-specific multi-GPU settings.
- Do not provision multi-GPU hardware for single-GPU code without documenting the cost and utilization tradeoff.

## Transformer Multi-GPU Checks

For HuggingFace training, verify:

- `torch.cuda.device_count()` in the running job
- `Trainer` or `Accelerate` sees the expected process count
- DDP settings match the cluster topology
- `ddp_find_unused_parameters` is intentional
- batch size, gradient accumulation, and evaluation cadence are safe for available memory

## Spark Data Import Checks

If the training path reads Unity Catalog or Spark tables:

- prove the cluster can execute Spark commands before training starts
- avoid single-node GPU modes that cannot import tables for the job
- if a cell hangs or warns that compute needs at least one worker to run Spark commands or import tables, classify the compute mode and worker count before changing Python training logic
- separate data preparation into a Spark-capable job when fine-tuning runs on single-node GPU hardware
- persist a dated extract cache and pass that cache path into the GPU training job

## Preflight Failure Rule

Fail before expensive training if compute is incompatible with the data path, model family, or distributed-training assumptions.