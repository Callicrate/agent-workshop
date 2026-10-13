# Data, Code, and Logic Review Lenses

Load this file when the artifact includes data analysis, metrics, experimental results, technical design, pseudocode, or strong feasibility claims.

## Data and metrics

Check:

- what was measured versus what the document claims was measured
- sample size and sampling method
- selection bias and survivorship bias
- missing values and exclusions
- units, denominators, and normalizations
- whether averages hide tail behavior or subgroup differences
- whether percent changes hide tiny absolutes
- whether the metric is gameable or merely adjacent to the desired outcome
- whether a benchmark uses the strongest relevant baseline

## Comparison integrity

When an artifact compares systems, policies, models, or workflows, make a small comparison map before accepting a headline result. For each reported metric, record:

- the eligible population, sampling frame, time window, exclusions, and whether both systems had a chance to act
- the unit and event being counted: prediction, request, accepted request, observed state change, outcome, or another explicit event
- numerator, denominator, conditioning rule, and whether the systems use the same denominator
- the reference-label source, its temporal order relative to each system, and any attribution rule needed to join it
- coverage for each system and the cohort used for any matched or paired result

Do not silently convert one event type into another. A submitted or accepted request is not an observed effect, and an add event is not necessarily a state transition. Call an event a change only when the inspected data establishes the preceding state, attribution, and resulting state needed for that claim.

Separate these estimands instead of treating them as interchangeable:

| Question | Required cohort | Safe conclusion |
|----------|-----------------|-----------------|
| How reliable is each system when it acts? | Each system's action-conditioned population | Conditional agreement or error rate for that system's actions |
| What happened in the shared workflow? | A common eligible population | Correct and incorrect action counts, coverage, and incidence using the common denominator |
| Which system wins on the same cases? | A paired cohort with comparable pre-reference outputs | A paired comparison, with its selection rule and size |

Different action rates or opportunity sets can make conditional agreement a useful operational measure, but they prevent it from establishing intrinsic accuracy or a universal system ranking. A small intersection where both systems acted or disagreed is also a different, selected estimand. Report it separately; do not let it stand in for either overall workflow effects or matched-coverage performance.

Treat a human-labelled subset as an evaluated slice until its label coverage and selection mechanism are known. Check whether labelling is incomplete, source-dependent, clustered, influenced by system-visible hints, or otherwise related to the outcome. A large row count does not establish independent or representative observations. Scope the result to the labelled cohort when those questions remain open, and distinguish an observed limitation from a measured bias.

For a verdict-driving comparison, verify the join and totals directly: reconcile each system's requests, actions, and outcomes; state the reference-label reconstruction rule; and rerun the result on the subset with unambiguous timing and attribution when that restriction could change the conclusion.

**Wrong:** "System A made 5,000 correct changes" when the evidence only records 5,000 accepted requests whose requested labels match the reference.

**Better:** "System A submitted 5,000 accepted requests whose requested labels match the reference; the audit observed 2,652 attributed category-add events. The comparison uses the observed events."

## Experimental and methodological claims

Check:

- control groups or lack of controls
- confounders and omitted variables
- timing effects and seasonality
- p-hacking patterns or specification search
- train/test leakage or benchmark contamination
- whether the validation environment matches the deployment environment
- whether the reported gain is statistically and practically meaningful
- whether robustness checks, ablations, or sensitivity analysis are missing

## Forecasts and business logic

Check:

- hidden growth assumptions
- unreasonable adoption curves
- TAM inflated into expected revenue
- cost assumptions that ignore integration, support, or compliance
- payback models that ignore switching friction
- scenario analysis that lacks downside cases

## Code and pseudocode

Review code, pseudocode, and algorithms for:

- correctness under normal and edge conditions
- hidden complexity or impossible big-O claims
- race conditions, retries, idempotency, and duplicate processing
- error handling, rollback, and recovery
- authentication, authorization, and secret handling
- privacy and data retention assumptions
- observability and debugging gaps
- scalability bottlenecks
- concurrency, caching, and consistency tradeoffs
- manual process hidden behind words like automated, seamless, or real-time

## Architecture and implementation

Ask:

- What breaks first at scale?
- What assumption fails under realistic latency, data quality, or user behavior?
- Which component is a single point of failure?
- What operational burden is being hidden?
- What safety or compliance work is missing from the plan?
- What part depends on perfect upstream behavior?

## Public surface and abstraction leaks

For tools, APIs, MCPs, prompts, skills, and technical docs, review the caller-facing mental model as seriously as correctness.

Check for:

- duplicate tools, commands, routes, prompts, or workflow names that make the caller choose between overlapping concepts
- confusing public names that preserve internal implementation details
- distinctions that are technically real but not useful to the target audience
- redundant required inputs when one canonical identifier or context value should be enough
- hidden lifecycle steps such as start without status, wait, result, cleanup, or failure handling
- claimed simplification without before and after counts
- docs that mention handoff partners, tool families, files, scripts, or services that are absent from the live repository or runtime

Group related public-surface issues by decision impact. A family of overlapping health, preflight, status, network, or file-transfer commands is usually one architecture finding, not many isolated naming nitpicks.

## Minimal technical-output rule

If you say a design or algorithm is weak, explain exactly where the logic fails. Point to the step, assumption, complexity claim, missing control, or boundary condition that creates the problem.

For public-surface findings, explain why the audience-facing model fails even when the internal implementation distinction is real.
