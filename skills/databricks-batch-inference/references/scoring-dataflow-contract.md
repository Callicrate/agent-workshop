# Scoring Dataflow Contract

Use this reference before editing batch inference workflows that span multiple notebooks, jobs, or tables.

## Required Map

Write a compact map before code changes:

| Role | Required Questions |
|------|--------------------|
| Source of truth for scoring | Which table defines rows eligible for scoring? |
| Feature source | Which table or view provides model inputs? |
| Training split | Is it train, validation, test, holdout, or review-only? |
| Scoring population | Which rows should receive predictions now? |
| Promotion population | Which rows decide alias, threshold, or ensemble changes? |
| Target table | Where are predictions written? |
| Audit table | Where are run-level counts and traces stored? |
| Temporary or prep table | Which downstream consumer requires it, and when can it be removed? |

Do not use `prep_train`, `prep_val`, or other training preparation tables for production scoring unless the model contract explicitly names them as scoring inputs.
The canonical clean table is usually the scoring population source unless a project-specific scoring table overrides it.

## Notebook And Task Roles

Keep roles distinct:

- `train.ipynb` produces model artifacts, metrics, feature contracts, thresholds, and registry versions.
- `score.ipynb` loads a resolved model version and writes predictions for a bounded population.
- `promote.ipynb` chooses aliases, thresholds, or candidates from metrics and review evidence.
- `ensemble.ipynb` combines model outputs or rule outputs and writes a traceable decision.
- monitoring validates terminal job state, row coverage, and output distributions.

Do not collapse training, scoring, promotion, and monitoring into one vague notebook task unless the existing project already has that contract and the user asks to keep it.

## Temporary Table Cleanup

Before deleting a prep table:

1. list direct notebook, job, dashboard, and downstream table consumers
2. identify retention or audit requirements
3. confirm the table is not used for reconciliation or rerun recovery
4. replace consumers with the canonical source table or explicit scoring table
5. document the removed role in the task summary

Extraneous temporary tables should not survive because they look useful. Keep them only when a named consumer or audit contract needs them.