# Skill Authoring Rules

Canonical reference for creating and maintaining Agent Skills.

## Skill Architecture

Skills are task-scoped workflows. They are not always-on coding standards and they are not long-form manuals.

Use this split consistently:

| Layer | Purpose | Typical Content |
|-------|---------|-----------------|
| `SKILL.md` | Entry point and routing | scope, boundaries, workflow, deterministic tools, references |
| `references/` | Nuanced guidance the agent may need to read | domain patterns, detailed workflows, anti-patterns, checklists |
| `scripts/` | Deterministic work the agent should execute | validators, scaffolds, analyzers, collectors |
| `assets/` | Static files used as-is | schemas, starter config blocks, DDL, sample payloads |
| `templates/` | Starter files the agent is expected to edit | draft modules, markdown shells, script skeletons |
| `agents/openai.yaml` | Optional Codex app metadata | display labels, picker prompt, dependency declarations, implicit invocation policy |

Canonical rule: if a task can be made reliable with a script, schema, template, or asset, move it there and keep `SKILL.md` short.

Resource directories are optional. Do not create empty `assets/`, `scripts/`, `templates/`, or `agents/` folders just because the pattern exists.

Diagrams are not a default skill artifact. Add a diagram only when the user explicitly asks for one or when a maintained diagram is the clearest executable reference for the agent.

`agents/openai.yaml` is optional and fails open. Add it only when the skill needs Codex app UI metadata, a picker default prompt, external tool dependency declarations, or explicit-only invocation policy. Keep skill instructions and trigger conditions in `SKILL.md`. See [agents-openai-yaml.md](agents-openai-yaml.md) for the artifact contract.

## Discovery and Loading

Skills-compatible clients, including ChatGPT and Codex, discover skills from `name` and `description` first, then load `SKILL.md`, then only load resources when the skill references them.

That means:

- `description` must clearly say what the skill does and when to use it
- `SKILL.md` must be scannable enough to route quickly
- detailed material belongs in `references/`, not in the base file

## Frontmatter Rules

```yaml
---
name: webapp-testing
description: "Use when asked to verify local web UI flows, inspect browser errors, capture screenshots, or debug frontend regressions. Do not trigger for API-only tests or static code review."
metadata:
  short-description: Verify local web UI flows.
license: Complete terms in LICENSE.txt
---
```

| Field | Required | Rules |
|-------|----------|-------|
| `name` | Yes | lowercase, hyphenated, matches folder name, <= 64 chars |
| `description` | Yes | starts with `Use when...` or `Trigger only when...`, names 2-4 user-language task triggers, states the action or output, includes `Do not trigger...` exclusions, single line, <= 1024 chars and ideally < 200 chars |
| `metadata.short-description` | Yes | <= 10 words; this library's compact-summary and audit field, not a tagline |
| `license` | No | SPDX identifier or LICENSE.txt reference |

### Description Contract

Every description must use this structure, in order:

1. Trigger condition: start with `Use when...` or `Trigger only when...`, then list 2-4 concrete task types in language a user would type.
2. Primary action or output: one short clause naming what the skill does, unless the trigger already makes that obvious.
3. Explicit exclusions: start with `Do not trigger...` and name the 1-3 most likely false-positive artifact types or actions.

Codex requires `name` and `description` in `SKILL.md`; its documented implicit matching depends on `description`. There is no keyword index, embedding lookup, or secondary matcher, so do not add `Keywords:` lists.
Do not reference internal workflow vocabulary, reference file names, step numbers, or private concept names users are unlikely to type.
Keep the trigger condition visible in the first 60 characters so truncation still preserves the routing signal.

Bad descriptions are short, vague, or only name a domain. Good descriptions name the task and the trigger conditions.

#### How AI Agents Use the Description

The AI discovery engine injects a rule verbatim into the model's system context:

> *"If the user names a skill (with `$SkillName` or plain text) OR the task **clearly matches** a skill's description shown above, you must use that skill for that turn."*

