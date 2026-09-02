# Agent-Readable Docs

Use this reference when an interface doc will be used as a starting point, handoff, or operating contract for autonomous agents.

## Shape

The first file an agent reads must be complete enough to start from that point without hidden conversation context.

Include:

- purpose and current objective
- role or actor that should use the doc
- hard constraints and forbidden actions
- allowed tools, commands, clients, or interfaces
- required environment or workspace state
- source-of-truth files and shared state files
- handoff files and expected update format
- success condition and stop condition
- known unknowns and blockers

## Style

- Write direct instructions, not narrative background.
- Put operational invariants in bullets or tables.
- Use exact paths, command names, tool names, and state files.
- Separate verified facts from hypotheses or future work.
- Avoid editor-specific assumptions unless the workflow truly requires that editor.
- Preserve external or trusted notes and mark whether they are read-only.

## Interface Contracts For Agents

When an agent must call an interface, state:

- what to call
- when to call it
- what inputs to pass
- where output appears
- what counts as success
- what failure evidence to record
- what the agent should do next

## AI-Ingestion Pass

Before finishing agent-readable docs, check:

- Can a fresh agent identify the first action without asking the user?
- Are constraints visible without reading long prose?
- Are shared files and writable files separated?
- Are command examples copyable in the target environment?
- Are VS Code, code-server, WSL, container, or terminal assumptions explicit?
- Does the doc avoid assuming artifacts that another workflow might not have created?