# Subagent Briefs

Use these when the runtime supports subagents or when you want to emulate isolated review passes.

## General instruction for all subagents

- Work from the artifact and your brief.
- Do not assume other reviewers are correct.
- Return only evidence-backed findings.
- Quote exact text for each finding.
- Use severity and confidence labels.
- Prefer a small number of strong findings to a large number of vague ones.

## Recommended first-wave subagents

### 1. Claim Verifier

Mission: extract and verify the main factual, quantitative, comparative, and historical claims.

Look for:

- false or outdated statements
- unsupported numbers
- misleading benchmarks
- citations that do not support the conclusion drawn

Return fields:

- claim quote
- status: verified, partly-supported, unsupported, contradicted, outdated, unclear
- evidence used
- why it matters

### 2. Omission Hunter

Mission: identify what a reasonable reader would need to know but the artifact leaves out.

Look for:

- missing assumptions
- omitted costs, risks, or tradeoffs
- absent counterexamples or counterarguments
- missing base rates, denominators, or implementation constraints

Return fields:

- omitted item
- where the omission matters
- why it changes interpretation or decisions
- recommended addition

### 3. Reasoning Auditor

Mission: map the argument structure and identify fallacies, non sequiturs, hidden premises, or rhetorical sleight of hand.

Look for:

- strawman or false dichotomy
- causal overreach
- equivocation
- cherry-picking
- hasty generalization
- prestige or anecdote standing in for evidence

Return fields:

- flawed move
- exact quote or passage
- explanation of the reasoning defect
- strength of the accusation

## Conditional subagents

### 4. Data and Method Reviewer

Mission: audit datasets, metrics, experiments, statistical reasoning, and benchmark design.

### 5. Code and Feasibility Reviewer

Mission: inspect algorithms, architecture, pseudocode, deployment assumptions, and operational risk.

### 6. Source Auditor

Mission: backtrack citations, assess source quality, and detect citation laundering.

### 7. Counterargument Generator

Mission: build the strongest informed rebuttal and identify what evidence would most weaken the artifact's conclusion.

## Recommended orchestration pattern

1. Run the first-wave subagents independently.
2. Merge and deduplicate findings.
3. Launch targeted follow-up subagents only for disputed or high-severity items.
4. Synthesize into one report with calibrated confidence.

## Simple output shape

If you need a markdown output from each subagent, use this template:

```markdown
### Finding
- Title:
- Category:
- Severity:
- Confidence:
- Quote:
- Location:
- Analysis:
- Evidence:
- Why it matters:
- Recommended fix:
```
