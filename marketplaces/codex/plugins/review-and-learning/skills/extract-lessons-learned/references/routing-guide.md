# Routing Guide

Apply the first matching rule. Every kept lesson should land in exactly one destination.

For mixed packets, generated retrospective artifacts, self-referential skill reviews, or evidence slices, apply [content-first-routing.md](content-first-routing.md) first. Route by the action that should change, not by the artifact family that contained the observation.

## 1. Discard incident-only notes

Discard the item if it does not produce reusable guidance.

- "A meeting went well" -> discard
- "We had to restart the cluster on Tuesday" -> discard unless it reveals a repeatable failure pattern

## 2. Route project-specific context to the project's `AGENTS.md`

Use this when the lesson explains how one repo works.

- architecture and data flow
- naming conventions
- deployment targets and approval gates
- schema or table conventions that matter only in this project

Examples:

- "This repo's CI requires manual approval before prod" -> project's `AGENTS.md`
- "The enrichment table partitions by ingestion_date, not event_date" -> project's `AGENTS.md`

## 3. Route multi-step workflows to a skill reference

Use this when the lesson is a repeatable domain workflow with more than one step.

- Existing skill covers it -> add or update that skill's `references/` content
- No skill covers it -> propose an exact candidate path of the form
  `skills/<candidate>/SKILL.md` in the routing table, replacing `<candidate>`
  with a concrete skill name such as `skills/release-evidence/SKILL.md`

Before proposing a candidate path, audit nearby skills and mark why existing
ownership is absent or insufficient. The literal text `new skill candidate` and
an unresolved `skills/<candidate>/SKILL.md` are placeholders, not valid final
destinations. Do not create new meta-skills for behavior an existing skill can
absorb.

Examples:

- "Validate the bundle, check the run, then verify the dashboard" -> `databricks-asset-bundles` skill reference
- "Add column, backfill nulls, update downstream views" -> `databricks-spark-etl` skill reference

## 4. Route broad single rules to an `instructions/` file

Use this when the lesson is a reusable rule or anti-pattern, not a workflow.

| Lesson domain | Destination |
|---|---|
| Python style, logging, packaging | `python-general.instructions.md` |
| PySpark schemas, Delta tables, UDFs | `pyspark.schema.instructions.md` |
| Databricks workspace, clusters, jobs | `databricks-general.instructions.md` |
| DAB config, bundle deployment | `databricks-asset-bundle.instructions.md` |
| SQL patterns, query optimization | `sql.instructions.md` |
| YAML formatting and gotchas | `yaml.instructions.md` |
| Markdown structure and links | `markdown.instructions.md` |
| PowerShell scripting | `powershell.instructions.md` |
| GPU or ML infrastructure | `ml-gpu.instructions.md` |

Examples:

- "Never use `print()` for logging in production code" -> `python-general.instructions.md`
- "Always use `mergeSchema` for additive nullable columns" -> `pyspark.schema.instructions.md`

## Output Rules

- Use concrete destination paths, not placeholders.
- Use `discard` when the lesson should not be retained.
- Keep `discard` in the routing table with evidence and a short reason, especially for empty sessions, prompt-only starts, partial failures, unsupported claims, and incident-only facts.
- Do not create a new skill inline during the retrospective; route it to a concrete proposed `skills/<candidate>/SKILL.md` path with `<candidate>` replaced.
- Include evidence, confidence, and limitations or counter-evidence for every route.
- Record primary roots as unique `<root-family-id>@<artifact-reference>` entries and make the integer count match; prior synthesis is not primary evidence.
- Use the disposition-compatible `Action Kind` and matching action prefix defined in [output-template.md](output-template.md).
- Add implementation-audit rows for `apply`, `already-covered`, `supersede`, and `defer`; omit them for `monitor` and `discard`.
