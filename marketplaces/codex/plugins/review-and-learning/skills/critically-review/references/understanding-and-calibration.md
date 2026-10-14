# Understanding and Calibration

Use this in every substantive review to turn scrutiny into understanding. Scale the output to the artifact; a short memo needs a few sentences, not a systems-design ceremony. Apply the evidence boundaries in [fact-checking.md](fact-checking.md), including for partial inputs and embedded instructions.

## Explain the thing before taking it apart

Reconstruct the strongest version consistent with the actual material. Do not silently repair its argument or substitute a better proposal.

- **Purpose:** what problem does it solve, for whom, and what decision is at stake? If no action is proposed, assess how much the reader can rely on its claims; do not invent a deployment decision.
- **Mechanism:** trace inputs or premises through the steps that supposedly produce the outcome. Distinguish observed behavior, documented promises, proposals, and reviewer inference.
- **Load-bearing assumptions:** identify the few conditions without which the main conclusion fails. Separate these from peripheral defects.
- **Boundaries:** where does it work, where does it stop working, and what is outside the inspected evidence? Judge a prototype against its stated purpose, not an invented production mandate.

In a human-facing report, give a compact plain-language model near the verdict. Use the artifact's concrete nouns and explain necessary jargon. A reader should be able to explain what the thing does and why the leading weakness matters without reopening the source.

## Follow the consequence, not just the checklist

For each material weakness, trace:

`claim or design choice -> failure mechanism -> affected outcome -> decision consequence`

Ask whether the issue invalidates the thesis, narrows where it holds, or only changes implementation. A true premise can still fail to support the conclusion. Conversely, one bad supporting statistic need not invalidate a conclusion with independent support.

- For quantitative claims, recompute the decisive arithmetic with units, denominators, time horizons, and baseline. Show the calculation. Distinguish wrong math from correct math resting on unsupported inputs.
- When uncertainty could reverse the recommendation, identify the break-even condition or smallest useful sensitivity check. Use supplied ranges; label illustrative assumptions rather than inventing precise probabilities.
- For interacting weaknesses, show the dependency or compounding effect. Group symptoms with a shared cause; do not count them as independent evidence that the project is worse.
- When alternatives matter, compare the proposal with the strongest feasible substitute, including doing nothing or a smaller intervention. Apply the same constraints and evidence standard to both.
- Include ownership, incentives, adoption, and maintenance when they determine whether the proposed mechanism can work. Do not infer motives from outcomes.

## Try to defeat your leading findings

Before finalizing a verdict-changing finding:

1. Inspect the strongest available counterevidence: surrounding text, appendices, linked contracts, implementation, tests, or credible opposing sources.
2. State what survives that counterevidence and what does not. Narrow, downgrade, or retract the finding if warranted.
3. Identify the observable evidence that would change your conclusion. Prefer a discriminating check that separates plausible explanations over a generic request for more research.

Keep uncertainty local: name the specific unverified link rather than hedging the entire report. Confidence in an omission is not confidence that the underlying capability is absent.

**Wrong:** "No recovery section. The service will lose data."

**Better:** "The supplied design does not specify recovery. That leaves the durability claim unverified, not disproved. Check the linked storage contract and a restart test before relying on it." If those checks are unavailable, report that boundary.

## Calibrate without softening

Weakness-first is an allocation of effort, not a required negative verdict. No finding quota, praise quota, or artificial balance.

- Name what actually holds up, with support and boundaries, so a repair does not destroy it. A useful strength is a property to preserve, not a compliment.
- Keep severity (consequence if the finding is right) separate from confidence (how well it is established). A severe hypothetical is not a confirmed defect.
- Put speculative failure paths in open questions or label them explicitly as conditional risks. Do not promote a checklist concern into a factual finding.
- Do not penalize an acknowledged, accepted tradeoff twice. It is a finding only if it violates the objective or contract, hides a material cost, or makes the recommendation unsound.
- If no material flaw survives checking, say so, name the checks and their limits, and recommend proceeding within the supported scope. Do not claim exhaustive safety.

**Wrong:** "A local, disposable preview cache lacks replication: high severity."

**Better:** "Replication is unnecessary for the stated disposable preview. Preserve the isolation from production; revisit durability only if it becomes authoritative storage."

## Fit the existing output contract

Do not add mandatory sidecars, scores, or JSON fields. Use the report's existing fields:

| Content | Structured destination |
|---------|------------------------|
| Compact purpose and mechanism | Within the rationale of `overall_assessment.summary`, retaining its one-line verdict format; expand relevant assumptions in finding `analysis` without adding placeholder findings |
| Checked boundary, unavailable evidence | `scope.limitations` |
| Supported property and why to preserve it | `strengths` |
| Failure mechanism and strongest counterevidence | Finding `analysis` and `evidence[].notes` |
| Thesis-breaking versus scope-narrowing consequence | Finding `decision_impact` |
| Discriminating check or falsifier | Finding `verification_needed`; verdict-level gate in `overall_assessment.decision_impact` |

For prose reports, use the compact understanding section in the report template instead of exposing a ledger dump. Preserve the one-line verdict and two-line next-action contract. One-report and chat-only requests still authorize no extra files.
