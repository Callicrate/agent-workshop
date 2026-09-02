# Review Method

This file expands the default workflow in `SKILL.md`. Use it when the document matters enough that you need a disciplined review rather than an improvised critique.

## Core mindset

The review should be adversarial in method but fair in judgment. The point is to determine what survives scrutiny, not to score rhetorical points.

This is a weakness-first exercise. The main output should tell the user where the artifact is bad, hidden, wrong, misleading, or dangerous to trust.

Document strengths so the review stays calibrated, but treat them as secondary to surfacing decision-relevant weaknesses and omissions.

Keep five buckets distinct at all times:

1. **True and well-supported**
2. **Plausible but under-evidenced**
3. **Unsupported or unverifiable with current evidence**
4. **Misleading because of omission or framing**
5. **False, contradicted, or logically broken**

## Phase 1: Reconstruct the argument

Before criticizing, map the artifact.

Capture:

- the headline thesis
- the exact decision question the review must answer and the key recommendation
- the explicit evidence chain
- the implied assumptions needed for the argument to work
- the audience being persuaded
- what the author appears to want the reader to ignore, accept, or take for granted

Also classify the review mode before building findings:

- `review-only`: produce findings and advice, then stop.
- `advise`: produce an implementation-ready repair plan, then stop.
- `patch-after-review`: review first, then apply authorized fixes.
- `first-look`: rebuild from current materials and avoid anchoring on prior reports.
- `delta-review`: compare current materials with a named prior baseline.
- `implementation-readiness`: decide whether the artifact is ready to build, deploy, publish, or commit.
- `public-surface-review`: prioritize live surface, naming, redundancy, and abstraction-leak checks.
- `evidence-slice-review`: bound claims to the supplied transcript, generated review, or truncated evidence slice.

Useful prompts:

- What must be true for the conclusion to hold?
- What is the strongest charitable version of the claim?
- Which claims are load-bearing and which are decorative?
- What evidence is central versus merely atmospheric?

## Phase 2: Build the claim ledger

Create an explicit ledger. Do not rely on an implicit or unstructured recollection.
For review-only, dirty, read-only, ephemeral, or one-report work, an explicit in-memory ledger is sufficient when it retains every required field and its material entries appear in the findings or report.
Create durable ledger files only when the user or repository contract authorizes a named workspace and that workspace is required for the review, such as authorized multi-pass persistence.

For each important claim, record:

- claim id
- exact quote
- location in the document
- claim type
- whether the claim is central to the thesis
- what support the document gives for it
- what would falsify or weaken it
- current verification status

For technical artifacts, add a live-surface ledger before judging implementation claims. Inventory registered tools, exported commands, routes, schemas, files, scripts, prompts, generated artifacts, or runtime outputs where available. Mark named systems, handoff targets, paths, tool families, external dependencies, and required inputs as observed, proposed, absent, or ambiguous.

Recommended statuses:

- verified
- mostly-supported
- partly-supported
- unsupported
- contradicted
- unclear
- opinion
- forecast

## Phase 3: Prioritize by materiality

Not all flaws matter equally. Rank issues by how much they would change a decision.

Spend most of the review budget on weaknesses, not on praise. If a document has ten decent paragraphs and two dangerous claims, the report should center the dangerous claims.

High-materiality items often include:

- quantitative claims driving ROI, safety, or policy conclusions
- hidden costs or omitted constraints
- causal claims used to justify intervention
- benchmark comparisons used to show superiority
- implementation promises that appear infeasible
- recommendations that ignore obvious tradeoffs or failure modes

## Phase 4: Run review passes

### Pass A: factual accuracy

Check names, dates, quantities, definitions, standards, timelines, and historical claims.

### Pass B: omissions and assumptions

Ask what the artifact left out that a reasonable decision-maker would want to know.

### Pass C: argument quality and fallacies

Map the reasoning chain. Find leaps, weak analogies, false dilemmas, selected examples, and disguised normative assumptions.

### Pass D: source quality and citation integrity

Check whether sources are primary, whether the cited passage supports the claim, and whether the source is current enough.

### Pass E: data and methodology

Inspect sample size, selection effects, confounders, denominators, baselines, confidence, model leakage, and whether the metric actually tracks the thing being claimed.

### Pass F: code, logic, and feasibility

If code or technical design is present, inspect correctness, complexity, scalability, operational burden, security, rollback, observability, and edge cases.

### Pass G: live technical surface

