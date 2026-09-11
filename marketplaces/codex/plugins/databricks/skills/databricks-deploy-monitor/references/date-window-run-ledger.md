# Date-Window Run Ledger

Use this when deploy-monitor work spans long waits, compaction, handoff, or repeated date-window runs.

## Required Fields

Persist a compact Markdown or JSON ledger with:

- `at_timestamp`
- `profile`
- `target`
- `workspace_host`
- `job_key`
- `job_id`
- `run_id`
- `task_run_ids`
- source date, partition, category, or window
- terminal lifecycle state and result state
- main output table and audit table checks
- source freshness checks when external catalogs are involved
- next action or next date
- explicit run-control state such as `no_rerun`, `prepare_only`, or `paused`

Update the ledger before every monitoring pause, long-running wait, context transition, handoff, or next-window run.

## Markdown Template

```markdown
## 2026-03-04 deploy-monitor run

- at_timestamp: 2026-05-26T15:30:00Z
- profile: TAP
- target: prod
- workspace_host: https://example.cloud.databricks.com
- job_key: promote_and_score
- job_id: 123456
- run_id: 789012
- task_run_ids: train_phish=789013, score=789014
- source_window: 2026-03-04
- terminal_state: TERMINATED / SUCCESS
- output_checks: promotion_candidates SCD2 current-row check passed
- source_freshness: tap.feed max_ingested_at=2026-03-04T23:55:00Z
- next_action: deploy and run 2026-03-05
```

## JSON Shape

```json
{
  "at_timestamp": "2026-05-26T15:30:00Z",
  "profile": "TAP",
  "target": "prod",
  "workspace_host": "https://example.cloud.databricks.com",
  "job_key": "promote_and_score",
  "job_id": "123456",
  "run_id": "789012",
  "task_run_ids": {"score": "789014"},
  "source_window": "2026-03-04",
  "terminal_state": "TERMINATED/SUCCESS",
  "output_checks": ["row counts passed", "SCD2 current rows passed"],
  "next_action": "run 2026-03-05"
}
```

## Stop Conditions

- If the user says `pause`, record the current state and stop.
- If the user says `do not rerun` or `do not run it`, set the run-control state and avoid starting another live run.
- If capacity blocks a date, record that date as blocked and preserve enough context for a repair job or split-window retry.