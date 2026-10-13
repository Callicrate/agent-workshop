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

## Date-Specific Recovery Preflight

Do this before preparing or triggering a repair or rerun. It is an inspect-and-compare step, not permission to execute.

Build two distinct private receipts. The **original-run receipt** comes from the failed root/task run with history and resolved values included. The **current-definition receipt** comes from the current job/task definition that a repair would use. Do not substitute one for the other: a repair uses current definitions, while the failed run's resolved values establish what actually ran.

Read the needed paginated run collections, including tasks, job parameters, resolved values, and repair history, to completion with bounded pages and time. Keep raw responses and parameter values only in private in-memory handling. Do not stream CLI JSON, write a status dump, or report raw parameters, paths, stderr, or credential-shaped values. An empty page does not prove every collection is complete. Stop on missing/changed run identity, an unbounded or repeated page token, or conflicting duplicate task, parameter, or repair-history identities. A conflict is a blocker, not a value to choose.

The safe receipt reports only non-sensitive input key names, a normalized non-sensitive window when known, collection completeness, failed task identity, and structural drift. Redact sensitive keys and all sensitive values. A task-resolved value is not proof that it was a job parameter; only pass a value as `job_parameters` when the failed-run evidence identifies that key as a job-level parameter.

Use this input decision rule:

| Evidence | Recovery input |
| --- | --- |
| Proven job-level failed value **B**, current bundle default **A** | Preserve **B** as the repair/rerun override. Do not silently fall back to **A**. |
| Current user explicitly requests **C** for a proven job-level key | Use **C** over **B**, record the supersession, and re-evaluate replay safety for **C**. |
| Current user explicitly requests **C** for a task-only key/widget | Bind **C** through the matching task or one-off definition. Never put it in `job_parameters`. |
| Current user explicitly requests **C** but key scope is unknown | Prepare only until current-definition or failed-run evidence proves the scope. |
| Task-only resolved failed value | Preserve it through the matching task contract or a one-off task. Do not invent a job-parameter override. |
| Missing, incomplete, or conflicting window/input evidence | Prepare only. Do not execute or guess a window. |

Record current-definition drift separately from input selection: task entrypoint, task type, task parameters, environment or libraries, compute, timeout, dependencies, and output/side-effect surfaces. A current fix can be necessary, but it does not change the original effective input. A user choosing **C** does not establish its scope. When the repair API accepts `job_parameters`, provide **B** or **C** only for a job-level key proven by current-definition or failed-run evidence, or by an explicitly requested and validated job-level contract change with a task binding. Keep task-only **C** in its matching task/widget or one-off definition.

## Replay Safety Proof

Do not infer safety from the word `MERGE`, a successful earlier task, or a date in the repair name. Before another execution, record all of these as actual observations for the selected keys and window:

- the failed task's input keys and concrete date/category/source window, with sensitive values redacted
- incoming duplicate checks for that same window and the target's existing-key or duplicate check
- the write condition that makes a repeated row update, skip, or otherwise remain safe, plus any partial-write boundary
- every append-only audit/history table, model registration, alias movement, notification, or external side effect the task can reach

An append-only partial write is blocked until actual incoming and target duplicate checks prove it safe. A `MERGE` path may proceed only when its observed keys and window prove that reprocessing the same input cannot create an unintended row or overwrite a different window. Model registration, alias movement, and notifications are append or externally visible effects; do not replay them when their prior effect or idempotency is unknown.

If any input window, duplicate check, partial-write effect, registry/alias state, or notification outcome is unknown, keep the work at a prepared manifest or one-off resource. Do not replay, cancel, or reconcile by guesswork.

## Validate-Only Default

If the user says `do not run it`, `make it so I can run it`, or `prepare only`:

1. create or describe the one-off resource
2. validate the bundle or generated config
3. do not call `databricks bundle run` or `jobs run-now`
4. report the exact command the user can run later only when useful

Operator run-control is a hard boundary.

Repair, rerun, and cancel actions are live execution controls. Prepare the command or resource when asked, but execute only when the user explicitly requests the action or the existing task contract authorizes it.

When the current task already authorizes the named repair or rerun and the recovery preflight and replay proof are complete, continue without a redundant approval prompt. All profile, workspace, resource-identity, production, and operation-class gates still apply.

## Repair Scope

- Isolate the failed task's date, category, or source window.
- Do not rerun successful expensive upstream tasks unless the failed task depends on fresh upstream output.
- Use task-scoped repair or Databricks repair-run behavior when it preserves successful task outputs.
- If capacity blocked the original date, split the window into smaller repair jobs or record the date as blocked in the run ledger.

After the repair run completes, return to [output-contract-verification.md](output-contract-verification.md) before declaring the repair verified.
