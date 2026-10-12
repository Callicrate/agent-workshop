# Review Checklist

Run one final pass before finishing.

- [ ] Scope matches the request and the audit output. No extra docs were created just because a template suggested them.
- [ ] Every command, path, heading, and link was verified against the repo.
- [ ] For folder-contract docs, the actual tree was audited after moves or renames and parent/child docs were updated together.
- [ ] The document states only facts supported by source, config, or existing docs.
- [ ] Generated or adversarial Markdown was not treated as authoritative without code, config, notebook, or output confirmation.
- [ ] Previous requested documentation edits still exist after adjacent implementation or MCP changes.
- [ ] Claims about results, measurements, incidents, exploitability, customer impact, or implementation status are backed by source evidence rather than inferred from a title or plan.
- [ ] The document does not duplicate content that already belongs in another doc, instruction, or skill.
- [ ] Existing validators, schemas, CLIs, table lists, and source-of-truth modules were scanned before inventing parallel documentation concepts.
- [ ] SDK, API, or architecture guidance for a named external system was checked against a canonical source and cites that source.
- [ ] AI-ingestion docs use compact structure, stable labels, and unambiguous instructions instead of human-friendly narrative.
- [ ] Agent-operation docs state start state, stop conditions, shared execution plane, status/directive files, peer-file trust boundaries, and avoid hidden editor assumptions.
- [ ] Contract definitions preserve source-of-truth boundaries and link to canonical contracts instead of duplicating them.
- [ ] Suite, MCP, or platform specs state scope, non-goals, ownership, and handoffs, and do not leak requirements from sibling components.
- [ ] Any AGENTS.md work was routed to `agents-md` instead of extending this skill.
- [ ] `README.md`, if touched, has accurate setup and usage guidance with no placeholder sections.
- [ ] Install docs, if touched, state target environment, prerequisites, install commands, first run, verification, expected output, troubleshooting, and unsupported paths.
- [ ] Access runbooks, if touched, state interfaces, access state, reproduction commands, observed behavior, read/write capability, peer docs, artifacts, and do-not-edit sidecars.
- [ ] Notebook docs, if touched, were based on executable cells, widgets, SQL, configs, and outputs; existing explanatory Markdown was preserved unless proven wrong; JSON/summary validation was run after edits.
- [ ] `CHANGELOG.md` or release notes, if touched, follow the repo's release convention, use a verified release boundary, and only include user-facing or operator-facing changes.
- [ ] Diagrams, if touched, were explicitly in scope and follow [ascii-art-standards.md](ascii-art-standards.md).
- [ ] User-provided product names, team terms, and preferred labels are preserved unless the source explicitly contradicts them.
- [ ] Terminology stays consistent across all touched docs.
- [ ] Labels do not imply blame, complaint, or negative judgment unless that connotation is intended by the user or source.
- [ ] Security article drafts use original public URLs where possible, quote minimally, and keep partner-sensitive wording neutral.
- [ ] Draft research articles label planned work, hypotheses, and open questions distinctly from completed findings.
- [ ] Package READMEs lead with workflow value and the smallest useful usage path before advanced internals.
- [ ] Demo-only flags, filters, and scaffolding are labeled as test/demo controls rather than future product or agent interfaces.
- [ ] Human-facing docs do not contain synthetic meta labels such as review packet, AI commentary, generated summary, or analysis artifact unless required.
- [ ] If the user corrected a phrase, the corrected wording is used literally in follow-up edits.
- [ ] No TODOs, blanks, speculative future-state text, or broken internal links remain.
