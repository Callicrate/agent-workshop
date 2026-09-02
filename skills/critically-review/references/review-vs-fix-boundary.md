# Review Versus Fix Boundary

Use this reference to choose the mode of work before changing files or presenting recommendations.

## Mode Selection

| Mode | Trigger | Primary output | Stop condition |
|------|---------|----------------|----------------|
| `review-only` | User asks for a review, critique, audit, skeptical read, or full report | Findings, evidence, decision impact, repair shape, and verification needed | Stop after the report unless the user asks for edits |
| `advise` | User asks what to do, how to fix, or whether an approach is sound | Findings plus implementation-ready repair plan | Stop after advice unless the user authorizes changes |
| `patch-after-review` | User says fix it, implement, patch, address findings, or equivalent | Apply changes, validate, and summarize | Stop after validation or a real blocker |
| `first-look` | User asks for current state, full new report, do it again, or as-if-fresh review | Fresh findings built from current materials | Draft without using prior report as the outline |
| `delta-review` | User asks what changed since a prior review or release | Comparison against prior baseline | Stop after delta findings and residual risk |
| `implementation-readiness` | User asks whether a proposal is ready to build, deploy, publish, or commit | Blocking issues, readiness checklist, and required validation | Stop after go/no-go recommendation |
| `public-surface-review` | User focuses on tool/API/prompt/agent-facing concepts, naming, or simplification | Live surface inventory and abstraction-leak findings | Stop after inventory-backed recommendations |
| `evidence-slice-review` | User gives a transcript, generated evidence pack, model review, or truncated case slice | Bounded findings with source-class and truncation caveats | Stop after scoped claims unless additional sources are available |

Default rule: a plain "review" request means findings and advice, not silent edits. Switch to implementation only when the user asks for fixes or when a file is obviously broken and the user has authorized repair work in the same request.

## Fresh Review Mode

When the user asks for a full new report, first-look review, current-state review, or "do it again":

1. Rebuild the claim ledger from current source materials.
2. Rerun live-surface or source checks that materially affect findings.
3. Draft findings without using previous findings as the outline.
4. Consult prior reports only after drafting, and only to check missed regressions or resolved issues.
5. State that the report reflects current materials, not a delta from the previous review.

Do not edit the previous report in place unless the user explicitly asks for an updated existing artifact.

## Optional Artifact Fallback

Do not assume helper artifacts exist. Evidence packs, rendered reports, `signals.json`, selected-session lists, prior-lesson files, or generated inventories are optional unless the user or repository contract says they are required.

When an expected helper artifact is missing:

- proceed from primary source materials that are available
- state the missing artifact as a limitation
- avoid claims that depend on the missing artifact
- create a new helper artifact only when the user asked for implementation, or when the user or repository contract authorizes a named workspace that the review requires

Generated or heuristic artifacts are leads, not proof. Anchor findings to primary source text, repository files, command output, runtime output, or direct user quotes whenever possible.

## Review-To-Repair Output

Each substantial finding should leave a repair path, even in review-only mode:

- observed evidence: the quote, file, command output, runtime inventory, or source line that supports the finding
- decision impact: what decision would change if the finding is accepted
- repair shape: the smallest plausible class of fix, not necessarily a full patch
- verification needed: the check that would prove the repair worked

Keep this separate from implementation. A repair shape can be specific enough to implement later without making the review itself mutate the system.
