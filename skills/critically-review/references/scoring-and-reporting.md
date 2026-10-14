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
- Include evidence-backed strengths and the boundaries under which they hold; explain what a repair should preserve. Keep this concise, not ceremonial. Use [understanding-and-calibration.md](understanding-and-calibration.md) for mechanism, consequence, counterevidence, and calibration checks.
- End with practical corrections, not just criticism.

## Reader-facing format

The default deliverable is readable Markdown, not serialized data. Use real headings, paragraphs, bullets, quote-specific preformatted blocks for exact evidence, and human-readable labels. Lead with the verdict; give findings descriptive titles. Omit empty optional sections. Keep evidence and source references readable alongside the finding.

Reserve code fences for actual code or output excerpts, never entire report objects, arrays, or ordinary prose. Do not expose JSON field names as the outline.

Use the [report template](../assets/review-report-template.md) for prose reviews. JSON is optional working/interchange data; a request for one Markdown report does not authorize an extra JSON file. If structured findings already exist, from the review workspace run `python -B <skill-root>/scripts/render_report.py --report findings.json --output report.md`.

The renderer escapes untrusted markup while generating normal Markdown. Do not paste its input JSON or CLI status JSON into the report. Read the resulting file before delivery: verdict, finding titles, evidence, and next action must be scannable without decoding objects or escaped newlines.

**Wrong:** A heading followed by `{"analysis": "The claim lacks evidence."}` in a fence.

**Better:**

### F-001: The attribution exceeds the evidence

**Severity:** high | **Confidence:** high

> The operator was responsible.

The cited account name is circumstantial evidence, not proof of ownership.

**Recommended fix:** Qualify attribution until provider records corroborate it.

## Verdict and action contract

Every final review must answer its decision question with one verdict:

- **proceed** — evidence supports the proposed decision and remaining unknowns are nonblocking
- **revise** — a bounded correction is required before proceeding
- **stop** — the proposal should not proceed on the available evidence
- **insufficient-evidence** — a blocking unknown prevents a responsible directional decision

In Markdown, put the verdict and rationale in a normal paragraph (bold labels are fine), classify open questions as blocking or nonblocking, and end with separate **Next action** and **Validation gate** paragraphs. Do not serialize them.

Only when producing structured JSON, use the existing schema fields:

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

1. Scope and materials reviewed, including coverage limits
2. Overall assessment
3. How it works and what it depends on (compact plain-language model)
4. What holds up and should be preserved
5. Detailed findings (most decision-relevant first; group shared causes)
6. Material omissions and classified open questions
7. Decision and next action

Scale to the requested depth; omit empty headings. Keep the detailed claim ledger out of the reader's way unless it is requested or needed to resolve disputed evidence. For structured JSON, use the existing field mapping in [understanding-and-calibration.md](understanding-and-calibration.md); these prose headings do not add schema requirements.
