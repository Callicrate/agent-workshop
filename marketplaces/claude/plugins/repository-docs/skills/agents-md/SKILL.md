---
name: agents-md
description: "Use when creating/updating/reviewing/moving/splitting AGENTS.md guidance. Do not trigger for skills, prompts, other instructions, or docs."
metadata:
  short-description: Create or update AGENTS.md.
---

# Manage AGENTS.md

`AGENTS.md` gives coding agents scoped, current, repo-specific instructions. It is not a README, runbook, architecture doc, changelog, tutorial, or evidence log. Good guidance is evidence-backed, concise, scoped to the file's directory tree, and behavioral: it tells agents what to do differently in this repository.

## When to Use

- Create, update, review, split, move, or repair a repository `AGENTS.md`.
- Capture verified local commands, repo contracts, path ownership, or recurring agent mistakes.
- Convert repo evidence into rules that future agents can follow.

## When NOT to Use

- Standalone skills, prompts, global instructions, READMEs, architecture docs, changelogs, or runbooks.
- Broad documentation rewrites where `AGENTS.md` is not an explicit deliverable.
- Aspirational process guidance that is not supported by current repo evidence.

## Modes

- **patch**: small text fix, typo, stale path, or narrow rule change. Inspect the affected section and evidence only.
- **create**: no suitable `AGENTS.md` exists. Run the analyzer, sample source/config/docs, then draft the smallest useful file.
- **update**: existing file needs new or corrected guidance. Preserve valid rules, remove stale or duplicated material, and add only evidenced changes.
- **split/move**: scope boundaries are changing. Inspect existing root and nested files, then use [references/topology-and-handoff-guidance.md](references/topology-and-handoff-guidance.md).
- **review**: do not edit unless asked. Return findings by severity with evidence and residual risk.
- **exhaustive**: monorepos, contract-heavy repos, safety-sensitive guidance, operational handoffs, major topology changes, or unclear evidence.

## Workflow

1. Pick the mode and target scope. Read the nearest existing `AGENTS.md`, nested `AGENTS.md` files that may overlap, and `git status --short` when available.
2. For create, update, split/move, or exhaustive modes, run `python <skill-root>/scripts/analyze_project.py --repo-root <target-repo> --format json`. Use `--cwd` and explicit `--project-doc-fallback-filename` values when the target working scope needs them. Its audit reflects the documented per-directory candidate precedence, but treats readability, active fallback configuration, and fresh-run loading as unverified runtime facts.
3. For normal authoring details, use [references/normal-authoring.md](references/normal-authoring.md). Inspect only the sources needed for the claims you will make.
4. Draft from the smallest applicable template in [references/template-selection.md](references/template-selection.md). Prefer [assets/agentsmd-minimal.md](assets/agentsmd-minimal.md) unless the repo clearly needs a specialized template.
5. Keep evidence in working notes or the final summary. Add an evidence file only when the repo already owns one or the user asks.
6. Validate with `python <skill-root>/scripts/validate_agentsmd.py --repo-root <target-repo> --agents-file <target-agents-file> --mode <quick|standard|exhaustive> --intent <create|update|review|split|move>` and `python <skill-root>/scripts/semantic_check_agentsmd.py --repo-root <target-repo> --agents-file <target-agents-file>`. Use the matching intent for create, review, split, or move work; map patch and update work to `update`. Exhaustive controls validation depth, so pair it with the underlying task intent.
   The helpers fail closed before reading repo-owned inputs that escape the target root, are not bounded strict UTF-8 text, or alias another file through a hardlink. Analyzer truncation and excluded aliases appear in `scan_summary` and `uncertainty_items`; treat either as incomplete evidence.
