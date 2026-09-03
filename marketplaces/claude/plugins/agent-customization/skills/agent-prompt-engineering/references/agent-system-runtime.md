# Agent System Runtime

Use this when a prompt needs more than better wording. The goal is to define how the agent moves work through states, stores context, and recovers from interruption.

## Runtime Contract

Specify:

- Entry condition: what input starts the agent
- Completion condition: what "done" means
- Caller: who invokes the helper agent, MCP, queue, worker pool, or workflow
- Trigger: when the caller should invoke it and when it should not
- Durable artifacts: files, logs, branches, plans, manifests, or reports that survive context loss
- Durable state location: where progress, worker output, queue state, or compiled results land
- State transitions: pending, in progress, blocked, verifying, done, failed, or custom states
- Sync versus async behavior: whether the caller waits, polls, resumes later, or hands off
- Polling or wait behavior: timeout, cycle budget, freshness check, and stale-state handling
- Result retrieval: exact file, field, command, endpoint, or artifact that contains the outcome
- Tool authority: which commands, APIs, or repositories the agent may use
- Failure modes: missing tool, empty input, worker timeout, partial output, stale state, platform failure, or conflicting results
- Retry policy: when to retry, repair, hand off, escalate, or stop
- Caller next action: what the caller does after success, failure, or no safe fallback
- Human-question boundary: what can be assumed versus what must be escalated
- Resume behavior: how the agent reconstructs state from files and logs
- Task inputs: named prompt files, task briefs, notebooks, hook outputs, environment notes, and user-provided artifacts that must be read before acting

## Design Rules

- Model the workflow as a state machine before adding motivational or persona text.
- Make every external side effect visible in a durable artifact or command output.
- Give each worker a clear ownership boundary and write scope.
- Define failure routing: retry, repair, hand off, escalate, or stop.
- Define validation as part of the state machine, not an optional final note.
- Treat named prompt files and task briefs as task-scoped user input. Read them fully and resolve conflicts by the normal authority order instead of treating them as background examples.
- Treat hook output as contextual evidence unless the user explicitly makes it part of the task. It can identify risks or changed files, but it should not override direct user instructions, repo guidance, or the named prompt contract.
- Scope tool surfaces by role. A role prompt should say which tools are allowed, which are forbidden, and why that role needs them; broad tool access needs an explicit operational reason.
- Define the call path before implementation starts. The prompt should make it clear who calls the capability, what they pass, how work is monitored, where output lands, what success means, and what happens next.
- Do not require helper artifacts unless this workflow creates them. If helper outputs are optional inputs, name the fallback source of truth.
- For extraction, summarization, retrospective, or review workflows, define empty-input behavior explicitly.

## Review Questions

- Can the agent tell whether it is done without asking the user?
- Can another agent resume from the persisted artifacts?
- Are tool permissions and write scopes explicit enough to avoid conflicting edits?
- Does the prompt distinguish a concrete blocker from an incomplete attempt?
- Are examples representative of real failure modes, not just ideal happy paths?

Start from the [runtime contract template](../templates/runtime-contract-template.md) when the runtime call path needs its own file.
