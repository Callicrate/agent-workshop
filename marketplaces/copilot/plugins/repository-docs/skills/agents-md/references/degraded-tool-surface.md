# Operational Capability Guidance

Use this only when `AGENTS.md` needs repository-defined capability checks, manual dispatch paths, blocked states, or live-operation stop conditions. For normal authoring, use [normal-authoring.md](normal-authoring.md).

## What To Capture

Add repository guidance only when it is verified by source, scripts, docs, or observed repeated failures.
For each operational capability, record:

- normal tool or workflow
- detection check
- approved alternate path
- stop condition
- artifact or status file to update
- owner for restoring the normal path

## Manual Dispatch

When repository docs define manual subagent or worker dispatch, `AGENTS.md` should specify:

- exact manual prompt fields
- files each worker must read first
- files each worker must write before returning
- expected return format
- how the coordinator accepts or rejects worker output

Do not rely on chat-only worker findings when the repository expects durable status, findings, or artifacts.
When dispatching manual workers or subagents, provide only the files and context needed for their task.
For ordinary repos, do not include secrets, credentials, or private customer data.
For authorized CTF or security-lab repos, keep shared material inside the task-defined lab scope and prefer references to repo-owned evidence/status files over embedding credentials, flags, captures, or target secrets in `AGENTS.md`.

## Capability Checks

For recurring blockers, define typed capability checks rather than prose warnings.
Useful fields are owner, status, evidence path, dedupe key, and next action.

Examples:

- local service running
- MCP tool available
- platform login present
- execution host ready
- browser access allowed
- target phase open

## Wording Pattern

Use direct operational wording:

```markdown
When `<capability check>` is blocked, stop at `<stop condition>` and write `<artifact>`.
Treat missing fresh output in `<artifact>` as blocked, not as success.
Do not proceed past `<stop condition>` without `<evidence>`.
```
