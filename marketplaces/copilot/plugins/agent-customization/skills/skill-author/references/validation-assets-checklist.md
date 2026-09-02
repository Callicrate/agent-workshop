# Validation Assets Checklist

Use this checklist when reviewing or updating a skill for completeness.

## Canonical Root

- Source of truth is `c:\Users\user\collab\agents\skills\<skill-name>\`.
- Mirrors are not edited directly unless the user explicitly requests a one-off local repair.
- `SKILL.md` carries the context needed for standalone use or links directly to the relevant resource.
- For mixed customization work, identify the canonical owner for prompts, instructions, AGENTS.md, MCP docs, scripts, and generated mirrors before editing.
- After changing a canonical skill that has known mirrors, compare hashes or run the repository's sync process before closing out.

## Deterministic Support

Check whether the skill needs any of these:

- `scripts/` validator, auditor, scaffold, or renderer
- `assets/` schema, config block, request body, DDL, or static sample
- `templates/` starter file the agent is expected to edit
- `agents/openai.yaml` for optional Codex app UI metadata, dependency declarations, default picker prompt, or explicit-only policy
- `references/` detailed workflow, checklist, or bad/good examples
- inventory or overlap helpers when reviewing skill coverage across more than one skill
- public-surface inventory when a skill affects tools, MCPs, scripts, or agent-facing commands

Do not add empty folders.
Add deterministic assets only when they replace repeated reasoning or prevent a known failure.

## Minimum Examples

For recurring mistakes, include:

- one bad pattern
- one preferred pattern
- the evidence or validation command that proves the preferred pattern

## Final Checks

- `SKILL.md` stays concise and routes to references.
- Relative links resolve.
- Helper scripts parse or show help.
- Every executable named in workflow text exists in the skill; optional helpers are marked optional with a stated fallback.
- Script invocation commands state the working directory and use one consistent shape across `SKILL.md`, references, and the tools table.
- Any operational policy (for example evidence-sidecar persistence) is stated once and cross-referenced, not contradicted across sections.
- Resource Markdown links resolve, not just links from `SKILL.md`.
- `agents/openai.yaml`, if present, follows [agents-openai-yaml.md](agents-openai-yaml.md) and contains no skill instructions.
- The strict validator passes.
- No secrets, local tokens, or private payloads are embedded.
