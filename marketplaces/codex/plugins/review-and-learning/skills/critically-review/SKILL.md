---
name: critically-review
description: "Use when skeptically reviewing reports, proposals, design docs, security articles, or technical surfaces; produces findings. Do not trigger for light editing, summaries, or code review."
metadata:
  short-description: Skeptically review documents.
---

# Critically Review

This is a weakness-first review skill. The main job is to find what is bad, hidden, wrong, misleading, or likely to break a decision.

Document what holds up so the review stays calibrated, but spend most of the effort on weaknesses, omissions, distorted framing, and claims that do not survive scrutiny.

Do not manufacture findings. If the artifact is strong after checking expected failure modes, say so and calibrate severity clearly.

## When to Use

- Reviewing a report, memo, proposal, white paper, design note, or thesis that may contain weak evidence, hidden assumptions, or bad reasoning
- Stress-testing a document before it influences a decision, investment, policy, or implementation plan
- Producing a structured critique with exact quotes, evidence, severity, confidence, and concrete revisions, especially when the user wants to know where the artifact is weak, misleading, or hiding risk
- Reviewing customer-facing security articles or partner-sensitive reports for source support, quote handling, novelty, recommendations, and tone risk
- Reviewing technical artifacts, architecture docs, MCP or tool surfaces, prompts, skills, API docs, or repository guidance for mismatch with the live implementation
- Reviewing a working tree, diff, or change set before commit when the user wants a clear explanation of what changed, why it changed, risks, validation, and suggested commit grouping

## When NOT to Use

- Summarizing or lightly editing a document without a skeptical review deliverable
- Routine source-code review when the request is about code defects rather than document quality
- Unstructured opinion writing that does not need evidence-backed findings

## Workflow

Do not manufacture findings. If the artifact is strong after checking the expected failure modes, say so and calibrate remaining risks clearly.


1. Start with [references/review-method.md](references/review-method.md), then classify the artifact, the decision it influences, the requested depth, and whether this is review-only, advise, patch-after-review, or fresh re-review. For working-tree or diff review, use [references/change-review.md](references/change-review.md). For phase boundaries, use [references/review-vs-fix-boundary.md](references/review-vs-fix-boundary.md).
   When the user pastes another review, asks to reconsider a finding or score, or supplies competing reviews, use [references/external-review-adjudication.md](references/external-review-adjudication.md) before changing findings or conclusions.
2. Before criticizing details, maintain an explicit review plan, TODO list, claim ledger, and evidence log. The ledger must retain each exact claim, location, supporting evidence, disconfirming or missing evidence, centrality, and verification status.
   Use [scripts/init_review.py](scripts/init_review.py) and the planning assets only when the user or repository contract authorizes a durable review workspace and it is required for that review, including authorized multi-pass persistence. Initialize only the explicitly named workspace, without `--force`.
   For review-only, dirty, read-only, ephemeral, or one-report work, keep the same ledger in memory and reflect its material entries in the findings or report. Do not create a workspace merely to satisfy this step; a request for one report authorizes only that report file.
3. For technical artifacts, verify the live surface before judging the claim. Inventory registered tools, exported commands, routes, schemas, files, scripts, prompts, generated artifacts, or runtime outputs when available. Compare the stated design against that inventory with [references/technical-surface-review.md](references/technical-surface-review.md) and, when useful, [scripts/surface_snapshot.py](scripts/surface_snapshot.py).
4. Run a fictional-component audit. List every named system, handoff target, tool family, artifact, path, external dependency, and required input. Mark each as observed, proposed, absent, or ambiguous. Treat absent-but-presented-as-real components as high-confidence factuality or architecture findings.
5. Run isolated passes for facts, omissions, reasoning, public-surface complexity, and technical feasibility. Load [references/fact-checking.md](references/fact-checking.md), [references/omissions-and-fallacies.md](references/omissions-and-fallacies.md), [references/data-code-and-logic.md](references/data-code-and-logic.md), and [references/domain-playbooks.md](references/domain-playbooks.md) only when they fit the artifact. For partner-sensitive security articles, use the security article playbook in [references/domain-playbooks.md](references/domain-playbooks.md).
6. Record only evidence-backed findings. Quote the exact text, separate unsupported from false, and prioritize the places where the artifact is bad, hidden, wrong, misleading, or materially incomplete. For evidence-slice reviews, treat truncated or generated source text as partial slice evidence and avoid claims that require unavailable full context unless corroborated by another source. For high-impact recommendations, use [references/high-impact-claim-ledger.md](references/high-impact-claim-ledger.md) before promoting the claim.
7. When using structured JSON outputs, validate with [scripts/validate_findings.py](scripts/validate_findings.py), merge pass outputs with [scripts/merge_findings.py](scripts/merge_findings.py) if needed, and render the final report with [scripts/render_report.py](scripts/render_report.py).
8. Finish with a decision, not an evidence dump. Answer the review's decision question with one verdict: `proceed`, `revise`, `stop`, or `insufficient-evidence`. Put `<verdict>: <evidence-grounded rationale>` on one line in `overall_assessment.summary`. Classify every `open_questions` entry as `[blocking]` or `[nonblocking]`. Put exactly two nonblank lines in `overall_assessment.decision_impact`: `Next action: <action>` followed by `Validation gate: <check that would justify the next state>`. In autonomous or delegated work, make the best reversible evidence-backed decision available; do not turn nonblocking unknowns into questions for an unavailable user. A review-only or advise verdict recommends what should happen but does not authorize implementation.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/init_review.py](scripts/init_review.py) | You need a repeatable review workspace | Planning files and starter report artifacts |
| [scripts/surface_snapshot.py](scripts/surface_snapshot.py) | You need before/after counts or a live implementation inventory | JSON inventory of common repo, skill, prompt, CLI, route, package, and MCP/tool surfaces |
| [scripts/validate_findings.py](scripts/validate_findings.py) | You have structured findings or a standalone findings list and need a contract check | Bounded schema-validation result |
| [scripts/merge_findings.py](scripts/merge_findings.py) | You ran multiple independent passes | Deterministic merged findings that preserve material variants and provenance |
| [scripts/render_report.py](scripts/render_report.py) | You need the final Markdown review | Schema-validated Markdown with untrusted values rendered as inert literals |
| [assets/findings-template.json](assets/findings-template.json) | You need a canonical finding shape | Stable report structure |

