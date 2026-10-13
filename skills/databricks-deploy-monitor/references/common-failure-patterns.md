# Common Failure Patterns

Use this file after `jobs get-run-output` or task output exposes the first actionable error. Ignore downstream cancellations until the first failure is fixed.

## Bundle And Deploy Failures

### `validation failed`

- Check: invalid YAML shape, missing required fields, or broken resource references.
- Fix: correct the bundle config, validate, and redeploy.

### `Failed to acquire deployment lock`

- Check: interrupted or crashed deploy left a stale lock.
- Fix: wait briefly, then use `databricks bundle deploy --force-lock --profile <profile>` only if the lock is actually stale.

### `Resource already exists`

- Check: conflicting bundle identity, existing unmanaged resource, or stale state.
- Fix: verify bundle identity and binding before forcing a redeploy.

### Generated bundle validates locally but fails DAB validation

- Check: generated YAML shape, resource nesting, unsupported fields, target overrides, and workspace paths.
- Fix: correct the owned bundle config. If the project owns a template for that config, update the template so regenerated bundles stay valid.
- Then rerun static validation and `databricks bundle validate`.

## Import And Dependency Failures

### `ModuleNotFoundError` or `ImportError`

- Check: task libraries, wheel build, environment spec, and import path.
- Fix: add the dependency to task libraries or the environment spec, then redeploy.

### `attempted relative import with no known parent package`

- Check: file is being executed as a script instead of a package entrypoint.
- Fix: switch to absolute imports or package-aware execution.

## Data And Schema Failures

### `DELTA_FAILED_TO_MERGE_FIELDS`

- Check: source versus target column types, especially numeric width and nested fields.
- Fix: cast before the write. Use `mergeSchema` only for additive compatible columns.

### `Column ... does not exist` or unresolved column

- Check: actual schema or `df.columns` versus the code path.
- Fix: correct the reference or add schema validation before the write.

### `NOT NULL` violation

- Check: null counts and upstream assumptions.
- Fix: filter invalid rows, fill with an explicit default, or intentionally relax the contract.

## Cluster And Resource Failures

### `OutOfMemoryError`, driver OOM, or executor killed for memory

- Check: `collect()`, `toPandas()`, shuffle width, repartitioning, and cluster size.
- Fix: remove driver-side collection, repartition deliberately, and only then scale the cluster.

### `Cluster failed to start` or `DRIVER_UNAVAILABLE`

- Check: spot capacity, instance type, and whether the failure looks platform-side.
- Fix: retry once if it looks transient. Otherwise change capacity or escalate.

### Capacity And Shared Resource Contention

- Check: cloud capacity, cluster events, workspace limits, shared node pools, and whether other jobs are consuming resources.
- Classify capacity failures as platform-side unless logs show a deterministic code failure.
- Retry once only when the platform signal is transient and the user permits reruns.
- Otherwise preserve the failed date or window, split large windows, create a one-off repair job, or record the date as blocked in the run ledger.
- Do not change scoring, training, ETL, or bundle logic to "fix" capacity unless logs prove code caused the resource failure.

## Auth And Permission Failures

### `PERMISSION_DENIED`

- Escalate. This is not a code fix.

### `UNAUTHENTICATED`, expired token, or invalid profile auth

- Escalate. Credential repair requires user or admin action.

## Python And Runtime Failures

### bare Python command stalls, is killed, or leaves stale logs

- Check: process list, exit code, log modification time, pending input prompts, and whether output redirected to another file.
- Fix: make the command non-interactive, add deterministic logging, reduce the workload for dev mode, or resume from the last completed artifact.
- Then continue the validate/deploy/run loop instead of reporting only that the command stalled.

### `TypeError: cannot pickle` or `PicklingError`

- Check: UDF closure for sessions, loggers, clients, or other non-serializable objects.
- Fix: move them out of the closure or create them inside worker code when necessary.

### timezone-aware versus naive datetime failure

- Check: datetime boundaries and Spark session timezone.
- Fix: standardize on UTC and convert at the boundary.

## Fix Order

1. Fix bundle validation and deployment errors before touching job code.
2. Fix the first failed task before investigating downstream task failures.
3. Fix code and data-contract problems before resizing the cluster.
4. Treat permissions and credentials as escalations, not code work.
5. Treat read-only source freshness and output contract failures as verification findings. Switch to the owning sibling skill for code changes, then return here for deploy and verification.
