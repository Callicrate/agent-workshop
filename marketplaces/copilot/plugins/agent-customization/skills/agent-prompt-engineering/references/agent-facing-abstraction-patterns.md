# Agent-Facing Abstraction Patterns

Use this when prompt authors need to decide what operational detail belongs in an agent prompt versus inside the backing tool, wrapper, or service.

## Rule

Prompts should describe the agent's intent and public operation. Tools should own infrastructure mechanics.

An agent-facing contract should expose:

- the normal operation name
- the inputs the agent can know
- the output or durable artifact the agent should inspect
- the success signal
- the blocker or break-glass condition

It should hide:

- transport folders
- tmux panes, shells, or process managers
- queue internals and worker scheduling details
- raw shell escape hatches for routine work
- duplicate lower-level operations that do the same job as the normal path

## Patterns

- Expose `terminal` behavior to an agent; keep tmux setup and pane management inside the execution tool.
- Expose ideation request, polling, and consolidated results; keep worker launch and compilation internals inside the generic service.
- Expose current public MCP tools; keep retired, proposed, or internal-only helpers out of role prompts.
- Put domain adaptation in a wrapper or edge prompt. Keep reusable orchestration generic.

## Review Questions

- Can the agent complete the routine workflow by following one obvious path?
- Does the prompt require the agent to understand implementation details that a tool could own?
- Are admin and break-glass paths labeled with concrete triggers?
- Do examples reference only verified public surfaces?