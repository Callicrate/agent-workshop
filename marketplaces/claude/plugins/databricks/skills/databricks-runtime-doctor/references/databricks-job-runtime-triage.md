# Databricks Job Runtime Triage

Use this when a Databricks job or task fails from platform state, cluster health, capacity, or task-isolation issues.

## Classification Ladder

Classify before touching code or dependency pins:

1. run state and task result state
2. first fatal task log line
3. cluster events and driver health
4. capacity or cloud-provider errors
5. DBR version, runtime flavor, and serverless versus job cluster mode
6. installed libraries and package conflicts
7. deterministic code exception

Infrastructure failures such as `DRIVER_NOT_RESPONDING` or compute capacity shortages are not dependency failures until logs prove a software exception caused them.

## Required Evidence

Collect or reconstruct:

- workspace profile and host
- job ID, run ID, task key, task attempt, and cluster ID when available
- cluster mode: serverless, job cluster, all-purpose cluster, or GPU cluster
- DBR version and whether it is ML runtime
- cluster events around the failure window
- task stderr, driver logs, and first fatal exception
- installed libraries or environment spec
- whether the task failed alone or after an upstream task failure

Use [fresh-runtime-evidence.md](fresh-runtime-evidence.md) when evidence is live, from-spec, or inferred.

## Capacity And Driver Failures

For `DRIVER_NOT_RESPONDING`, driver lost, or capacity messages:

- inspect cluster events before changing training code
- check whether retries used the same node type or availability zone
- reduce task size only when logs show memory pressure or driver overload
- use a different node family or serverless only when the failure is capacity or policy related
- preserve the original runtime parameters for any repair run

## One-Task Recovery

When only one expensive task failed:

1. preserve original job parameters, date, category, model name, and data window
2. isolate the failed task in a one-off rerun job or repair run
3. avoid rerunning successful expensive tasks unless downstream dependencies require it
4. keep the one-off job dry-run or paused by default unless the user asks to run it
5. validate the produced artifact, MLflow run, or promotion row before closing the incident

## Runtime And Bundle Compute Conventions

- Non-GPU jobs should prefer serverless when the project bundle supports it.
- GPU or transformer jobs need explicit job cluster shape and ML runtime evidence.
- DBR `17.3.x-scala2.13` or the ML equivalent is a project convention for current job clusters, not proof of the live runtime.
- Broad `17.x ML` guidance is a hypothesis until the failing run snapshot confirms exact DBR, Python, and package versions.

## Spark/Table Access Hangs

When a notebook cell appears to hang while importing tables, reading Unity Catalog data, or starting Spark work, classify compute before changing Python dataflow:

- cluster mode and whether Spark commands are supported
- worker count, especially warnings that the mode needs at least one worker
- job task compute versus attached interactive cluster
- whether the table read works in a tiny sanity check
- driver and task logs around the apparent hang

Treat messages such as `compute in this mode needs at least 1 worker to run Spark commands or import tables` as platform/compute blockers, not model or Python logic bugs.

If the issue is bundle compute policy rather than runtime behavior, hand off to `databricks-asset-bundles` and return when the job spec is corrected or live runtime evidence is available.