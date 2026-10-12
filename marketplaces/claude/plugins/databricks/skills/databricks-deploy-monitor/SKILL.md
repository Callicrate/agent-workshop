---
name: databricks-deploy-monitor
description: "Use when deploying Databricks bundles, monitoring runs, diagnosing run failures, or verifying outputs; drives run loops. Do not trigger for bundle design, pure code fixes, or API scripting."
metadata:
  short-description: Deploy and monitor Databricks jobs.
---

# Databricks Deploy And Monitor


## When to Use

- Deploying a Databricks Asset Bundle and keeping ownership through the resulting run
- Monitoring a known Databricks job run until it reaches a terminal state
- Diagnosing a failed bundle-triggered run and staying in the fix-redeploy loop until success
- Resuming a previous deploy-monitor session when you already have a `run_id`
- Verifying output tables, model aliases, promotion tables, or scored data after a terminal run
- Managing iterative date-window deploy, run, monitor, verify, and next-window loops
- Creating a one-off repair job for a failed task without running it yet

## When NOT to Use

- Initial bundle authoring or resource design that is not yet in a deploy-monitor loop
- Pure ETL, training, or serving code work once the failure is already localized there
- One-off Databricks API scripting or workspace inspection unrelated to a live run

## Delivery Unit Principle

When the user explicitly asks to deploy, run, watch, monitor, verify a live job, or fix a live Databricks failure, every code change, its tests, and live deployment verification belong to a single delivery unit. The full authorized loop is:

**local tests → bundle validate → deploy → monitor run → output contract verification → live job-setting verification**

If the user asks you to run, watch, monitor, or ensure completion, do not let the run drift into the background. Stay in the loop until the run reaches a terminal state, the user explicitly stops you, or an escalation condition in the workflow applies.

For production, destructive, traffic-shifting, schedule-changing, rerun, repair, cancel, alias-moving, or paid/long-running operations that were not explicitly requested, stop at a ready-to-run plan or manifest. For deploy, bundle, and agent-run tasks, `done` means terminal success or a concrete blocker. A patch, generated bundle, or started run is not a completed delivery unit.

See [references/deploy-monitor-workflow.md](references/deploy-monitor-workflow.md) for the complete delivery loop.

## Workflow

1. Run local tests first. Fix red tests before packaging.
2. Prove the Databricks profile and target workspace with [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) unless already verified in this session.
3. Classify the operation with [references/operation-classes.md](references/operation-classes.md) before deploy, run, repair, cancel, schedule, alias, production, or destructive actions.
4. Validate generated and edited bundles with the static validator when available, then `databricks bundle validate`. Abort on validation errors.
5. Deploy and start the target resource only when the operation class is authorized, then capture a `run_id`. Use [references/deploy-monitor-workflow.md](references/deploy-monitor-workflow.md) for the exact command order.
6. From this skill package root, monitor with `bash scripts/check_run_status.sh <run_id> <profile>`. The profile is required and never defaults. The helper uses the current Jobs 2.2 status field, bounded pagination, and a read-only CLI command. Read [references/run-status-helper-contract.md](references/run-status-helper-contract.md) before consuming its output or handling a nonzero exit. Keep polling until the run is terminal; reporting "it is running" is not a completion state.
7. If the run fails, identify the failing task run or single-task parent run, then pull output with `databricks jobs get-run-output <task_run_id> --profile <profile> -o json`.
8. Match the first actionable error against [references/common-failure-patterns.md](references/common-failure-patterns.md), apply the smallest fix, validate locally if possible, then redeploy and rerun.
9. Once the failure is localized, switch to the sibling skill that owns the code fix, then return to this loop for redeploy and recheck.
10. After terminal success, verify declared outputs with [references/output-contract-verification.md](references/output-contract-verification.md) before reporting the run as verified.
11. For multi-date or long-running loops, update [references/date-window-run-ledger.md](references/date-window-run-ledger.md) before every pause, compaction, handoff, or next-window run.
12. After output verification passes, verify live job settings (cluster policy, libraries, schedule, runtime version, permissions) against bundle expectations. Fix drift in the same delivery unit only when live workspace updates are authorized; otherwise report the exact drift and ready-to-run fix.
13. Respect live-operation boundaries. If the user says not to rerun, or has not authorized repair/rerun/cancel/production activation, fix local code or config and stop before the gated live action.
14. Do not unpause schedules, shift traffic, move aliases, repair/cancel active runs, delete/reset resources, or activate production behavior unless explicitly requested or already authorized by the current task contract.
15. If output verification localizes semantic scoring, SCD2, ETL, or training errors, switch to the owning sibling skill for the code fix, then return here for validation, rerun if allowed, output verification, and live job-setting verification.
16. Escalate only for permissions, ambiguous business logic, persistent platform failures, user-forbidden reruns, or the same error after 3 distinct fixes.

## Deterministic Tools

| Resource | Use When | Outcome |
|----------|----------|---------|
| [scripts/check_run_status.sh](scripts/check_run_status.sh) | You need a normalized, bounded Jobs 2.2 run-state summary | Read-only JSON summary with current status, termination, paginated task/ForEach failures, and explicit completeness fields |
| [scripts/check_run_status.py](scripts/check_run_status.py) | You need the deterministic implementation behind the public Bash entrypoint | Explicit-profile, bounded subprocess and pagination behavior |
| [references/deploy-monitor-workflow.md](references/deploy-monitor-workflow.md) | You need the exact deploy-start-monitor-fix loop | Ordered workflow, command set, and terminal-state handling |
| [references/output-contract-verification.md](references/output-contract-verification.md) | A successful run writes tables, registers models, changes aliases, or produces scored data | SQL and semantic checks before declaring output verified |
| [references/date-window-run-ledger.md](references/date-window-run-ledger.md) | A run loop spans dates, long waits, compaction, or handoff | Compact run ledger with run IDs, output checks, and next action |
| [references/one-off-task-recovery.md](references/one-off-task-recovery.md) | One task failed and the user wants an isolated repair job | Validate-only one-off task recovery checklist |
| [references/common-failure-patterns.md](references/common-failure-patterns.md) | You have the first actionable error and need the first fix path | Failure-family checks and fix order |
| [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) | The target Databricks profile or workspace has not been proven | Correct workspace before validate/deploy/run |
| [references/operation-classes.md](references/operation-classes.md) | A deploy, run, repair, cancel, schedule, alias, production, or destructive action may occur | Explicit live-operation gate before execution |

## References

- [references/deploy-monitor-workflow.md](references/deploy-monitor-workflow.md) - deploy, run, monitor, diagnose, and rerun loop
- [references/output-contract-verification.md](references/output-contract-verification.md) - post-run table, model, SCD2, scoring, and sampling checks
- [references/date-window-run-ledger.md](references/date-window-run-ledger.md) - durable state for long-running or repeated date-window loops
- [references/one-off-task-recovery.md](references/one-off-task-recovery.md) - failed-task repair jobs that preserve original task contracts
- [references/common-failure-patterns.md](references/common-failure-patterns.md) - common Databricks job failure families and first fixes
- [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) - profile, workspace, host, and read-only source checks before live work
- [references/operation-classes.md](references/operation-classes.md) - approval gates for live Databricks operations
- [references/run-status-helper-contract.md](references/run-status-helper-contract.md) - current status-first output, pagination, and failure contract
