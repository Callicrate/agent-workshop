# Public Surface Simplification

Use this when skill guidance affects tools, MCPs, scripts, commands, prompts, or any agent-facing operation.

## Agent Mental Model First

Review what the agent or user must understand, not only what the implementation can distinguish.

Prefer:

- one clear public abstraction over multiple implementation-specific operations
- derived defaults over redundant required inputs
- lifecycle wrappers over partial scripts
- generic core helpers plus thin domain wrappers
- public names that describe the user's task, not internal machinery

User confusion about a public operation is evidence of abstraction or discovery failure. Treat repeated questions such as why two tools exist, what an input means, or which command owns a lifecycle as findings.

## Before And After Surface Checks

When a skill update claims simplification, produce a before and after inventory:

- public tool, command, prompt, workflow, or script names
- required inputs
- lifecycle states exposed to the agent
- hidden setup details the agent must know
- deprecated, hidden, or internal-only operations

Do not call the work simpler if public names or required concepts increased without a clear compensating reduction.

## Lifecycle Wrapper Checklist

For scripts and deterministic helpers exposed by skills, check:

- start or create action
- status or resume action
- result or output retrieval
- timeout and wait controls
- validation or health check
- cleanup or failure handling when needed

Partial helpers that start background work but do not expose status, result, or failure state should be findings or implementation targets.

## Bad And Good Patterns

Wrong:

```text
Keep both public operations because they call different internal functions.
```

Correct:

```text
Expose one public operation that matches the agent task. Keep internal helper functions private or document them as implementation details.
```

Wrong:

```text
Require both `ctf_id` and `project` when one can be derived from the active challenge context.
```

Correct:

```text
Require the canonical identifier, derive the rest, and fail loudly only when the context cannot be resolved.
```

## Live Boundary Checks

Before documenting cross-skill or MCP handoffs, verify the named systems exist in the repository or runtime. Remove fictional boundaries or label them as proposed future work.