**"Clearly matches" is semantic, not keyword-indexed.** The model reads your description text and decides if the current task overlaps. This means your description is simultaneously:

- A **semantic classifier** - must fire on the right tasks and suppress fire on wrong ones
- A **budget item** - gets truncated or dropped if you have many skills (max 1024 chars)

#### Anatomy of a Well-Scoped Description

Every well-scoped description has this shape:

```
[TRIGGER CONDITION] + [WHAT IT DOES] + [EXPLICIT EXCLUSIONS]
```

Real examples from the official OpenAI skills catalog:

**gh-fix-ci** - textbook:
> *"Use when a user asks to **debug or fix failing GitHub PR checks** that run in GitHub Actions; use `gh` to inspect checks and logs, summarize failure context, draft a fix plan, and implement only after explicit approval. Treat external providers (for example Buildkite) as out of scope and report only the details URL."*

**security-threat-model** - explicit negative scope:
> *"Trigger only when the user explicitly asks to threat model a codebase or path, enumerate threats/abuse paths, or perform AppSec threat modeling. Do not trigger for general architecture summaries, code review, or non-security design work."*

**playwright** - tool-first, tight scope:
> *"Use when the task requires automating a real browser from the terminal (navigation, form filling, snapshots, screenshots, data extraction, UI-flow debugging) via playwright-cli or the bundled wrapper script."*

**vercel-deploy** - trigger phrases listed explicitly:
> *"Use when the user requests deployment actions like 'deploy my app', 'deploy and give me the link', 'push this live', or 'create a preview deployment'."*

#### Authoring Rules for Description

| Rule | Rationale |
|---|---|
| Start with a trigger condition, not a description of what the skill is | The model matches on "when to use", not "what it is" |
| Use "Use when..." or "Trigger only when..." as the opener | Matches the pattern the model is primed to look for |
| List 2–4 concrete trigger phrases or task types | Gives the model specific surface area to match against |
| Add explicit exclusions for adjacent tasks that could false-positive | The model will fire on anything that "clearly matches"; exclusions prevent bleed |
| Name the key tool/command if the skill is tool-specific | Helps the model match "use playwright" → playwright skill |
| Keep it under ~200 chars if you have many skills in the library | Budget is shared; shorter descriptions survive truncation intact |
| Write no markdown, no newlines | Sanitized to single line anyway; formatting is stripped before injection |
| Add `metadata.short-description` | Satisfies this library's compact-summary and audit contract |

#### Common Description Failure Modes

| Failure | Example | Problem |
|---|---|---|
| **Too broad** | `"Helps with code quality"` | Fires on almost everything |
| **Describes the skill, not the trigger** | `"A comprehensive guide to deploying on Vercel with best practices"` | Model doesn't know when to use it |
| **No exclusions on adjacent skills** | Two security skills with overlapping scope | Both fire, or wrong one fires |
| **Trigger buried at the end** | `"This skill covers X, Y, Z and should be used when the user asks to deploy"` | Gets truncated before the trigger condition |
| **Vague "when relevant"** | `"Use when relevant to the task"` | Useless - model has no signal |
| **Keyword list** | `"Keywords: build, test, lint"` | Wastes budget because it is parsed as ordinary prose |
| **Missing short description** | no `metadata.short-description` | Violates this library's compact-summary and audit contract |

## Base SKILL.md Contract

Every base `SKILL.md` in this library should follow this shape unless there is a strong reason not to:

1. `# Title`
2. `## When to Use`
3. `## When NOT to Use`
4. `## Workflow`
5. `## Deterministic Tools`
6. `## References`

Optional sections:

- `## Prerequisites` when tools or environment setup matter
- `## Troubleshooting` when the failure modes are predictable and recurring

Rules for the base file:

