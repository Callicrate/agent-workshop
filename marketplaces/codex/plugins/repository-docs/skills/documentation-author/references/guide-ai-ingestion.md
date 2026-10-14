# AI-Ingestion Documentation Workflow

Use this guide when the user says the document is for agents, prompts, model ingestion, instruction summaries, retrieval, or another machine reader.

## Output Contract

- Optimize for compact structure, stable labels, and unambiguous instructions.
- Prefer imperative instructions and explicit start state when an agent will execute the doc.
- Prefer lists, tables, short imperative rules, and explicit source paths over narrative exposition.
- Keep labels stable across revisions so downstream prompts and retrieval chunks can target them.
- Preserve source-of-truth boundaries. Link to canonical contracts instead of duplicating schema, policy, or API definitions.
- Include examples only when they remove ambiguity; keep them minimal and representative.
- Keep prerequisites visible. Do not bury required files, credentials, shells, or working directories in prose.
- Use [guide-agent-ops-docs.md](guide-agent-ops-docs.md) when the document defines agent roles, directives, peer files, or an execution plane.

## Avoid

- decorative prose
- marketing or onboarding narrative
- duplicated contract definitions
- merged concepts that should remain separate source-of-truth sections
- headings that change wording without changing meaning
- hidden IDE assumptions
- meta labels such as review packet, AI commentary, generated summary, or analysis artifact unless required by schema
