# Fallback Path Audit

Use this checklist when a prompt, role, or multi-agent contract assumes a tool, agent, service, or handoff surface might exist.

## Required Questions

For each required capability, answer:

- What exact tool, agent, file, or service is required?
- How does the agent detect that it is available?
- What is the approved fallback when it is missing?
- What is the stop condition if there is no safe fallback?
- What artifact records the blocker or degraded mode?
- Who owns the next action?

For each required artifact, answer:

- Does this workflow create the artifact, or is it only a possible output from another helper?
- If it is optional, what fallback input should the agent use when it is absent?
- If input evidence is empty, should the agent output nothing, write an empty report, or produce a diagnostic?
- Can one required identifier derive another? If yes, require only the source identifier and state the override rule.

## Handoff Return Conditions

Every handoff needs a return condition:

- success artifact or field
- blocker artifact or field
- timeout or cycle budget
- evidence required before the coordinator trusts the result
- supersession rule if two workers disagree

## Bad And Good Patterns

Bad pattern: "Launch workers and continue when they are done."

Good pattern: "If native worker launch is unavailable, write manual prompts to `<path>`, require each worker to write `<status path>` and `<findings path>`, and treat missing fresh files as a dispatch blocker."

## Prompt Text Pattern

Use explicit fallback language:

```text
If <capability> is unavailable, do <fallback>.
If <fallback> cannot produce <evidence>, stop and report <blocker artifact>.
Do not infer success from silence or stale state.
```