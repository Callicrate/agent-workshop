# Job And Pipeline Configuration

Use this file when the task is wiring jobs, task types, compute, schedules, or DLT pipeline resources.

## Lookup for Named Compute

When the user specifies a SQL warehouse, cluster, query, or dashboard by name, use a bundle `lookup` expression instead of hardcoding IDs.

| Resource Type | Lookup Pattern |
|--------------|----------------|
| SQL warehouse | `${lookup.sql_warehouses."Warehouse Name".id}` |
| Cluster | `${lookup.clusters."Cluster Name".cluster_id}` |
| Query | `${lookup.queries."Query Name".id}` |
| Dashboard | `${lookup.dashboards."Dashboard Name".id}` |

## Job Defaults

- Apply `tags` to every job.
- Prefer serverless environments for non-GPU tasks.
- Start scheduled or continuous jobs paused until the bundle and task logic are validated.
- During development, keep `max_retries: 0` so failures surface immediately.
- Expose only the minimal operator-facing parameter surface. Decide the manual-run contract first (e.g., `end_date`, `lookback_hours`), wire those through `job.parameters`, and keep stable tables, modes (like dry-run), and resource wiring internal to the bundle/code.
- For topology changes, also read [job-topology-changes.md](job-topology-changes.md).
- For GPU clusters, also read [gpu-cluster-patterns.md](gpu-cluster-patterns.md).

## Common Task Shapes

### Notebook task

```yaml
notebook_task:
  notebook_path: ./notebooks/my_notebook.ipynb
  base_parameters:
    catalog: "${var.catalog}"
```

Notebook tasks inherit the bundle contract. When editing the notebook, also verify:

- the bundle path points at the edited notebook
- the notebook path ends in `.ipynb`; use `spark_python_task` or `python_wheel_task` for `.py` entrypoints
- task parameters match the notebook's expected widgets or arguments
- the runtime and dependencies support the notebook code
- validation and deployment were rerun if deployment verification is in scope

### Spark Python task

```yaml
spark_python_task:
  python_file: ./src/main.py
  parameters:
    - "--date"
    - "{{job.parameters.date}}"
```

### Python wheel task

```yaml
python_wheel_task:
  package_name: my_package
  entry_point: main
  parameters:
    - "--config"
    - "/dbfs/config.yaml"
```

### SQL task

```yaml
sql_task:
  warehouse_id: ${lookup.sql_warehouses."Analytics Warehouse".id}
  query:
    query_id: ${lookup.queries."Daily Report".id}
```

### Pipeline task

```yaml
pipeline_task:
  pipeline_id: ${resources.pipelines.daily_ingest.id}
```

## Job-Metadata Defaults for Scheduled vs. Manual Runs

When a parameter must be mandatory for manual backfills but auto-populated for scheduled runs, set the `default` to a Databricks job-metadata template. The scheduler expands the template at trigger time; manual "Run Now" callers can override it with an explicit value.

| Template | Expands To | Use Case |
|----------|-----------|----------|
| `{{job.start_time.iso_date}}` | `YYYY-MM-DD` of the run's logical start | Date-partitioned ETL |
| `{{job.start_time.iso_datetime}}` | Full ISO-8601 timestamp | Timestamp-based processing |

```yaml
parameters:
  - name: end_date
    default: "{{job.start_time.iso_date}}"
  - name: lookback_hours
    default: "24"
```

With this setup:

- **Scheduled runs** auto-populate `end_date` from the job start time - no operator input needed.
- **Manual runs** still accept an explicit `end_date` override for backfills.
- Parameters like `lookback_hours` that are rarely changed get a sensible numeric default.

Keep source tables, output tables, and mode flags (e.g., dry-run) out of `job.parameters` entirely - those belong in bundle variables or code constants.

## Complete Job Example

```yaml
resources:
  jobs:
    daily_etl:
      name: "${var.job_basename} Daily ETL (${bundle.target})"
      tags: ${var.tags}
      parameters:
        - name: date
          default: "{{job.start_time.iso_date}}"
      environments:
        - environment_key: default
          spec:
            client: "1"
            environment_version: "${var.serverless_environment_version}"
            dependencies:
              - pydantic==2.11.3
              - tenacity==9.1.2
      tasks:
        - task_key: extract
          notebook_task:
            notebook_path: ./notebooks/01_extract.ipynb
          environment_key: default
          max_retries: 0
        - task_key: transform
          depends_on:
            - task_key: extract
          spark_python_task:
            python_file: ./src/transform.py
            parameters:
              - "--date"
              - "{{job.parameters.date}}"
          environment_key: default
          max_retries: 0
      schedule:
        quartz_cron_expression: "0 0 6 * * ?"
        timezone_id: "UTC"
        pause_status: PAUSED
```

## Cluster Patterns

- Use `new_cluster` only when the job needs classic compute, usually for GPU or runtime-specific workloads.
- Keep `spark_version` explicit.
- Verify the current target DBR/Python/Spark/MLflow compatibility before selecting or changing `spark_version`.
- Reuse a `job_cluster_key` within one job when multiple tasks share the same classic cluster.
- Separate dev and full training cluster assumptions. Dev jobs should use cheaper/smaller compute and reduced workload parameters; full jobs should use the approved production-sized compute.
- For serverless `environment_version`, follow the existing project or current workspace-supported value; a template example is not proof of support in the target workspace.

## Schedule Audit Checklist

When changing schedules or answering whether scheduled jobs are healthy, check:

- intended cadence and timezone
- `pause_status` in the bundle and live job settings
- last successful run and last terminal failure
- output table coverage for the expected date or partition window
- model age or table freshness for scheduled ML jobs
- whether downstream consumers observed the new output

Do not report a schedule as healthy from the bundle alone. Compare live run history and output freshness.

## Continuous Job Migration

When replacing a scheduled batch job with an always-on job:

1. remove or pause the old schedule in the intended target
2. define the continuous loop, trigger, or streaming contract explicitly
3. keep the task entrypoint and dependencies in the bundle, not in notebook state
4. deploy with the proven profile/target command shape
5. verify the live job is active and the old scheduled job is paused or removed

## Live Settings Check After Deploy

After deployment changes, fetch every touched job and compare bundle intent with live state:

```powershell
databricks jobs get <job_id> --profile <profile> -o json
```

Check task paths, parameters, node type, `driver_node_type_id`, `num_workers`, runtime, schedule, pause status, libraries, environments, and permissions. If the UI or API shows a different cluster shape than the bundle intended, keep the work open until the drift is explained or fixed.

## DLT Pipeline Resource

```yaml
resources:
  pipelines:
    daily_ingest:
      name: "${var.job_basename} DLT (${bundle.target})"
      target: "${var.catalog}.${var.schema}"
      development: true
      photon: true
      libraries:
        - notebook:
            path: ./notebooks/dlt_pipeline.ipynb
```

- Use `development: true` during bring-up and switch to `false` for the production target override.
- Keep the pipeline notebook path relative to the bundle root.
- Use a job `pipeline_task` when the pipeline needs orchestration with other tasks.
