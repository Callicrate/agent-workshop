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
- `queried_run_scope`, either `job_root` or `foreach_task_run`
- `tasks_complete`
- `reported_child_outcomes_complete`
- source date, partition, category, or window
- terminal lifecycle state and result state
- `monitoring_cadence` that preserves the user's requested maximum interval or, when none is supplied, the selected 30–90 second cadence from the [deploy-monitor workflow](deploy-monitor-workflow.md)
- `last_nonterminal_observed_at` and `last_nonterminal_state` when the run is still active
- `monitoring_stop_condition` that preserves the user's requested completion condition or, when none is supplied, records the workflow default: monitor to terminal, verify declared outputs after terminal success, and stop early only for an explicit pause or concrete blocker/escalation
- main output table and audit table checks
- source freshness checks when external catalogs are involved
- next action or next date
- incomplete child evidence and its next action when either completeness flag is false
- explicit run-control state such as `no_rerun`, `prepare_only`, or `paused`

Update the ledger before every monitoring pause, long-running wait, context transition, handoff, or next-window run.
For an active run, the successor must resume monitoring before the recorded cadence expires. Do not replace an explicit user cadence or completion condition with the skill's default polling guidance; when no cadence is specified, record the selected cadence from that guidance.

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
- queried_run_scope: job_root
- tasks_complete: true
- reported_child_outcomes_complete: false
- source_window: 2026-03-04
- terminal_state: TERMINATED / SUCCESS
- monitoring_cadence: <user-requested maximum interval, or selected 30–90 second workflow cadence>
- last_nonterminal_observed_at: 2026-05-26T15:29:30Z
- last_nonterminal_state: RUNNING
- monitoring_stop_condition: <user-requested completion condition, or default: monitor to terminal; verify declared outputs after terminal success; stop early only for explicit pause or concrete blocker/escalation>
- output_checks: promotion_candidates SCD2 current-row check passed
- incomplete_child_evidence: query the ForEach task run ID before claiming iteration diagnostics are exhaustive
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
  "queried_run_scope": "job_root",
  "tasks_complete": true,
  "reported_child_outcomes_complete": false,
  "source_window": "2026-03-04",
  "terminal_state": "TERMINATED/SUCCESS",
  "monitoring_cadence": "<user-requested maximum interval, or selected 30–90 second workflow cadence>",
  "last_nonterminal_observed_at": "2026-05-26T15:29:30Z",
  "last_nonterminal_state": "RUNNING",
  "monitoring_stop_condition": "<user-requested completion condition, or default: monitor to terminal; verify declared outputs after terminal success; stop early only for explicit pause or concrete blocker/escalation>",
  "output_checks": ["row counts passed", "SCD2 current rows passed"],
  "incomplete_child_evidence": "query the ForEach task run ID before claiming iteration diagnostics are exhaustive",
  "next_action": "run 2026-03-05"
}
```

## Stop Conditions

- Preserve an explicit user cadence and completion condition in the ledger. If the user specifies no cadence, record the selected 30–90 second cadence from the deploy-monitor workflow. If the user specifies no completion condition, record the workflow default: monitor to terminal, verify declared outputs after terminal success, and stop early only for an explicit pause or concrete blocker/escalation. For an active run, record its latest nonterminal state and observation time before pausing, handing off, or compacting context; resume before the recorded cadence expires.
- Record `tasks_complete` and `reported_child_outcomes_complete` separately at every checkpoint. Require both before claiming child diagnostics or the absence of child failures are exhaustive within the reported scope. Empty reported scope is vacuously complete and does not prove that no child work exists.
- Record whether `run_id` is a job root or a ForEach task run. A parent job-run query does not establish transitive iteration coverage; query the ForEach task run ID separately. Returned iteration failures may be superseded attempts.
- Keep root terminal outcome and declared output verification separate from child diagnostic exhaustiveness. A root success may advance to output verification, while incomplete child evidence and its next action remain in the ledger.
- If the user says `pause`, record the current state and stop.
- If the user says `do not rerun` or `do not run it`, set the run-control state and avoid starting another live run.
- If capacity blocks a date, record that date as blocked and preserve enough context for a repair job or split-window retry.
