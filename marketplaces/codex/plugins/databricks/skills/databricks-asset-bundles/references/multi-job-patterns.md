# Multi-Job Bundle Patterns

Guidance for bundles containing multiple jobs.

For job removal, target-only resources, one-off repairs, backfill splitting, or parallelism changes, also read [job-topology-changes.md](job-topology-changes.md).

## Job Naming Conventions

Use a consistent naming pattern with the bundle target and a descriptive suffix:

```yaml
resources:
  jobs:
    ingest_job:
      name: "${var.job_basename} Ingest - ${bundle.target}"
      tags: ${var.tags}

    transform_job:
      name: "${var.job_basename} Transform - ${bundle.target}"
      tags: ${var.tags}

    export_job:
      name: "${var.job_basename} Export - ${bundle.target}"
      tags: ${var.tags}
```

- Prefix all job names with `${var.job_basename}` for consistency
- Suffix with `${bundle.target}` to distinguish dev from prod
- Use a short, descriptive middle segment (e.g., "Ingest", "Transform", "Export")

## Shared Job Clusters

Define clusters once and reference them across multiple jobs to reduce cost and config duplication:

```yaml
resources:
  jobs:
    ingest_job:
      job_clusters:
        - job_cluster_key: shared_etl
          new_cluster:
            spark_version: "17.3.x-scala2.12"
            node_type_id: "i3.xlarge"
            num_workers: 4
            custom_tags: ${var.tags}
            data_security_mode: SINGLE_USER

      tasks:
        - task_key: ingest
          job_cluster_key: shared_etl
          notebook_task:
            notebook_path: ./notebooks/ingest.ipynb

        - task_key: validate
          depends_on:
            - task_key: ingest
          job_cluster_key: shared_etl
          notebook_task:
            notebook_path: ./notebooks/validate.ipynb
```

> **NOTE:** `job_clusters` are scoped to each job — they cannot be shared *across* jobs. For cross-job cluster reuse, extract the cluster config into a `variables` block and reference it in each job.

### Cross-Job Cluster Variable

```yaml
variables:
  etl_cluster_config:
    type: complex
    default:
      spark_version: "17.3.x-scala2.12"
      node_type_id: "i3.xlarge"
      num_workers: 4
      custom_tags: ${var.tags}
      data_security_mode: SINGLE_USER
```

## Inter-Job Triggering

Databricks DABs do not natively support cross-job dependencies. Use these patterns instead:

### Option 1: Combine into a single multi-task job

If jobs always run together, merge them into one job with task dependencies:

```yaml
resources:
  jobs:
    full_pipeline:
      tasks:
        - task_key: ingest
          notebook_task:
            notebook_path: ./notebooks/ingest.ipynb

        - task_key: transform
          depends_on:
            - task_key: ingest
          notebook_task:
            notebook_path: ./notebooks/transform.ipynb

        - task_key: export
          depends_on:
            - task_key: transform
          notebook_task:
            notebook_path: ./notebooks/export.ipynb
```

### Option 2: Use `run_job_task` to chain jobs

When jobs must remain separate (different schedules, different owners):

```yaml
resources:
  jobs:
    orchestrator:
      tasks:
        - task_key: run_ingest
          run_job_task:
            job_id: ${lookup.jobs."Ingest Job".id}

        - task_key: run_transform
          depends_on:
            - task_key: run_ingest
          run_job_task:
            job_id: ${lookup.jobs."Transform Job".id}
```

### Option 3: Event-driven via webhook notifications

For loosely coupled jobs, use webhook notifications on job completion to trigger downstream workflows.

## When to Split vs Combine

| Scenario | Recommendation |
|----------|---------------|
| Tasks always run together | Single multi-task job |
| Different schedules needed | Separate jobs, chain with `run_job_task` |
| Different owners/permissions | Separate jobs |
| Independent failure handling | Separate jobs |
| > 20 tasks in one job | Split into multiple jobs |