If the artifact describes tools, APIs, MCP servers, prompts, skills, commands, architecture, repository guidance, or simplification work, compare it to the current implementation before accepting the claim. Use `technical-surface-review.md` and a repository or runtime inventory. Treat non-existent components presented as real as factuality or architecture defects, not wording issues.

### Pass H: public mental model

Check whether the public concepts make sense to the target audience. Flag duplicate tools, confusing names, redundant required inputs, hidden lifecycle steps, or distinctions that matter internally but should not be caller-facing.

### Pass I: counterargument generation

Create the strongest opposing case. Good criticism often appears when you ask how a smart defender of the opposite conclusion would respond.

## Phase 5: Iterate

After every pass:

- update the TODO list
- add newly discovered claims
- log missing evidence
- schedule follow-up checks on disputed or high-severity items
- drop low-value rabbit holes if they no longer affect the decision

Stop only when the remaining open items are either low-materiality or impossible to resolve with available evidence.

Classify each unresolved item before synthesis:

- **blocking** — the evidence gap could change the verdict or make the recommended action unsafe or irreversible
- **nonblocking** — the item can be resolved by the recommended validation gate or after a reversible next step without changing the current verdict

In autonomous or delegated work, resolve nonblocking unknowns through the best reversible next action and its validation gate. Do not convert them into follow-up questions for an unavailable user. Use `insufficient-evidence` only when a blocking unknown prevents a responsible directional decision; uncertainty by itself is not a reason to avoid a verdict.

## Phase 6: Write findings

Every substantial finding should answer six questions:

1. What exact claim or move are you criticizing?
2. What is wrong with it?
3. What evidence supports your criticism?
4. Why does it matter?
5. What would a better version say instead?
6. How confident are you?

For implementation, architecture, or technical-surface findings, also include:

- observed evidence
- decision impact
- repair shape
- verification needed

Good reviews may include a short strengths section, but the detailed findings section should focus on weaknesses, hidden assumptions, misleading framing, and unsupported claims.

### Synthesize the decision

Answer the decision question with exactly one of these verdicts:

- `proceed` — current evidence supports the proposed decision; remaining unknowns are nonblocking
- `revise` — a bounded correction is required before proceeding
- `stop` — the proposal is contradicted, unjustified, or exposes a cost that should not be accepted
- `insufficient-evidence` — a blocking unknown prevents a responsible directional decision

Use the existing report fields rather than adding parallel conclusion fields:

- `overall_assessment.summary`: one line containing `<verdict>: <evidence-grounded rationale>`
- `open_questions`: prefix every item with `[blocking]` or `[nonblocking]`
- `overall_assessment.decision_impact`: exactly two nonblank lines, `Next action: <specific action>` followed by `Validation gate: <observable check that would justify the next state>`

The next action should be the smallest useful, reversible move consistent with the evidence. The validation gate must say what result changes the state; “validate later” is not a gate. In `review-only` and `advise` modes, this is a recommendation, not permission to edit or execute. In `patch-after-review`, act only within the user's existing authorization.

## Phase 7: Challenge your own review

Run a short self-audit before finalizing:

- Did you overstate any finding?
- Did you mistake missing evidence for disproof?
- Did you fail to note the document's strongest valid points?
- Did you miss a simpler interpretation of the author's claim?
- Did you rely too much on outside assumptions not justified by the artifact or sources?
- Did you review prose when the live implementation could be checked?
- Did you promote a workflow-gap finding before checking linked scripts, schemas, generated artifacts, or helper outputs for equivalent coverage?
- Did you treat a user correction or term question as wording-only when it may reveal a broken contract or redundant input?
- Does the verdict directly answer the stated decision question and follow from the findings?
- Is every unknown classified by whether it could change that verdict?
- Does the recommended next action have an observable validation gate?
- If the user is unavailable, did you avoid turning nonblocking unknowns into questions?

For evidence-slice reviews, add two more checks:

- Is the finding explicitly scoped to the supplied slice or corroborated by another source?
- Are truncated, generated, or heuristic inputs labeled as partial evidence rather than complete history?

## Common traps in reviewers

- focusing on style instead of substance
- calling something a strawman without showing the distorted target
- assuming malicious intent where incompetence or sloppiness explains the issue
- rewarding fancy charts that hide bad comparisons
- forgetting that omissions can be more misleading than direct falsehoods
- overfitting criticism to one paragraph while ignoring the full document context
- reusing the previous report outline when the user asked for a fresh first-look review
- accepting plausible future components as real without repository or runtime evidence
