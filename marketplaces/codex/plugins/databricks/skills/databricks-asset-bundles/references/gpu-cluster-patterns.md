# GPU Cluster Patterns

Use this when a Databricks bundle defines GPU classic compute, ML runtimes, or training jobs.

## GPU Cluster Contract

- Use classic compute for GPU jobs; serverless is the default only for non-GPU work.
- Keep `spark_version`, `node_type_id`, `driver_node_type_id`, and `num_workers` explicit.
- Match `driver_node_type_id` to `node_type_id` unless the project intentionally needs a different driver shape.
- Verify live job settings after deploy because UI-visible cluster shape can drift from intended YAML.
- Keep dev and full training clusters separate when cost, data window, or tuning budget differs.

## Single-Node Rule

Single-node GPU is acceptable only when the task does not run Spark table commands, import Delta tables, or require executor workers.

Bad pattern:

```yaml
new_cluster:
  spark_version: "17.3.x-gpu-ml-scala2.12"
  node_type_id: "g5.4xlarge"
  num_workers: 0
tasks:
  - task_key: train_from_delta
    notebook_task:
      notebook_path: ./notebooks/train.ipynb
```

Good pattern:

```yaml
new_cluster:
  spark_version: "17.3.x-gpu-ml-scala2.12"
  node_type_id: "g5.4xlarge"
  driver_node_type_id: "g5.4xlarge"
  num_workers: 1
```

Recognize this Databricks signal as compute topology, not data-loading code:

```text
compute in this mode needs at least 1 worker to run Spark commands or import tables
```

## Multi-Node GPU Rule

If a GPU cluster has multiple workers, the training code must use multiple GPUs intentionally, such as DDP, Torch distributed, Horovod, or a framework-supported distributed strategy.

Do not scale to multi-node GPU only to work around a bundle issue. First verify:

- the task actually uses all GPUs
- the runtime and package pins support distributed training
- `ddp_find_unused_parameters` or equivalent settings are correct for the model
- data loading is partitioned and not driver-bound

When the runtime failure is package or CUDA-specific, use `databricks-runtime-doctor` for the code/runtime fix, then return here for bundle validation and live settings checks.
