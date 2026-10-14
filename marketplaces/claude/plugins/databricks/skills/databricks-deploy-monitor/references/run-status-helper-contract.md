# Run Status Helper Contract

From the `databricks-deploy-monitor` skill package root, use the public Bash entrypoint:

```bash
bash scripts/check_run_status.sh <run_id> <profile>
```

Or provide both fields without positional arguments:

```bash
TOOL_INPUT='{"run_id":"12345","profile":"dev"}' bash scripts/check_run_status.sh
```

`run_id` must be a decimal signed-64-bit Databricks run ID from `1` through `9223372036854775807`. `profile` must be nonempty and explicit. `DEFAULT` is permitted only as an explicitly supplied profile name. Extra positional arguments, blank values, malformed `TOOL_INPUT`, and invalid values exit `2` before the Databricks CLI starts.

## Read-Only Command Boundary

The helper executes only this structured read command, adding `--page-token <token>` only after a previous response supplied a valid next token:

```bash
databricks jobs get-run <run_id> --profile <profile> --include-history -o json
```

It never deploys, starts, cancels, repairs, retries, or changes a job. It ignores returned configuration and `job_clusters`; it aggregates only run `tasks`, ForEach `iterations`, and the minimal repair-history ordering needed to identify the current root-task attempt.

Page tokens are server-provided but untrusted. They must match the compact opaque grammar `[A-Za-z0-9._~+/=-]{1,4096}`. Controls, whitespace, quoting, and command metacharacters such as `&`, `|`, `%`, `!`, `<`, `>`, and `^` fail closed before a second CLI process starts.

On Windows, the helper requires a direct `databricks.exe`. It refuses a resolved `.cmd` or `.bat` wrapper because Windows delegates those files through `cmd.exe`, which can reinterpret an otherwise argv-safe token. Install or expose the Databricks executable rather than a batch wrapper.