7. These helpers check structure and reference existence, not guidance logic. Before finalizing, run the guidance-quality pass in [references/normal-authoring.md](references/normal-authoring.md#guidance-quality-pass) to confirm globs, paths, and links are anchored and conditioned against the tree they actually govern.

## Rule Quality Rubric

Keep a rule only when it is:

- **specific**: names actual paths, commands, contracts, or local conventions.
- **current**: supported by live files or user-provided current facts.
- **scoped**: applies to this `AGENTS.md` directory tree.
- **behavioral**: changes what an agent should do.
- **evidence-backed**: traceable to inspected repo evidence.
- **safe**: avoids secrets, unauthorized access, destructive commands, and production execution.
- **non-duplicative**: does not restate global instructions, language basics, or README content.
- **concise**: short enough to scan during normal coding work.

## Escalation Matrix

Load extra references only when the task needs them:

| Trigger | Read |
|---------|------|
| Tool, CLI, MCP, API, generated file, migration, schema, or public contract guidance | [references/contract-bearing-agentsmd.md](references/contract-bearing-agentsmd.md) |
| Split, move, nested scopes, stale topology, or ownership handoff | [references/topology-and-handoff-guidance.md](references/topology-and-handoff-guidance.md) |
| Live operations, degraded tools, manual dispatch, stop states, or environment resets | [references/degraded-tool-surface.md](references/degraded-tool-surface.md), [references/evidence-invalidation.md](references/evidence-invalidation.md) |
| Subagents or delegated review workflows | [references/subagent-coordination-patterns.md](references/subagent-coordination-patterns.md) |
| Python, TypeScript, other stack, Databricks/Spark, or ML-specific evidence prompts | [references/language-specific/python-guidance.md](references/language-specific/python-guidance.md), [references/language-specific/typescript-guidance.md](references/language-specific/typescript-guidance.md), [references/language-specific/other-stack-guidance.md](references/language-specific/other-stack-guidance.md), [references/domain-specific/databricks-spark.md](references/domain-specific/databricks-spark.md), [references/domain-specific/ml-projects.md](references/domain-specific/ml-projects.md) |
| Review-only or judgment-heavy audit | [references/manual-audit.md](references/manual-audit.md) |

## Final Response

For edit modes, include changed files, rules added/changed/removed, evidence sources sampled, validation commands and results, and residual risks.

For review mode, lead with findings ordered by severity. Each finding needs the affected `AGENTS.md` section or line, supporting repo evidence, why it matters, and the recommended fix. Then list open questions, test/validation gaps, and a brief summary only after findings.

## Deterministic Tools

| Tool | Use |
|------|-----|
| [scripts/analyze_project.py](scripts/analyze_project.py) | Stack, config, test, path, and suggestion inventory |
| [scripts/validate_agentsmd.py](scripts/validate_agentsmd.py) | Structural validation |
| [scripts/semantic_check_agentsmd.py](scripts/semantic_check_agentsmd.py) | Path, command, link, and example validation |
| [scripts/check_agentsmd_templates.py](scripts/check_agentsmd_templates.py) | Skill template and guidance hygiene |
| [scripts/run_agentsmd_fixture_checks.py](scripts/run_agentsmd_fixture_checks.py) | Known fixture behavior for validator changes |

## References

- [references/tool-contracts.md](references/tool-contracts.md) and [references/template-selection.md](references/template-selection.md) - helper CLI contracts, modes, and templates
- [assets/agentsmd-minimal.md](assets/agentsmd-minimal.md), [assets/agentsmd-contract-bearing.md](assets/agentsmd-contract-bearing.md), [assets/agentsmd-operational.md](assets/agentsmd-operational.md), [assets/agentsmd-full.md](assets/agentsmd-full.md) - starter templates
- [assets/example-validated-agentsmd.md](assets/example-validated-agentsmd.md), [assets/example-contract-bearing-agentsmd.md](assets/example-contract-bearing-agentsmd.md), [assets/example-operational-agentsmd.md](assets/example-operational-agentsmd.md), [assets/writing-tips.md](assets/writing-tips.md) - compact examples
