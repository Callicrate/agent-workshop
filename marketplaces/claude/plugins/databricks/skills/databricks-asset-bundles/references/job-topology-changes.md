# Job Topology Changes

Use this when changing resource graph shape, target scoping, job splits, task parallelism, or one-off repair jobs.

## Read Current Shape First

Read the current `databricks.yml` and included `resources/*.yml` files before inventing a new job shape. Preserve existing task parameters, libraries, compute, permissions, and source windows unless the task explicitly changes them.

## Topology Checklist

Before editing jobs or tasks, decide:

- which jobs are root resources versus target-only resources
- which jobs are dev-only, prod-only, one-off, or repair-only
- which tasks are independent and can run in parallel
- which `depends_on` edges are required by dataflow, not habit
- which backfills should split by category, date, or early/late windows
- whether any job removal leaves dangling YAML anchors, aliases, or references

Avoid YAML anchors in DAB configs unless the project intentionally maintains them. Prefer variables, `job_clusters`, reusable environment blocks, or explicit duplication for small blocks.

## Moving Jobs Between Targets

- Dev-only or repair-only jobs should live under `targets.dev.resources` or a documented include pattern that only dev loads.
- Production jobs should not inherit experimental dev jobs through root `resources.jobs`.
- Removing root jobs requires checking target overrides, scheduled jobs, permissions, and live resources after deploy.

## One-Off Task Rerun Jobs

For a failed single task in a larger job:

1. copy the failed task entrypoint and parameters
2. preserve libraries, environment, job cluster or task cluster, permissions, and `run_as`
3. isolate the date, category, or source window
4. name the job as repair-only, for example `repair_train_phish_2026_03_04`
5. validate the bundle but do not run it when the user says `don't run it` or `make it so I can run it`

Use `databricks-deploy-monitor` for live repair-run monitoring when the user asks to run or watch it.

## Parallel Category Pipelines

When category tasks are independent, avoid serial `depends_on` edges between categories. Keep dependencies inside each category chain, for example prepare, train, score, promote, but let separate category chains run in parallel when dataflow permits it.

## Capacity Recovery Pattern

If capacity blocks a normal backfill date, do not churn code to hide a platform failure. Preserve the blocked date and create explicit smaller windows or one-off repair jobs, such as early and late windows, so unaffected dates can proceed.