The current API documents `status.state`, `termination_details`, and paginated run arrays in the [Jobs Get Run API reference](https://docs.databricks.com/api/jobs/v2/get-run). The CLI documents `--include-history`, `--page-token`, and the rule that later pages can contain empty arrays when a different run array continues in the [Jobs CLI reference](https://docs.databricks.com/aws/en/dev-tools/cli/reference/jobs-commands). Jobs API 2.2 describes page tokens for run array fields in its [2.2 migration guide](https://docs.databricks.com/aws/en/reference/jobs-api-2-2-updates).

## Outcome Interpretation

`status.state` is authoritative whenever it is present and one of the current documented states. A terminal success is only:

```text
status.state == "TERMINATED"
and status.termination_details.code == "SUCCESS"
```

The deprecated `state.life_cycle_state` and `state.result_state` are used only when `status` is absent. When both are present but conflict, the helper sets `mixed_state_conflict: true`; it keeps the current status result. An unknown root status or terminal code fails closed with `outcome_complete: false` and exit `1`.

Known active states are valid snapshots, but have `is_terminal: false` and `outcome_complete: false`. A complete terminal failure exits `0`, because the status snapshot is valid, with `is_success: false`. `CLUSTER_TERMINATED_BY_USER` is treated as a known non-success terminal code for both root runs and task runs, so it remains actionable through `failed_task_runs` rather than being misclassified as an unknown outcome.

## Reported Child Outcomes

The helper selects the current root-task attempt using the existing repair chronology before interpreting task state. Malformed or unknown state on a nonselected historical attempt does not poison the selected scope. Every returned ForEach iteration is interpreted as returned; the helper does not infer a latest iteration or request `only_latest`.

For each selected task and returned iteration, a present `status` key is authoritative. It never falls back to deprecated `state`, even when the current object is incomplete or contains a future state. If `status` is absent, the helper may use legacy `state`. Missing optional outcome fields, future enum strings, and known active states are valid snapshots with `reported_child_outcomes_complete: false`. A present status, state, termination-details object, or scalar with the wrong JSON shape is `invalid_cli_schema` and uses the existing exit `1` error path.

Recognized terminal child outcomes follow these rules:

- Current `TERMINATED` plus `SUCCESS` is complete success. Any other known termination code is a complete failure except `SKIPPED`, which is complete non-execution.
- Legacy `TERMINATED` plus `SUCCESS` is complete success. Known non-success results are complete failures except `EXCLUDED`, which is complete non-execution.
- Legacy `INTERNAL_ERROR` without `result_state` is an actionable complete failure with effective termination `INTERNAL_ERROR`. An unknown present result remains incomplete.
- Legacy `SKIPPED` without a result or with `EXCLUDED` is complete non-execution. A distinct known failure result, including `DISABLED`, remains actionable.
- `BLOCKED`, `WAITING_FOR_RETRY`, and all other known active states remain nonterminal and incomplete. A missing or future terminal code or result remains incomplete rather than being inferred as success or failure.

Complete non-executions are omitted from both failure arrays. `DISABLED`, other recognized failures, and the legacy `INTERNAL_ERROR` case remain in the appropriate failure array. These child facts never rewrite root `outcome_complete` or strict root `is_success`. A root success can proceed to declared output verification while child diagnostic completeness remains a separate fact.

`reported_child_outcomes_complete` is true only when every selected task attempt and every returned iteration has a recognized terminal outcome. An empty reported child scope is vacuously true; it does not prove that the job has no child work. Require both `tasks_complete: true` and `reported_child_outcomes_complete: true` before claiming child diagnostics or empty failure arrays are exhaustive within the returned scope.

ForEach iteration evidence is scoped to the queried run ID. A parent job-run query does not transitively query task run IDs and cannot prove all ForEach iterations succeeded. Query the ForEach task run ID separately to retrieve its iterations. Because returned iterations are not filtered with `only_latest`, a reported iteration failure may be a superseded attempt and does not redefine root business success.

## Output Fields

The helper retains the original normalized fields and adds explicit current semantics:

| Field | Meaning |
|---|---|
| `life_cycle_state`, `result_state`, `state_message` | Compatibility fields mapped from the authoritative current state when available. Messages are redacted and bounded. |
| `status`, `termination`, `source` | Current status state, termination code, and either `status` or `legacy_state`. |
| `is_terminal`, `is_success`, `outcome_complete` | Terminal, strict success, and whether a terminal outcome is known. |
| `pages`, `tasks_complete` | Number of fetched pages and whether every bounded, coherent page was aggregated. |
| `reported_child_outcomes_complete` | Whether every selected current task attempt and every returned iteration has a recognized terminal outcome. Empty reported scope is vacuously true. |
| `task_run_ids` | The latest root-task run ID per task key, chosen using chronological `repair_history.task_run_ids` and `attempt_number`. |
| `failed_task_runs` | Failures among current root-task attempts, safe to use with `jobs get-run-output`. |
| `failed_iteration_runs` | ForEach iteration failures, reported separately from root-task repair state. |
| `mixed_state_conflict` | The deprecated and current root state disagree; the current state remains authoritative. |

## Bounded Failure Behavior

Each CLI call has a timeout and the whole page walk has a monotonic deadline. Stdout is capped at 25 MiB, stderr at 64 KiB, aggregation is capped, page tokens cannot cycle, and at most 100 pages are read. Timeout and overflow cleanup start the CLI in a dedicated process group: POSIX sends TERM then KILL to the session group, while Windows assigns the CLI to a kill-on-close Job Object, then uses `taskkill /T /F` and a direct-process fallback if that assignment is unavailable. A malformed response, changed run ID, missing token when `has_more=true`, contradictory `has_more` and token, output overflow, timeout, CLI error, JSON error, or page-limit failure exits `1` with a structured redacted error:

```json
{
  "error": {"code": "invalid_pagination", "message": "..."},
  "tasks_complete": false,
  "reported_child_outcomes_complete": false,
  "outcome_complete": false,
  "is_success": false
}
```

Never infer successful completion from an error payload. Credential-shaped diagnostics, authorization headers, JWTs, Databricks personal access tokens, secret assignments, and URL token parameters are redacted before output.
