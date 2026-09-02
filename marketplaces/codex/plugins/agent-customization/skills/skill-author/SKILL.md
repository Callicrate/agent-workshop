---
name: skill-author
description: "Use when creating, updating, reviewing, or auditing Agent Skills, SKILL.md files, metadata, references, or scripts. Do not trigger for prompts, instructions, AGENTS.md, MCP config, or non-skill docs."
metadata:
  short-description: Author or audit Agent Skills.
---

# Skill Author

## When to Use

- Creating a new Agent Skill from scratch
- Updating an existing skill's SKILL.md, references, scripts, assets, templates, or `agents/openai.yaml`
- Reviewing a skill for structure, discovery quality, or duplication
- Translating retrospective evidence, repeated failures, or user corrections into skill guidance
- Auditing skill inventory, trigger coverage, overlap, or split/merge candidates
- Adding deterministic helpers that should replace stochastic workflow text

## When NOT to Use

- Editing standalone `.instructions.md`, `.prompt.md`, `.agent.md`, AGENTS.md, MCP docs, or other customization files when no skill artifact or reusable skill guidance is being changed; use `agent-prompt-engineering` for standalone prompt/instruction files
- General customization debugging that is not primarily a skill task

## Workflow

1. Work only under `skills/<skill-name>/` within this agents repo for skill source changes and read [references/skill-authoring-rules.md](references/skill-authoring-rules.md) first.
2. Triage artifact ownership with [references/artifact-ownership-triage.md](references/artifact-ownership-triage.md). Use this skill as primary only for skill artifacts; otherwise name the owning workflow and use this skill only to capture reusable skill guidance.
3. For a new skill, scaffold it with [scripts/scaffold_skill.py](scripts/scaffold_skill.py) and start from [templates/skill-minimal.md](templates/skill-minimal.md). For project-specific skills, read and apply [references/project-specific-skills.md](references/project-specific-skills.md) before scaffolding.
4. For an existing skill, follow the appropriate detailed workflow in [references/workflow-create-new.md](references/workflow-create-new.md), [references/workflow-update-existing.md](references/workflow-update-existing.md), or [references/workflow-review-existing.md](references/workflow-review-existing.md).
5. For retrospective or failure-slice work, use [references/workflow-evidence-backed-update.md](references/workflow-evidence-backed-update.md) before editing. Build a claim ledger that maps evidence quote, repeated failure, current coverage, proposed destination, confidence, and validation check.
6. For multi-file or high-volume skill work, use [references/workflow-large-batch-skill-work.md](references/workflow-large-batch-skill-work.md). Inventory first, produce bounded batches, validate after each batch, and keep resumable checkpoints.
7. For skill guidance that affects tools, MCPs, scripts, or agent-facing commands, run [references/public-surface-simplification.md](references/public-surface-simplification.md). Prefer one clear agent-facing abstraction over multiple implementation-specific operations, and validate shell scripts for LF line endings plus syntax before finishing.
8. For skill reviews or updates that mention missing deterministic support, run the checklist in [references/validation-assets-checklist.md](references/validation-assets-checklist.md).
9. When a skill mandates executable scripts, states an operational policy in more than one place, or is packaged for a plugin surface, apply [references/skill-packaging-contract.md](references/skill-packaging-contract.md) so the skill stays deterministically executable.
10. When a change may apply to more than one skill, apply [references/propagation-rules.md](references/propagation-rules.md).
10. For Codex app UI metadata, default picker prompts, external tool dependency declarations, or explicit-only invocation policy, build `agents/openai.yaml` from [references/agents-openai-yaml.md](references/agents-openai-yaml.md).
11. Prefer deterministic scripts, assets, templates, schemas, and compact good/bad examples over long procedural prose.
12. When a skill introduces shell scripts, validate line endings and shell syntax as part of the final checklist.
13. Finalize with [references/workflow-finalize-skill.md](references/workflow-finalize-skill.md) and validate the finished skill with [scripts/validate_skill.py](scripts/validate_skill.py).
## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/scaffold_skill.py](scripts/scaffold_skill.py) | You need a new skill skeleton with the shared-guidance mirror | Deterministic skill scaffold |
| [scripts/validate_skill.py](scripts/validate_skill.py) | You need a contract check before finishing | Structural validation of the skill |
| [scripts/inventory_skills.py](scripts/inventory_skills.py) | You need a skill inventory, line counts, resources, descriptions, trigger terms, or a discovery-budget estimate | JSON matrix plus a nonblocking modeled discovery-budget advisory |
| [scripts/audit_skill_overlap.py](scripts/audit_skill_overlap.py) | You need to review skill overlap, missing triggers, or split/merge candidates | JSON overlap and trigger-coverage findings, with discovery-budget advisory kept separate |
| [scripts/routing_probe.py](scripts/routing_probe.py) | Discovery/routing needs a bounded fresh-context regression check | Oracle-free plan plus value-free external-capture scoring/comparison |
| [templates/skill-minimal.md](templates/skill-minimal.md) | You need a minimal starter SKILL.md | Canonical base template |
| [templates/openai-agent-metadata.yaml](templates/openai-agent-metadata.yaml) | You need a starter `agents/openai.yaml` | Optional Codex app metadata template |
| [references/skill-authoring-rules.md](references/skill-authoring-rules.md) | You need the governing contract for skill design | Canonical authoring rules, including detailed description guidance |

## Troubleshooting

- **Rate limited or no response:** preserve completed files, reread the last verified artifact, shrink the next batch, and continue from the checkpoint instead of restarting blindly.
- **Length limit:** stop generating broad output, write or update the inventory and batch plan, then produce one bounded artifact group at a time.
- **Interrupted partial split:** reread every changed skill and resource file, list expected versus actual files, run strict validation and script smoke tests, then patch only the verified gaps.

## References

- [references/skill-authoring-rules.md](references/skill-authoring-rules.md) - structure, scope, duplication rules, and **detailed description metadata guidance**
- [references/artifact-ownership-triage.md](references/artifact-ownership-triage.md) - primary/supporting/out-of-scope routing for mixed customization work
- [references/workflow-create-new.md](references/workflow-create-new.md) - detailed workflow for new skills
- [references/workflow-update-existing.md](references/workflow-update-existing.md) - detailed workflow for updates
- [references/workflow-review-existing.md](references/workflow-review-existing.md) - detailed workflow for reviews
- [references/workflow-evidence-backed-update.md](references/workflow-evidence-backed-update.md) - convert retrospective slices and repeated failures into skill deltas
- [references/workflow-large-batch-skill-work.md](references/workflow-large-batch-skill-work.md) - resumable batching for large skill-generation work
- [references/public-surface-simplification.md](references/public-surface-simplification.md) - avoid agent-facing abstraction leaks in skill-backed tools
- [references/agents-openai-yaml.md](references/agents-openai-yaml.md) - optional Codex app UI metadata, dependency declaration, and invocation policy artifact
- [references/workflow-finalize-skill.md](references/workflow-finalize-skill.md) - final cleanup and validation workflow
- [references/propagation-rules.md](references/propagation-rules.md) - cross-skill propagation rules
- [references/validation-assets-checklist.md](references/validation-assets-checklist.md) - scripts, schemas, assets, templates, examples, and canonical root checks
- [references/skill-packaging-contract.md](references/skill-packaging-contract.md) - mandatory-script existence, explicit invocation paths, single-source policy, and plugin parity
- [references/project-specific-skills.md](references/project-specific-skills.md) - project-specific ownership gate, deterministic marker packaging, and scope boundary
