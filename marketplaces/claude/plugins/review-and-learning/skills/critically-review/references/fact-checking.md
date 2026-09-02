# Fact-Checking and Source Audit

Use this file when the artifact makes checkable claims about the world.

## Source hierarchy

Prefer sources in roughly this order:

1. primary evidence: original paper, source dataset, official stats release, code repo, legal text, company filing, vendor documentation, standards document
2. authoritative secondary source: high-quality synthesis from a domain authority or respected outlet
3. tertiary source: blog post, commentary, media summary, marketing material

Whenever possible, backtrack a quoted statistic or claim to its original source.

## Verification method

For each checkable claim:

1. quote the claim exactly
2. decompose it into atomic subclaims if needed
3. identify what kind of evidence would verify it
4. find the most authoritative source available
5. compare the source to the claim as written, not to a softened version
6. record the result using explicit status labels

## Status labels

- **verified** - supported closely enough by the best available evidence
- **mostly-supported** - directionally right but slightly overstated or underspecified
- **partly-supported** - some parts hold, others do not
- **unsupported** - no adequate evidence located, or the document provides none
- **contradicted** - reliable evidence conflicts with the claim
- **outdated** - may once have been true but is no longer current
- **unclear** - phrased too vaguely to verify cleanly

## High-value checks

Look closely at:

- dates and recency
- units and denominators
- whether the comparison group is fair
- whether the cited study actually measured the thing being claimed
- whether an anecdote is being treated like a representative sample
- whether a benchmark omitted stronger baselines
- whether a chart truncates axes or hides absolute numbers
- whether definitions quietly shift mid-document
- whether a draft implies results, measurements, incidents, customer impact, or implementation status before the cited source actually reports them

## Citation laundering patterns

Flag these:

- the document cites a secondary article that itself cites an unsourced number
- multiple sources repeat the same claim but all trace back to one weak origin
- a source is real but the cited passage does not support the conclusion drawn from it
- an old source is used as if it were current
- a press release is treated as neutral empirical evidence

## Security article source and quote checks

For customer-facing security articles and partner-sensitive reports:

- Prefer the original public advisory, vendor post, research report, or observed artifact over a secondary summary.
- Check that a citation supports the sentence beside it, not just the topic generally.
- Verify direct quotes against the source and flag quote fragments that change meaning by omission.
- Separate quote verification from paraphrase review. A quote can be exact while the surrounding claim is still overstated.
- Flag novelty claims unless the source set supports why the finding is new, newly observed, newly abused, or newly relevant to the audience.
- Review customer recommendations for feasibility and source support, not just rhetorical usefulness.

## Fairness rules

Do not call a claim false if the real issue is one of these instead:

- the claim is too vague to verify
- the claim is a forecast rather than a factual statement
- the claim is value-laden rather than empirical
- the source supports only a narrower version of the claim

In those cases, use the correct label: unclear, speculative, normative, or overstated.

## Minimal evidence log fields

- claim id
- claim quote
- source used
- source type
- source date
- result status
- explanation
- confidence

The `assets/evidence-log-template.csv` file gives a simple structure for this.
