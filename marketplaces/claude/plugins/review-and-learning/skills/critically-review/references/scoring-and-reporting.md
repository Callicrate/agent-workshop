# Scoring and Reporting

Use this file to standardize severity, confidence, and evidence quality.

## Severity

- **critical** — likely to overturn the document's main conclusion or create major decision risk
- **high** — materially weakens a core claim, method, recommendation, or implementation plan
- **medium** — important defect, but not necessarily thesis-breaking on its own
- **low** — real issue, but mostly clarifying or limited in decision impact
- **note** — worth recording, but minor

## Confidence

- **high** — strong textual grounding and strong evidence or clear logic
- **medium** — likely correct, but some uncertainty remains
- **low** — plausible concern, but under-evidenced or dependent on interpretation

## Evidence quality

- **A** — direct primary evidence or direct contradiction in the text or code
- **B** — strong secondary evidence or highly persuasive technical reasoning
- **C** — suggestive but incomplete support
- **D** — weak support, mostly heuristic concern

## Finding categories

Use a category plus a more specific issue type.

Suggested categories:

- factuality
- omission
- reasoning
- source-quality
- methodology
- data
- benchmark
- code
- architecture
- feasibility
- recommendation
- framing

## Reporting principles

- Lead with weaknesses. The reader mainly wants to know what is bad, hidden, wrong, misleading, or likely to cause a bad decision.
- Put the most decision-relevant issues first.
- Group related findings instead of repeating the same complaint in multiple places.
- Distinguish direct factual errors from misleading framing and from unsupported extrapolation.
- Include strengths so the review is credible and useful, but keep that section brief unless the user asks for a balanced summary.
- End with practical corrections, not just criticism.

## Verdict and action contract

Every final review must answer its decision question with one verdict:

- **proceed** — evidence supports the proposed decision and remaining unknowns are nonblocking
- **revise** — a bounded correction is required before proceeding
- **stop** — the proposal should not proceed on the available evidence
- **insufficient-evidence** — a blocking unknown prevents a responsible directional decision

Use the existing report structure:

- Write `overall_assessment.summary` as one line starting with exactly `proceed:`, `revise:`, `stop:`, or `insufficient-evidence:`, followed by a nonblank evidence-grounded rationale.
- Prefix each `open_questions` item with `[blocking]` when it could change the verdict or `[nonblocking]` when a reversible step or validation gate can resolve it.
- Write `overall_assessment.decision_impact` as exactly two nonblank lines: `Next action: <specific action>` followed by `Validation gate: <observable check that would justify the next state>`.

For autonomous or delegated reviews, choose the best reversible action supported by current evidence. Do not ask an unavailable user to resolve nonblocking unknowns. Review-only and advise modes recommend the action without authorizing it; patch-after-review remains bounded by the user's existing authorization.

## Finding repair fields

Every substantial finding should include:

- observed evidence: exact quote, source path, command output, runtime inventory, or other primary support
- decision impact: what would change if the finding is accepted
- repair shape: the smallest practical class of change that would address the issue
- verification needed: the check, test, inventory, source read, or runtime output that would prove the repair worked

For review-only requests, these fields are repair guidance, not permission to edit files. For patch-after-review work, use them as the implementation checklist.

When source materials are generated, truncated, or heuristic, label the evidence class and avoid corpus-level claims unless another source corroborates them.

## Minimal final-report sections

1. Scope and materials reviewed
2. Overall assessment
3. What holds up
4. Detailed findings (grouped by category)
5. Material omissions
6. Open questions
7. Decision and next action
