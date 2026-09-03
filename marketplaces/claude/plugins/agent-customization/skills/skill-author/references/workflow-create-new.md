# Workflow: Create a New Skill

Step-by-step instructions for building a skill from scratch.

## Prerequisites

- Read [skill-authoring-rules.md](skill-authoring-rules.md) first
- Have a clear skill request: name, purpose, triggers, and any required resources

## Steps

### 1. Check for Duplicates

Search the `skills/` directory in this agents repo for an existing skill that already covers the domain. If one exists, follow [workflow-update-existing.md](workflow-update-existing.md) instead.

### 2. Choose a Name and Directory

- Lowercase, hyphenated, max 64 chars (e.g., `webapp-testing`)
- Create the directory: `skills/<skill-name>/`

### 3. Write `SKILL.md` from the Template

Copy [../templates/skill-minimal.md](../templates/skill-minimal.md) into the new directory as `SKILL.md`.

Replace all placeholders:

| Placeholder | What to write |
|-------------|---------------|
| `name` | Lowercase hyphenated skill name |
| `description` | WHEN to use it + WHAT it supports + explicit exclusions where needed (max 1024 chars) |
| `# Title` | Brief skill title |
| `## When to Use` items | Concrete trigger scenarios |
| `## When NOT to Use` items | Adjacent tasks handled elsewhere |
| Workflow steps | Imperative, specific instructions |

### 4. Write the Description for Discovery

The `description` is the **only** thing Copilot reads to decide whether to load the skill. A vague description means the skill never activates.

**Pattern:** `Use when asked to [triggers]. Supports [capabilities]. Do not trigger when [adjacent out-of-scope task].`

See [skill-authoring-rules.md - Description](skill-authoring-rules.md#description-contract) for good/poor examples.

### 5. Design Supporting Resources

Decide which resource folders the skill needs. Create only what materially improves the skill.

| Folder | Use when… |
|--------|-----------|
| `references/` | Detailed guides, decision trees, or patterns that would bloat SKILL.md beyond 500 lines |
| `scripts/` | Deterministic helpers that would be rewritten each time without a script |
| `assets/` | Static files consumed unchanged in output |
| `templates/` | Starter code the agent modifies per task |
| `agents/openai.yaml` | Optional Codex app UI metadata, default picker prompt, dependencies, or explicit-only policy |

See [skill-authoring-rules.md - Bundling Resources](skill-authoring-rules.md#bundling-resources) for the full resource-type table.

### 6. Decide Whether to Add Codex App Metadata

Add `agents/openai.yaml` only when the skill needs app picker metadata, a default picker prompt, declarative external tool dependencies, or explicit-only invocation policy.
Do not add it just because a skill exists.

If needed, start from [../templates/openai-agent-metadata.yaml](../templates/openai-agent-metadata.yaml) and follow [agents-openai-yaml.md](agents-openai-yaml.md).

### 7. Context Optimization Pass

Before validating, critically review the skill for context efficiency. Every token the agent loads should help it execute the task. Unnecessary content degrades agent performance by consuming context window budget.

Walk through each file and ask these questions:

**Cut non-actionable text:**
- Does every sentence help the agent accomplish the skill's task?
- Remove provenance notes ("extracted from X"), authorship history, design rationale, and commentary that explains *why* the skill was built rather than *how to use it*.
- Remove hedging language ("you might want to consider") - use imperative instructions instead.

**Deduplicate across files:**
- Is the same content repeated in `SKILL.md` and a `references/` file? Keep the detail in `references/` and replace the inline copy in `SKILL.md` with a brief summary + link.
- Are two reference files covering overlapping ground? Merge or split by distinct use case.

**Split by workflow:**
- Does the skill serve multiple distinct tasks (e.g., create vs update vs review)? If so, each task should have its own `references/workflow-*.md` file rather than one monolithic workflow in `SKILL.md`. The main file routes the agent to the right workflow.
- This keeps context focused - the agent loads only the workflow it needs.

**Right-size SKILL.md:**
- `SKILL.md` is the entry point, not the encyclopedia. It should route, orient, and handle simple cases.
- Detailed procedures, decision trees, and deep reference material belong in `references/`.
- Target: `SKILL.md` under 150 lines when the skill has reference files; under 500 lines for self-contained skills.

### 8. Review and Validate

Run through the validation checklist:

- [ ] `SKILL.md` has valid frontmatter with `name` and `description`
- [ ] `name` is lowercase, hyphenated, ≤64 chars
- [ ] `description` states WHAT, WHEN, and explicit exclusions where needed
- [ ] Skill directory is under `skills/` in the agents repo
- [ ] Skill works standalone when loaded independently
- [ ] `SKILL.md` under 500 lines; detail in `references/`
- [ ] Relative paths for all resource references
- [ ] `agents/openai.yaml` is present only when optional UI metadata, dependencies, or invocation policy are needed
- [ ] No secrets or credentials
- [ ] Scripts have usage docs and error handling (if any)
- [ ] Every line helps the agent accomplish the task - no provenance, history, or filler
- [ ] Anti-patterns section included if the skill covers a task where agents commonly fail

Run the skill validator:

```powershell
c:/Users/user/collab/agents/.venv/Scripts/python.exe -B skills/skill-author/scripts/validate_skill.py skills/<skill-name> --strict
```

### 9. Report

Provide a concise summary:

1. Skill name and directory
2. Files created
3. How `description` was shaped for discovery
4. Supporting resources added and why
5. Context optimizations applied (deduplication, splits, cuts)
6. Validation result
7. Any follow-up recommendations
