# Subagent Coordination Patterns

Use this only when `AGENTS.md` describes subagent use, worker dispatch, coordinator roles, blind review passes, or dead-path ledgers.

## Required Contract

Document:

- coordinator role and authority
- when to use subagents
- allowed context levels, including full context, focused context, and blind or low-context passes
- files every subagent must read first
- files every subagent must write or update
- expected return format
- acceptance and rejection criteria for subagent findings
- how results are compared against prior dead paths or invalidated evidence
- idle-agent reuse rules if the environment has standing worker capacity

Share focused context with each subagent.
For ordinary repos, do not include secrets, credentials, private customer data, or broad repository dumps when a focused file set is enough.
For authorized CTF or security-lab work, pass only task-needed lab context to workers and prefer pointers to repo-owned evidence/status files. Do not persist credentials, flags, captured sensitive artifacts, or discovered target secrets in `AGENTS.md` by default.

## Blind Or Low-Context Passes

Blind passes are useful only when the repo explains how to evaluate them.
AGENTS.md should state:

- the minimal briefing allowed
- what prior context is intentionally withheld
- how the coordinator compares suggestions against verified evidence
- how to avoid repeating dead paths unless the goal is deliberate reevaluation

## Dead-Path Ledger

For exploratory or CTF-style work, document where agents record:

- failed route or hypothesis
- evidence that rejected it
- time and environment state
- condition that would make it worth retrying

Subagent output that ignores the current coordinates, trusted notes, or stop condition should not be accepted as complete.
