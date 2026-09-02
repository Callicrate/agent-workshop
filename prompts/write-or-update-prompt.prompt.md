---
name: crud-prompt-π
description: Creates or updates a reusable prompt file in the agents repo prompts/ directory. Fully autonomous after the task description is provided.
agent: agent
model: Claude Opus 4.6
tools: [agent,edit,execute,vscode/memory,read,search,todo]
argument-hint: "Desired prompt filename, purpose, required inputs, and whether it wraps an existing skill or is standalone"
---

# Write or Update a Prompt

Load the `skill-author` skill first for prompt-file conventions. This prompt adds only the local library rules for the `prompts/` directory.

## Task Description

Provide the following input.

- Desired filename, such as `review-api.prompt.md`
- Describe what the prompt should accomplish.
- List required inputs, outputs, constraints, and acceptance criteria.
- Say whether the prompt is a wrapper around an existing skill or a standalone workflow.

## Workflow

1. Create a TODO list and search the local `prompts/` directory for an existing prompt with the same intent.
2. If the prompt already exists, update it in place. If not, create a new one with a `kebab-case.prompt.md` filename.
3. Start from [../userdata/prompt-frontmatter-template.yaml](../userdata/prompt-frontmatter-template.yaml) and keep the frontmatter aligned with the actual tool needs.
4. Keep the body easy to digest: short mission, clear workflow, explicit deliverables, and no duplicated skill guidance when a skill already owns the workflow.
5. Move reusable structure or verbose examples into skill `references/`, `assets/`, or `userdata/` instead of embedding them inline.
6. Verify the final prompt has valid frontmatter, no placeholders, and no stale references.

## Guardrails

- Prompt files in this folder are reusable wrappers or workflows, not encyclopedic manuals.
- When a skill already exists for the task, prefer delegation to that skill over repeating its detailed rules.
- If the prompt needs examples, keep only the minimum necessary examples inline.
