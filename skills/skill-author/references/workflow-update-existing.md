# Workflow: Update an Existing Skill

Step-by-step instructions for improving, fixing, or extending a skill that already exists.

## Prerequisites

- Read [skill-authoring-rules.md](skill-authoring-rules.md) first
- Know which skill to update and what changes are needed

## Steps

### 1. Read the Existing Skill Completely

Before changing anything:

1. Read `SKILL.md` end to end
2. List all files in the skill directory
3. Read `references/`, `scripts/`, `assets/`, `templates/`, and `agents/openai.yaml` files that are relevant to the change
4. Understand the current structure, conventions, and resource organization

### 2. Identify What to Change

Classify the update:

| Type | Examples |
|------|----------|
| **Content fix** | Incorrect instructions, outdated commands, wrong paths |
| **Discovery fix** | Skill not activating or triggering incorrectly - improve `description` with WHAT, WHEN, and explicit exclusions |
| **Expansion** | New workflow, additional reference, new script |
| **Restructure** | SKILL.md over 500 lines, content needs to move to `references/` |
| **Convention alignment** | Wrong frontmatter format, missing required section, stale references |
| **Codex app metadata** | Picker display name, default prompt, MCP dependency declaration, explicit-only policy |

### 3. Preserve What Works

- Do not rewrite correct content unnecessarily
- Keep existing structure unless it conflicts with the fix
- Maintain backward compatibility with existing resource references
- Keep the skill's voice and style consistent

### 4. Make the Changes

Apply the minimum change set that fully solves the issue.

**For content/discovery fixes:** Edit `SKILL.md` directly.

**For expansions:**
- Add new sections to `SKILL.md` if it stays under 500 lines
- Otherwise, create a new file in `references/` and link from `SKILL.md`
- Add new scripts/assets/templates only when they materially improve the skill
- Add or update `agents/openai.yaml` only for optional Codex app metadata, dependencies, default picker prompt, or invocation policy. Follow [agents-openai-yaml.md](agents-openai-yaml.md).

**For restructures:**
- Extract detailed material into `references/` files
- Replace inline content in `SKILL.md` with brief summaries + links
- Verify all relative paths still resolve after moving content

### 5. Context Optimization Pass

After making changes, critically review the skill for context efficiency. Every token the agent loads should help it execute the task.

**Cut non-actionable text:**
- Remove provenance notes, authorship history, design rationale, and commentary about *why* the skill was built rather than *how to use it*.
- Remove hedging - use imperative instructions.
- Remove diagrams unless the user explicitly asked for them or the diagram is already a maintained execution aid.

**Deduplicate across files:**
- If `SKILL.md` and a `references/` file say the same thing, keep detail in `references/` and replace inline content with a brief summary + link.

**Split by workflow:**
- If the skill now serves multiple distinct tasks, each task should have its own `references/workflow-*.md`. The main `SKILL.md` routes the agent to the right one.

**Right-size SKILL.md:**
- `SKILL.md` routes, orients, and handles simple cases. Detailed procedures belong in `references/`.
- Target: under 150 lines when reference files exist; under 500 lines for self-contained skills.

**Prefer executable examples:**
- For recurring mistakes, include a short bad/good example pair.
- Put long examples in `assets/` or `templates/`; keep only the routing rule in `SKILL.md`.
- If a task can be checked or scaffolded, add or update a script instead of adding more prose.

### 6. Check Standalone Behavior

Verify the skill still works when loaded independently:

1. Required context is either in `SKILL.md` or linked from it.
2. Any skill-specific exception to general instructions is stated in the relevant workflow or reference.
3. Every referenced resource path resolves.

### 7. Validate

Run through the same checklist used for new skills:

- [ ] Frontmatter valid with `name` and `description`
- [ ] `name` lowercase, hyphenated, ≤64 chars
- [ ] `description` states WHAT, WHEN, and explicit exclusions where needed
- [ ] `SKILL.md` under 500 lines
- [ ] Relative paths for all resource references
- [ ] `agents/openai.yaml`, if present, contains UI/runtime metadata only
- [ ] No secrets or credentials
- [ ] Every line helps the agent accomplish the task - no filler

Run the validator:

```powershell
c:/Users/user/collab/agents/.venv/Scripts/python.exe -B skills/skill-author/scripts/validate_skill.py <skill-dir> --strict
```

### 8. Report

Provide a concise summary:

1. Skill name
2. What was changed and why
3. Files modified, added, or removed
4. Context optimizations applied (deduplication, splits, cuts)
5. Whether `description` was updated
6. Validation result
7. Any follow-up recommendations
