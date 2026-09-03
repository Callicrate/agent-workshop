# System Prompt Patterns

Use this when the deliverable is a single-agent system prompt.

## Section Contract

| Section | Put Here | Keep Out |
|---------|----------|----------|
| Identity | Agent name, single job, audience | Tool rules, confirmation policies |
| Environment | Tools, channels, filesystem, runtime, output surfaces | Persona claims |
| Security | Explicit never rules, confirmation gates, forbidden actions | Tone or style preferences |
| Role | Scope, authority, non-goals, ownership boundaries | Duplicated guardrails |
| Instructions | Ordered workflow, retries, validation, escalation | Identity fluff |
| Memory | What context to load, persist, or refresh | Process rules already covered above |

## Writing Rules

- Use direct imperatives.
- Keep one rule per sentence or bullet.
- Turn values into observable behavior.
- Put destructive-action gates in Security, not scattered across the prompt.
- For agents with file, shell, network, API, or MCP tools, add the relevant checks from [tool-security-patterns.md](tool-security-patterns.md).
- For large prompts, keep the base prompt short and move examples or specialized policies to linked references using [prompt-size-versioning.md](prompt-size-versioning.md).
- If a line would fit almost any agent, cut it.

## Minimal Review Pass

- Identity names one job, not a personality essay.
- Environment lists actual tools and IO.
- Security includes at least one explicit confirmation rule for destructive or irreversible actions.
- Role states what the agent owns and what it does not own.
- Instructions define retry, validation, and blocker handling.
- Memory states which context sources to read and when.

## Wrong / Correct

```text
Wrong: You are a helpful assistant who does your best and is careful.
Correct: You are the release reviewer. Read changed files, identify regressions, cite file paths, and never approve destructive changes without explicit confirmation.
```

```text
Wrong: Be careful with destructive operations.
Correct: Never run git push --force or delete production resources without explicit user confirmation.
```

For starter files, `--template-baseline` checks that the skeleton has the required headings. It does not validate whether the final prompt content is specific enough.

Start from [system prompt template](../templates/system-prompt-template.md) when writing a new prompt file.
