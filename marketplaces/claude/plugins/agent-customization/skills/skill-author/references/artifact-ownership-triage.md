# Artifact Ownership Triage

Use this before editing when the request mixes skills, prompts, instruction files, AGENTS.md, MCP docs, scripts, or generated artifacts.

## Classify The Artifact

| Artifact | Primary owner | `skill-author` role |
|----------|---------------|---------------------|
| `skills/<name>/SKILL.md`, skill references, skill scripts, skill assets, skill templates, `skills/<name>/agents/openai.yaml` | `skill-author` | Primary |
| `.prompt.md`, prompt wrappers, agent prompts, role specs | Agent prompt or customization workflow | Supporting only when reusable skill guidance changes |
| `.instructions.md`, global guidance, repository coding standards | Instruction or customization workflow | Supporting only when a skill should link to or mirror the rule |
| `AGENTS.md` | `agents-md` or repository guidance workflow | Supporting only for skill-specific reusable lessons |
| MCP docs, MCP servers, registered tools, runtime surfaces | MCP or domain workflow | Supporting only when skill guidance or helper scripts change |
| Retrospective evidence, logs, generated evidence packs | Retrospective workflow first | Primary only for converting verified evidence into skill deltas |

## Primary And Supporting Skill Rule

Choose one primary owner for behavior changes.

- If a domain skill or MCP implementation owns the bug, fix that behavior first under the owning workflow.
- Use `skill-author` afterward to capture the reusable guardrail, checklist, script, or validation rule.
- Define the handoff return condition: what artifact is fixed, what evidence proves it, and what skill update remains.

Do not use `skill-author` to justify editing arbitrary customization files. If the deliverable is not a skill artifact, state the owning workflow and keep this skill scoped to reusable skill guidance.

## Canonical Roots And Mirrors

Before writing, identify:

- canonical source path
- generated or copied mirrors
- files that are source-of-truth versus generated output
- paths that belong to another repository, user profile, plugin cache, or temporary folder

Default source of truth for shared skills is `c:/Users/user/collab/agents/skills/<skill-name>/`. Do not patch mirror copies directly unless the user explicitly asks for a one-off repair. Verify mirror parity after source changes when the workspace maintains mirrors.

## Bad And Good Patterns

Wrong:

```text
Patch the Codex mirror because that is the file currently visible.
```

Correct:

```text
Patch the canonical skill under c:/Users/user/collab/agents/skills/, then verify the Codex mirror matches.
```

Wrong:

```text
Update an MCP handoff rule in a skill without checking whether the named MCP servers exist.
```

Correct:

```text
Inventory the current MCP server and tool surface first, remove fictional boundaries, then add only reusable skill guidance that matches the observed implementation.
```
