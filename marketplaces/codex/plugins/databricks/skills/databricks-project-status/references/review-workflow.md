# Databricks Project Status Review Workflow

## Contents

- [Review Contract](#review-contract)
- [Target Manifest](#target-manifest)
- [Evidence Hierarchy](#evidence-hierarchy)
- [Time Windows](#time-windows)
- [Health Dimensions](#health-dimensions)
- [Reliability Formulas](#reliability-formulas)
- [Error Analysis](#error-analysis)
- [Trends And Emerging Risks](#trends-and-emerging-risks)
- [Finding Quality](#finding-quality)
- [Recommendation Routing](#recommendation-routing)

## Review Contract

Record these before collecting metrics:

| Field | Required content |
|---|---|
| Project | Repository root, branch, commit, worktree state, environment, owning team |
| Workspace | Explicit CLI profile, host, principal, workspace ID when available |
| Time | Current window, equal baseline window, timezone, generation timestamp |
| Expected behavior | Schedules, SLAs/SLOs, freshness contracts, model thresholds, endpoint targets |
| Operation mode | `inspect-only` unless the user separately authorizes live changes |
| Exclusions | Resources, windows, maintenance periods, or inaccessible evidence |

Never include credentials or secret values in the report.

## Target Manifest

Start from repository configuration, then reconcile it with live workspace objects.
Do not assume every workspace resource with a similar name belongs to the project.

Inventory applicable resources:

- Databricks Jobs tasks, schedules, triggers, run-as identity, cluster policies, notifications, and recent runs.
- Lakeflow Declarative Pipelines, update history, event logs, data-quality expectations, and current state.
- Delta tables, materialized views, streaming tables, volumes, checkpoints, and downstream consumers.
- MLflow experiments, recent runs, registered models, versions, aliases, signatures, input examples, and metric contracts.
- Model Serving endpoints, served entities, routing, auto-capture or inference logs, events, request health, and downstream clients.
- SQL warehouses, dashboards, alerts, query workloads, and project-specific service principals.
- Monitoring tables, audit tables, run ledgers, alert destinations, and operational documentation.

For each object record its stable ID or three-part name, environment, owner, source-of-truth file, and inspection status.

### Coverage And Pagination

For every list, search, history, or event endpoint:

- record the endpoint or source, resource filter, requested window, page size, and pagination mechanism
- follow every documented page token, offset, or `has_more` continuation until exhausted
- deduplicate by the resource, run, update, event, query, or model-version stable ID
- record page count, raw record count, unique record count, earliest and latest observed timestamps, and collection timestamp
- compare the observed range with the requested current and baseline windows
- retry only inspect-only requests after throttling, honor `Retry-After` when present, and use bounded backoff

If permissions, retention, throttling, API limits, or time prevent complete retrieval, mark the source `partial` or `blocked` and name the affected conclusions.
Never infer zero failures, no usage, or healthy state from a partial first page.
In the report ledger, use `pages=<n>; raw=<n>; unique=<n>` and `requested=<range>; observed=<range>` for complete or partial evidence so coverage can be audited deterministically.
Use only `complete`, `partial`, `blocked`, or `not applicable` as coverage states, and explain the affected conclusions for every non-complete row.

## Evidence Hierarchy

Prefer evidence in this order:

1. Live read-only APIs, system tables, resource settings, run/task outputs, pipeline event logs, Delta metadata, and MLflow records.
2. Project-owned monitoring, audit, quality, inference, or run-ledger tables.
3. Bundle and source configuration at the reviewed commit.
4. Contract-bearing project documentation.
5. UI-only observations or prior narrative summaries, labeled as unverified leads.

Use `databricks-api-calls` for profile-safe list/get API inspection.
Before SQL, inspect warehouse state. Query only an already-running warehouse when the current task authorizes that read-only compute use; otherwise record the blocked metrics.
Do not invoke a serving endpoint merely to populate a status report.
Use these owning skills for interpretation or read-only helpers only at their decision points:

- `databricks-deploy-monitor` references for terminal-state and task-output interpretation, without deploy, run, watch, repair, cancel, or rerun actions.
- `databricks-spark-etl` for table freshness, schema, SCD2, and output-contract interpretation.
- `mlflow-run-auditor` for run metadata, metric provenance, registry drift, and reproducibility gaps.
- `databricks-model-serving` references for endpoint state, events, semantic readiness, inference logging, latency, errors, and drift. Do not run endpoint sample invocations or helpers with implicit profiles.
- `spark-diagnostics` for runtime, capacity, performance, or Spark anti-pattern evidence.

If an API, system table, warehouse, or permission is unavailable, record the exact missing evidence and affected conclusions.
Discover the system schemas and tables available in the target workspace rather than assuming a fixed catalog.
Keep system-table and query-history reads bounded by project identifiers and review windows; do not scan workspace-wide history when project attribution cannot be established.

## Time Windows

Default behavior:

- Current window: most recent 30 complete calendar days ending at the latest completed midnight in the schedule timezone.
- Baseline window: the immediately preceding equal-length 30-day window.
- Short-window check: most recent expected schedule interval or 24 hours for high-frequency workloads.

Adjust the default when:

- The user supplies a window.
- The workload runs less frequently than monthly. Include at least three expected opportunities when history exists.
- A deployment, data-source change, model version, or incident creates a more meaningful comparison boundary.
- Retention prevents an equal baseline. Report the actual available period and do not normalize it silently.

Use an explicit IANA schedule timezone, not the reviewer machine timezone, for window boundaries and expected-run calculations.
Treat approved pauses and maintenance as exclusions only when documented; report raw and adjusted values.
Keep the report's `Window basis` as `Latest complete days` when using the default window.
For a user-selected, deployment-bounded, incident-bounded, or retention-limited period, record a specific reason in `Window basis` so a historical window cannot be mistaken for current health.

## Health Dimensions

### Reliability And Uptime

For periodic jobs and pipelines collect:

- expected schedule opportunities
- actual starts and completed runs
- successful, failed, timed-out, canceled, skipped, and retried runs
- last attempted run, last successful run, and age since last success
- schedule delay, queue time, setup time, execution duration, and total duration
- mean, median, p95, maximum, and current versus baseline duration
- consecutive failures and observed recovery time
- task-level failure concentration and partial-output behavior

For continuously available endpoints or pipelines collect measured state history, request success, and outage intervals only when the source supports it.
Current `READY` or `RUNNING` state is not historical uptime.

Expected schedule slots are the unit of periodic reliability.
Map every scheduled run to one expected slot and allow each slot to contribute at most once to fulfillment or success.
Classify slots as missed, on-time success, late success, failed or timed out, canceled, or duplicate/rerun.
Report duplicate starts and manual reruns separately so they cannot inflate uptime.

### Triggered And Continuous Workloads

Choose reliability denominators by trigger type:

- **Periodic schedule:** use expected schedule slots as defined above.
- **Event or file trigger:** use eligible source events only when an auditable source-event ledger can be correlated to runs. Otherwise report run success, trigger latency, and output freshness while marking trigger fulfillment unknown.
- **Continuous pipeline:** use historical state intervals, restart or update failures, processing lag, and output freshness. Current `RUNNING` state alone is not availability evidence.
- **Manual or ad hoc workload:** do not invent expected opportunities. Report attempted-run outcomes, age since last success, output validity, and usage.

For triggered workloads, distinguish platform trigger delay from queue, setup, execution, and downstream publication latency.

### Lakeflow Pipelines

For each project-owned Lakeflow pipeline inspect, when available:

- current state and edition or mode
- update history with pagination and current-versus-baseline outcomes
- primary versus downstream-canceled failures
- event-log errors and warnings grouped by stable signature
- data-quality expectation totals, failures, drops, and quarantine behavior
- latest successful update and output-table freshness
- restart frequency, update duration, processing lag, and continuous-mode interruption intervals
- live settings versus bundle or repository source of truth

If event logs or expectation metrics are inaccessible, mark pipeline health incomplete rather than deriving it from current state.

### Tables And Data Products

Collect where applicable:

- latest successful write, latest source timestamp, freshness lag, and SLA
- row-count and storage growth by bounded period
- expected partitions or windows and missing coverage
- schema changes, null changes, duplicate keys, constraint or expectation failures
- Delta history, failed writes, concurrent operations, and maintenance history
- SCD2 current-row uniqueness, validity overlap, and point-in-time checks
- downstream read activity or consumer adoption when query history is accessible
- retention, checkpoint, small-file, skew, or growth risks supported by evidence

Do not scan an unbounded production table merely to create a status report.
Prefer metadata and bounded aggregates.

Map consumers from bounded query history, job or pipeline dependencies, serving or dashboard configuration, and repository references.
Record query-history retention and workspace/warehouse coverage.
When consumer evidence is unavailable, report adoption and blast radius as unknown rather than treating the table as unused.

### Models And MLflow

Collect where applicable:

- recent run status, experiment path, run age, and expected training cadence
- current and previous accepted model versions or aliases
- project-defined primary and guardrail metrics with formulas and thresholds
- current versus baseline metric values and sample sizes
- class-specific metrics, confusion matrix, AUC, calibration, threshold, and business KPI when relevant
- signature, input example, feature list, dataset/table lineage, point-in-time evidence, and source freshness
- registry drift, stale aliases, missing provenance, and promotion readiness
- input, prediction, or business-performance drift when monitoring data exists

Do not compare unrelated model metrics or claim degradation without matching data scope, split, threshold, and metric definition.

### Serving Endpoints

Collect where applicable:

- endpoint state, served model versions, routing, events, and recent configuration changes
- request volume, error rate, p50/p95/p99 latency, timeouts, throttling, and fallback rate
- schema violations, null or unscorable requests, and semantic sample results
- auto-capture or inference-log freshness and completeness
- feature and prediction drift, labeled performance, and retraining signals
- cold-start, concurrency, saturation, and scale-to-zero behavior when measured

### Usage, Performance, And Cost

Collect only when attributable evidence exists:

- job and task run counts, compute runtime, queue time, and retry overhead
- DBU or billed-usage totals by SKU, workspace, job, endpoint, or tag
- SQL query count, execution time, spill, scanned bytes, failures, and warehouse utilization
- serving request or token volume and cost where available
- table read/write activity and data growth
- current versus baseline absolute and percentage change

Separate usage growth from efficiency regression.
A cost increase can be healthy when throughput or adoption grows faster.

Attribute cost only when project ownership can be tied to stable job, pipeline, warehouse, endpoint, resource, or billing tags.
Separate job compute, pipeline compute, SQL warehouse, serving, storage, and other attributable categories when the available billing source supports them.
For shared compute, do not allocate cost by an arbitrary equal split; use query, runtime, request, or tag attribution and report the remaining unattributed share.
Compare total attributable cost and useful unit costs such as cost per successful schedule slot, processed record, query, or thousand requests only when the denominator is stable and meaningful.
If billing system tables or resource attribution are inaccessible, report cost as unknown and retain runtime, DBU, or usage proxies with their limitations.

### Security And Operational Readiness

Inspect:

- service-principal `run_as` for production jobs instead of personal identities
- owners and permissions for jobs, pipelines, tables, models, endpoints, and warehouses
- secret references without secret values
- schedule, notification, alert, and on-call coverage
- retries, timeouts, concurrency limits, recovery instructions, and idempotency
- runtime versions, policies, dependencies, deprecation notices, and proven upgrade risk
- source-of-truth drift between bundle configuration and live settings

Do not activate production or change permissions during a status review.

## Reliability Formulas

Use formulas only when their inputs are observable.

| Metric | Formula | Notes |
|---|---|---|
| Schedule fulfillment | expected slots with at least one start / expected schedule slots | A slot contributes at most once |
| Scheduled success | expected slots with a successful completion / expected schedule slots | Best periodic-job uptime analogue |
| On-time scheduled success | expected slots completed successfully within SLA / expected schedule slots | Separates late success from healthy delivery |
| Terminal success rate | successful terminal runs / all terminal runs | Does not measure missed schedules |
| Failure rate | failed or timed-out terminal runs / all terminal runs | Report canceled separately |
| Duplicate start rate | duplicate starts beyond the first / expected schedule slots | Report manual reruns separately when identifiable |
| Retry overhead | retry runtime / total runtime | Requires task attempt lineage |
| Freshness lag | review time - latest valid data timestamp | Compare with documented SLA |
| Recovery time | next successful completion - first failure in incident | Group contiguous related failures |
| Duration delta | current p95 - baseline p95 | Include sample counts |
| Relative change | (current - baseline) / abs(baseline) | Omit when baseline is zero or meaningless |

For a periodic job, never label `terminal success rate` alone as uptime.
Report schedule fulfillment, scheduled success, and on-time scheduled success together.

## Error Analysis

Group errors by normalized signature without discarding task or resource context.
Build the signature from resource ID, task key, platform error code or exception class, and the first actionable message after removing timestamps, run IDs, UUIDs, attempt numbers, and machine-specific paths.
Do not remove semantic identifiers such as table, model, column, or permission names.
Group contiguous runs with the same signature into one incident until the affected resource produces a verified successful output or the signature changes.
Treat downstream cancellations and skipped dependents as impact from the primary failure unless they have an independent root error.
For each error family report:

- affected resource and task
- first and last occurrence
- occurrence count and affected-run count
- current or resolved status
- triggering change or correlated event when proven
- output or downstream impact
- observed recovery or absence of recovery
- exact evidence reference

Distinguish primary failures from downstream cancellations and cleanup noise.
Do not count the same root failure once per child task unless each task independently failed.

## Trends And Emerging Risks

A current-versus-baseline comparison establishes a change, not a sustained trend.
A trend needs at least three comparable periods or a sufficiently granular time series with three or more comparable points and enough samples for the workload cadence.
Label sparse evidence `watch`, not a confirmed trend.

Look for evidence-backed precursors:

- run duration approaching or exceeding the schedule interval
- rising queue, setup, retry, failure, timeout, or cancellation rates
- growing freshness lag or missing windows
- row, storage, file-count, query-cost, or serving-volume growth without matching capacity
- worsening latency, fallback, schema violation, drift, or model metrics
- declining usage that may indicate a broken consumer or abandoned output
- runtime, dependency, policy, permission, or owner drift
- missing alerts, stale runbooks, personal production identities, or untested recovery paths
- live resource settings diverging from the project source of truth

Use project-defined SLAs, SLOs, alert thresholds, model contracts, and accepted baselines.
Do not inherit generic threshold examples from sibling skills as project status thresholds.
When no threshold exists, report raw values and deltas, mark threshold-based status unknown, and recommend defining the contract when material.
Do not predict an outage solely from a generic deprecation warning or one noisy sample.

## Finding Quality

Use these severities:

| Severity | Meaning |
|---|---|
| `critical` | Active production failure, unavailable required output, confirmed data integrity/security impact, or no viable recovery |
| `high` | Repeated failure, breached SLA, material metric degradation, or near-term risk with strong evidence |
| `medium` | Sustained degradation, operational gap, cost/performance concern, or incomplete control |
| `low` | Hygiene, maintainability, or optional optimization with limited current impact |

Use confidence separately:

- `high`: direct live evidence and a verified contract or repeated observation
- `medium`: direct evidence with incomplete scope or a plausible but unproven causal link
- `low`: indirect evidence, sparse samples, or missing baseline

Every finding must include:

- finding ID and concise title
- severity and confidence
- affected resource and owner
- observed evidence and window
- impact or likely consequence
- current, recurring, resolved, or emerging status
- concrete recommendation
- verification or acceptance check

Roll up the executive status deterministically:

- `critical` when any active critical finding exists, even if other dimensions are unknown
- `degraded` when an active high finding, breached required SLA, or confirmed material availability, integrity, or model-quality failure exists
- `watch` when no critical/high condition exists but medium findings or evidence-backed emerging risks require attention
- `healthy` only when all material discovered dimensions have sufficient evidence, required contracts are met, and no critical/high condition exists
- `unknown` when evidence gaps prevent distinguishing healthy from degraded and no known critical condition already determines the result

## Recommendation Routing

Route implementation by failure owner:

- bundle or live-resource definition drift: `databricks-asset-bundles`
- active run failure and authorized rerun loop: `databricks-deploy-monitor`
- API or SQL inspection helper: `databricks-api-calls`
- table, schema, freshness, SCD2, or write semantics: `databricks-spark-etl`
- Spark runtime, capacity, OOM, skew, or unsupported API: `spark-diagnostics`
- model training, metrics, or promotion flow: `databricks-ml-training`
- MLflow metadata, provenance, registry, or reproducibility: `mlflow-run-auditor`
- endpoint, inference, latency, error, fallback, or drift: `databricks-model-serving`
- batch scoring and prediction reconciliation: `databricks-batch-inference`

The report should make recommendations ready to scope, not execute them automatically.