### Tool Caveats

- `init_review.py` defaults to non-destructive mode: existing files are skipped. On Windows, pass `--force` to replace verified safe existing files. On other platforms, forced replacement fails closed. If `--title` or `--review-mode` are supplied without `--force` and findings.json already exists, a warning is emitted to stderr. Review modes include `triage`, `standard`, `exhaustive`, `technical-deep-dive`, `first-look`, `delta-review`, `implementation-readiness`, `public-surface-review`, and `evidence-slice-review`.
- `surface_snapshot.py` is heuristic evidence collection, not a complete parser. Treat its JSON as a starting inventory and verify high-impact items against source or runtime output.
- `validate_findings.py` validates against `assets/review-report.schema.json` and `assets/finding.schema.json` using the `referencing` library. It bounds bytes, JSON depth, nodes, strings, reports, findings, and evidence before accepting a report or findings list. Requires `jsonschema>=4.18` and `referencing>=0.30` (declared in PEP 723 script metadata).
- `merge_findings.py` validates every input, preserves findings that differ in severity, confidence, analysis, or evidence, and records safe origin/source IDs. It deduplicates only byte-identical canonical findings and never uses severity or confidence to choose between variants. `research_performed` is true only when at least one full input report says so.
- `merge_findings.py`, `render_report.py`, and `surface_snapshot.py` create outputs only when the target does not exist. On Windows, `--force` safely replaces only a verified unchanged regular file: identity, metadata, and a transient bounded content comparison are rechecked from the final locked handle; target growth, shrink, rewrites (including timestamp-restored same-size rewrites), replacement, aliases, links, or reparse paths fail closed. No target digest or content is persisted or emitted. Elsewhere it fails closed. All four writers reject input/output aliases, symlinks, hardlinks, and reparse-point output paths. See [references/structured-helper-contract.md](references/structured-helper-contract.md) for the bounded error and rendering contract.

## References

- [references/review-method.md](references/review-method.md) - end-to-end review method and iteration rules
- [references/review-vs-fix-boundary.md](references/review-vs-fix-boundary.md) - review-only, advise, patch-after-review, and re-review mode boundaries
- [references/technical-surface-review.md](references/technical-surface-review.md) - live implementation, fictional-component, and public-surface checks
- [references/fact-checking.md](references/fact-checking.md) - claim verification and source-quality guidance
- [references/omissions-and-fallacies.md](references/omissions-and-fallacies.md) - omission patterns, framing problems, and reasoning defects
- [references/data-code-and-logic.md](references/data-code-and-logic.md) - technical, data, and feasibility review lenses
- [references/subagent-briefs.md](references/subagent-briefs.md) - independent review-pass prompts
- [references/scoring-and-reporting.md](references/scoring-and-reporting.md) - severity, confidence, and reporting rules
- [references/high-impact-claim-ledger.md](references/high-impact-claim-ledger.md) - evidence-for, evidence-against, confidence, and destination checks
- [references/domain-playbooks.md](references/domain-playbooks.md) - genre-specific review priorities
- [references/change-review.md](references/change-review.md) - working-tree and diff explanation before commit
- [references/external-review-adjudication.md](references/external-review-adjudication.md) - verify pasted or competing review claims before revising findings or scores
- [references/structured-helper-contract.md](references/structured-helper-contract.md) - bounded validation, merge, output, and safe-rendering contract
