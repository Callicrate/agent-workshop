# One-Off Task Recovery

Use this when a multi-task Databricks job has one failed task and the user asks for an isolated repair job or prepare-only recovery path.

## Preserve The Failed Task Contract

Read the existing `databricks.yml` or bundle resource before creating the repair job. Preserve:

- task key and purpose
- notebook, Python file, wheel task, or SQL task entrypoint
- parameters, widgets, named arguments, and source window
- libraries and environment spec
- job cluster, task cluster, serverless setting, or cluster policy
- permissions, `run_as`, tags, notifications, and timeout settings
- upstream artifacts needed by the failed task

Name the repair resource clearly, for example `repair_train_phish_2026_03_04`, and mark it as one-off or repair-only.

## Validate-Only Default

If the user says `do not run it`, `make it so I can run it`, or `prepare only`:

1. create or describe the one-off resource
2. validate the bundle or generated config
3. do not call `databricks bundle run` or `jobs run-now`
4. report the exact command the user can run later only when useful

Operator run-control is a hard boundary.

Repair, rerun, and cancel actions are live execution controls. Prepare the command or resource when asked, but execute only when the user explicitly requests the action or the existing task contract authorizes it.

## Repair Scope

- Isolate the failed task's date, category, or source window.
- Do not rerun successful expensive upstream tasks unless the failed task depends on fresh upstream output.
- Use task-scoped repair or Databricks repair-run behavior when it preserves successful task outputs.
- If capacity blocked the original date, split the window into smaller repair jobs or record the date as blocked in the run ledger.

After the repair run completes, return to [output-contract-verification.md](output-contract-verification.md) before declaring the repair verified.