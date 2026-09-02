---
name: databricks-project-status
description: "Use when asked how a Databricks project is doing; writes a status report on job/pipeline/table/model/serving health/usage/trends/risks. Do not trigger for deploy/repair/workload authoring."
metadata:
  short-description: Review Databricks project health.
---

# Databricks Project Status

## When to Use

- The user asks how a Databricks project, scheduled workload, model, table set, or endpoint is doing.
- A periodic health, reliability, usage, model-quality, or operational-readiness review is needed.
- The user wants failures, gaps, degradation, trends, emerging risks, or improvement recommendations.
- A timestamped report must be written under `status-reports/` in the project root.

## When NOT to Use

- Deploying, running, repairing, canceling, or actively monitoring a known run; use `databricks-deploy-monitor`.
- Editing `databricks.yml` or designing bundle resources; use `databricks-asset-bundles`.
- Fixing localized ETL, training, serving, or runtime code after this review identifies the owner.
- Auditing one MLflow run for promotion readiness without a broader project-status question; use `mlflow-run-auditor`.

## Read-Only Contract

This is an inspect-and-report workflow by default.
Do not start compute, submit SQL that could auto-start a stopped warehouse, invoke serving endpoints, trigger or repair jobs, cancel runs, alter schedules, move aliases, change endpoint traffic, mutate permissions, optimize tables, or write project data unless the user separately authorizes that operation.
If inspection requires starting a warehouse or other paid resource, report the blocked dimension instead of starting it.

## Workflow

1. Identify the project root, intended Databricks profile, workspace, environment, owners, and documented SLAs. Use `databricks-api-calls` to prove the profile, host, and principal before live inspection. Never silently use `DEFAULT`.
2. Prove the chosen profile with `databricks-api-calls`, writing its redacted context output to a temporary file outside the inspected project. Then create the report scaffold with that receipt:

   ```powershell
  $contextFile = Join-Path ([System.IO.Path]::GetTempPath()) ("databricks-context-" + [guid]::NewGuid().ToString("N") + ".json")
  try {
    python <agents-root>/skills/databricks-api-calls/scripts/check_databricks_context.py --profiles <profile> | Set-Content -LiteralPath $contextFile -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Databricks context preflight failed." }
    python <skill-root>/scripts/status_report.py create --project-root . --project-name <name> --profile <profile> --window-timezone <iana-timezone> --context-file $contextFile
  } finally {
    Remove-Item -LiteralPath $contextFile -Force -ErrorAction SilentlyContinue
  }
   ```

   The helper accepts one successful effective-host/current-user receipt for the exact profile, writes it to `Workspace host / principal`, uses a UTC filename timestamp, and refuses to overwrite an existing report. The temporary receipt is always cleaned up. Without `--context-file`, it preserves the legacy `Unknown until verified` scaffold.
3. Build a target manifest from repository configuration and live read-only discovery. Include jobs, Lakeflow pipelines, tables, volumes, MLflow experiments and registered models, serving endpoints, warehouses, dashboards or alerts, and downstream consumers that actually belong to the project.
  For every list or search API, exhaust its documented token, offset, or `has_more` pagination, deduplicate by stable resource ID, and record requested versus observed time coverage. Partial, rate-limited, retention-truncated, or permission-blocked evidence must remain visibly incomplete.
4. Define a current review window and an equal baseline window. Default to the most recent 30 complete calendar days and the preceding 30 days in the workload schedule timezone unless project cadence or an explicit user window requires something else. Keep `Window basis` as `Latest complete days` for the default; for an adjusted window, replace it with `user-specified: <reason>`, `change-bounded: <reason>`, or `retention-limited: <reason>`. Record timezone, exclusions, expected schedule opportunities, and incomplete data.
5. Follow [references/review-workflow.md](references/review-workflow.md). Collect source coverage before conclusions, then assess reliability, failures, data health, model quality, serving, usage, performance, cost, security, ownership, and emerging risks. Consult sibling skills for interpretation, but use inspect-only APIs with the explicit profile; do not enter deploy, rerun, endpoint invocation, or paid-compute workflows.
6. Define uptime per resource. For periodic jobs use schedule fulfillment and successful completion, not wall-clock uptime. For tables use freshness and queryability. For endpoints or continuously running pipelines use measured availability only when historical state or monitoring data exists.
7. Compare current and baseline windows. Report raw values, absolute deltas, relative deltas when denominators are meaningful, sample counts, and confidence. Do not call a one-point difference a trend.
8. Group errors by stable signature, resource, task, first and last occurrence, recurrence, affected outputs, and observed recovery. Pull run or task output only through inspect-only calls.
9. Separate findings into `critical`, `high`, `medium`, and `low`. Every finding needs evidence, impact, confidence, owner, recommendation, and a verification step. Distinguish current failures from future risks and hygiene improvements.
10. Fill every report section. Mark unsupported dimensions `unknown` or `not applicable` with a reason; do not silently omit them or invent metrics.
11. Validate the finished report:

    ```powershell
    python <skill-root>/scripts/status_report.py validate ./status-reports/<timestamp>-status.md --project-root .
    ```

12. Stop after the report and recommendations. If the user asks to implement a recommendation, switch to the owning skill and apply the relevant live-operation gate.

## Required Report Outcome

- The report path exactly matches `status-reports/YYYYMMDDTHHmmSS-status.md`.
- Current and baseline windows use complete calendar-day boundaries in the recorded timezone.
- A non-default window declares its basis and reason instead of appearing current.
- Repository metadata states whether the reviewed project subtree was clean or dirty when the report was created.
- The executive summary states whether the project is healthy, watch, degraded, critical, or unknown and why.
- The executive status is never healthier than the most severe known scorecard dimension.
- The report covers every discovered resource class and explicitly accounts for unavailable evidence.
- The evidence coverage ledger marks each source complete, partial, blocked, or not applicable and records pagination, observed range, and limitations.
- Recommendations are prioritized, concrete, and tied to findings rather than generic best practices.
- No overall numeric health score is invented unless the project already defines a weighted scoring contract.

## Deterministic Tools

| Resource | Use When | Outcome |
|---|---|---|
| [scripts/status_report.py](scripts/status_report.py) | Create or validate a project status report | Correct timestamped path and required-section validation |
| [templates/status-report.md](templates/status-report.md) | Draft the report | Stable health, trend, finding, recommendation, and evidence structure |
| [references/review-workflow.md](references/review-workflow.md) | Conduct the review | Cross-surface evidence checklist, formulas, severity, and routing |

## References

- [references/review-workflow.md](references/review-workflow.md) - evidence sources, health dimensions, trend rules, and finding quality
- [templates/status-report.md](templates/status-report.md) - report structure created by the deterministic helper