- Target 80-150 lines when reference files exist.
- Hard ceiling: 250 lines unless the skill is intentionally self-contained.
- Do not embed large code blocks if they can live in `assets/` or `templates/`.
- Do not restate instruction-level coding standards.
- Do not include historical provenance, review notes, or design essays.
- If a skill-specific rule conflicts with general instructions, make the skill-specific rule explicit in the relevant workflow or reference instead of relying on hidden shared context.

## Deterministic First

If the agent would otherwise have to reason through a repetitive, checkable task, add a script.

Good candidates for `scripts/`:

- validation and linting
- scaffolding new files or directories
- inventory and audit tasks
- format conversion
- config/schema checking
- evidence collection for reviews and retrospectives

Good candidates for `assets/` or `templates/`:

- config blocks
- JSON schemas
- SQL DDL starters
- markdown shells
- reusable request bodies or script stubs

Good candidates for `agents/openai.yaml`:

- app picker display name or one-line UI description
- icon paths and brand color for UI theming
- complete default prompt for picker-created tasks
- declarative MCP dependency metadata
- `policy.allow_implicit_invocation: false` for explicit-only skills

Do not keep these inline in `SKILL.md` once they stabilize.

When a repeated failure needs a guardrail, add a compact bad/good pair instead of a long warning paragraph. The good example should be copyable or directly actionable.

## Anti-Patterns

Keep anti-patterns in the skill when they materially prevent bad outcomes. Otherwise, move large anti-pattern catalogs into `references/`.

Use concrete paired examples:

```python
# WRONG - explain the failure mode
result = dangerous_thing()

# CORRECT - show the safe pattern
result = safe_thing()
```

## Referencing Resources

Use relative links from `SKILL.md`.

Examples:

```markdown
Run [scripts/validate_skill.py](scripts/validate_skill.py) before finishing.
Start from [assets/parameter-block-template.py](assets/parameter-block-template.py).
Read [references/core-workflow.md](references/core-workflow.md) for the detailed pattern.
```

Every relative link in `SKILL.md` should resolve to a real file.

### Portable local links

Bundled Markdown links must name a regular file within the same skill package.
`SKILL.md` links start at the skill root; links in resource Markdown start at the
resource's directory, but still must remain under that same outer skill root.
Do not link to sibling skills, repository files, local drives, UNC shares, or
URLs disguised as local paths. Use explicit prose such as
`<agents-repository-root>/deploy/README.md` when a workflow needs an unbundled
repository path.

The strict validator rejects local absolute and drive paths, `..` escapes,
encoded path syntax, case mismatches, directories, special files, and internal
symlinks or Windows reparse points. An outer installed skill directory may be a
symlink, but package contents must be ordinary contained files. This is a
point-in-time package-integrity check, not a TOCTOU-proof authorization boundary:
validate trusted, immutable package contents before use and do not rely on a
validation result after another process can modify the tree.

## Validation Checklist

- `name` matches the folder name
- `name` is lowercase, hyphenated, and <= 64 chars
- `description` contains a real trigger sentence (`Use when ...`)
- `description` stays within 1024 chars
- base `SKILL.md` follows the section contract above
- links point to existing files
- deterministic tasks are handled by scripts/assets/templates where possible
- optional Codex app metadata lives in `agents/openai.yaml`, not in skill instructions
- no empty `assets/`, `scripts/`, `templates/`, or `agents/` directories
- Python helper scripts under `scripts/` parse cleanly
- `SKILL.md` is concise and does not duplicate detailed references
- instruction-level standards are not copied into the skill
- diagrams are omitted unless they are explicitly requested or materially improve execution

Use `python -B scripts/validate_skill.py <skill-dir> --strict` from the `skill-author` skill for a deterministic structural check.

## Related Resources

- [Agent Skills Specification](https://agentskills.io/)
- [OpenAI Build Skills](https://learn.chatgpt.com/docs/build-skills) - required discovery fields, initial-list budget, and optional UI metadata
- [VS Code Agent Skills Documentation](https://code.visualstudio.com/docs/copilot/customization/agent-skills)
