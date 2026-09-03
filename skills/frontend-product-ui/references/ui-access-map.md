# UI Access Map

Use this template when the task is to inspect, operate, or hand off an existing interface rather than build a new one.

## Required Fields

Record each interface with:

- name and purpose
- entry URL, command, route, tab, or tool
- auth/session source
- browser/profile/tool boundary
- visible views and navigation paths
- controls and actions available
- data shown and its source confidence
- read actions verified
- write actions verified or intentionally avoided
- reproduction steps or click path
- proof artifacts such as screenshots, logs, status text, or command snippets
- last verified timestamp and timezone
- stale or poisoned-state caveats
- owner file when multiple agents collaborate

## Compact Template

```markdown
## <Interface Name>

- Entrypoint:
- Session/auth source:
- Browser/tool/profile:
- Visible views:
- Controls:
- Data shown:
- Read actions verified:
- Write actions verified:
- Reproduction steps:
- Proof artifacts:
- Last verified:
- Caveats:
- Owner/update rule:
```

## Collaboration Rules

- Read trusted source files before updating the access map.
- Preserve a ledger of stale, poisoned, or contradicted UI observations.
- Update only the agent-owned file unless the user asks you to modify another source.
- If another agent reported a view or capability, label it as reported until you verify it yourself.
- Include what changed since the last update when the access map is used as a status handoff.

## Completion Criteria

An access map is useful when another worker can answer:

- how to open the interface
- which session or auth state is required
- what the interface shows
- which controls are safe to use
- what was verified and when
- what evidence supports the claim
- what caveats might make the state stale

Do not stop at a prose summary when the user asked for a durable access document. Update the named file with the interface contract and continue work when the original task requires continued action.