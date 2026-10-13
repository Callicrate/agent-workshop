# Runtime Performance Diagnostics

Use this for an unexplained Spark slowdown after runtime/compute errors have been classified.
The static auditor identifies source hazards; it cannot measure live skew, spill, or executor contention.
A few unfinished tasks are a symptom, not a root-cause diagnosis.

## Capture the critical stage

If no relevant stage is visible, identify the current lifecycle phase from run/driver logs before applying stage-level tuning.
Identify the longest stage on the job's critical path, then record its task count and input/output and shuffle read/write volumes.
Link it to the corresponding SQL physical operator rather than assuming the slow line of Python is the expensive operation.
Databricks documents this starting point in [diagnosing a long Spark job](https://docs.databricks.com/aws/en/optimizations/spark-ui-guide/long-spark-stage).

For that stage, capture the metrics needed to distinguish the leading explanations:

- Task-duration distribution, including maximum and 75th percentile; compare slow tasks' input and shuffle volumes.
- Memory/disk spill, peak execution memory, GC time, scheduler delay, shuffle fetch wait, and executor loss where available.
- Operator rows, scan/metadata time, shuffle size, and spill from the SQL plan.

Metric names and availability depend on the runtime/UI version.
Treat explicit zero and documented omission-as-zero as zero: the current [Databricks stage-page behavior][dbx-skew-spill] states that no displayed spill statistics means no spill.
A metric the runtime/UI did not emit, retain, or expose remains unknown.
Use existing UI/operator metadata before adding a full scan solely to obtain diagnostic counts.
The [Spark Web UI reference](https://spark.apache.org/docs/3.5.6/web-ui.html#stage-detail) explains stage metrics and their connection to the SQL tab.

## Match evidence to a hypothesis

These are leads that can coexist, not automatic diagnoses.

| Observed evidence | Leading explanation | Smallest distinguishing check |
|---|---|---|
| A few tasks take much longer than p75 and process disproportionate input/shuffle | Skew or another localized straggler cause | Compare partition work and slow-task executor/log evidence; elapsed duration alone is insufficient |
| Substantial spill around a join, sort, or aggregate | Execution-memory or shuffle pressure | Identify the operator and intermediate data size; compare partition workload before choosing a remedy |
| Many small-input tasks plus high scan/metadata time | File/metadata overhead or ineffective pruning | Check scan operator, pruning, and available file count/size distribution |
| The stage has one or very few total tasks carrying large work | Serial operator or insufficient parallelism | Inspect whether the operator requires serial work and compare stage widths |
| High scheduler delay, executor loss, GC, or fetch wait | Scheduling/executor/network pressure | Correlate task timing with executor logs and concurrent workload; distinguish these signals |
| Driver restart/memory pressure around collection | Driver hazard | Inspect driver logs and collection/result size; executor tuning does not establish a fix |
| Runtime metrics are absent | Insufficient evidence | Obtain the relevant stage/operator evidence or keep competing explanations explicit |

Databricks describes maximum duration substantially above p75 as a skew lead and separates it from spill in [skew and spill][dbx-skew-spill].
A ratio is a triage signal; partition work and executor evidence distinguish skew from other stragglers.

Bad: three tasks remain, so prescribe salting, `OPTIMIZE`, or `coalesce(1)`.
Better: identify the slow stage, compare task duration/work and spill, then select the matching hypothesis.

## Compare one bounded change

Tie a proposed performance change to the observed cause.
Avoid choosing caching, repartitioning, salting, file rewrites, cluster upsizing, or AQE settings from completion counts or guesses.
Preserve an already established cause or a user's specifically authorized operation; this procedure addresses uncertainty rather than imposing another approval step.

Compare one reversible change on the same source snapshot/window and runtime assumptions.
Record before/after stage evidence, result row/key integrity, and the observation that would falsify the hypothesis.
Keep source exhaustion, temporal filters, and downstream table contracts fixed during the comparison.
If metrics do not support a cause, report the missing evidence and next bounded check instead of claiming a performance fix.

## Diagnostic output

Report the observed stage/operator, relevant metrics, supported hypothesis, plausible competing explanation, and the proposed bounded comparison.
Separate observed improvement from an unexecuted recommendation.
Live reruns, production cancellation, table maintenance, and compute changes remain within the authority boundary in [shared diagnostic guidance](shared-skill-patterns.md#safety).

[dbx-skew-spill]: https://docs.databricks.com/gcp/en/optimizations/spark-ui-guide/long-spark-stage-page
