# Cluster Runtime Diagnostics

Use this before rewriting Spark code when a Databricks run hangs, reports `DRIVER_NOT_RESPONDING`, or emits a compute warning.

## Runtime Contract Checklist

Collect these facts first:

- job run error text and cluster event messages
- task type and whether it imports tables or runs Spark commands
- task cluster mapping: existing cluster, job cluster key, or serverless
- deployed job JSON and UI-visible cluster spec
- DAB or bundle YAML that was intended to deploy the task
- Spark runtime version and ML runtime version
- cluster policy and access mode
- `num_workers`, autoscale min/max workers, driver node type, worker node type, and single-node flags
- GPU node type and whether the training code uses multi-GPU execution

Resolve compute-contract failures before changing Spark query code.

## Worker Count Hazards

### Bad: Spark workload on an incompatible single-node shape

```yaml
new_cluster:
  spark_version: 15.4.x-gpu-ml-scala2.12
  node_type_id: g5.4xlarge
  num_workers: 0
```

If Databricks warns that compute in this mode needs at least one worker to run Spark commands or import tables, this is a runtime-contract problem. Fix the cluster topology first.

### Better: Explicit worker topology for Spark work

```yaml
new_cluster:
  spark_version: 15.4.x-gpu-ml-scala2.12
  node_type_id: g5.12xlarge
  num_workers: 1
```

After adding workers, check whether the workload now has multiple GPUs available. If training runs on driver plus worker GPUs, verify DDP, Torch Trainer, HuggingFace Trainer, or another multi-GPU strategy is actually enabled. Do not pay for multi-node GPU compute while running single-GPU training by accident.

## Job Cluster Mismatch

Do not assume the deployed job uses the YAML you just edited.

Verify all three surfaces after cluster changes:

1. Bundle YAML or DAB config.
2. Deployed job JSON from Databricks Jobs API.
3. UI-visible task cluster spec.

Flag mismatches such as:

- intended `g5.12xlarge`, deployed/UI still shows `g5.4xlarge`
- one training task updated, another backfill task still references the old job cluster key
- job cluster has workers, but task uses an existing single-node cluster
- serverless target still runs code that requires unsupported APIs

## DRIVER_NOT_RESPONDING Triage

`DRIVER_NOT_RESPONDING` can come from memory pressure, but do not assume that first. Check:

- cluster events for worker unavailable, policy denial, image pull, init script, or Spark startup failure
- worker count and access mode compatibility with Spark commands
- job cluster key assigned to the failed task
- driver and worker node memory, disk, and GPU topology
- source table imports and first Spark action in logs
- whether the run reached user code or hung during Spark/session/table setup

Only move to code-level driver memory fixes after the runtime contract is plausible.

## Static Auditor Signals

The auditor reports `num_workers: 0` and GPU node types as configuration facts.
It increases severity only when YAML establishes Spark-task or multi-worker
context. Treat every report as a prompt for the checklist above, not proof that
the cluster is wrong or that source size proves broadcast payload size.
