# Point-in-Time Training

Use this reference for table-backed training, SCD2 feature tables, temporal labels, or any run that must be repeatable after it launched.

## Two Time Contracts

Do not collapse physical table reproducibility and per-example feature validity into one timestamp.

| Contract | What it prevents | Required evidence |
|----------|------------------|-------------------|
| Immutable physical source | A rerun reading a later Delta state | Fully-qualified table, recorded Delta version, recorded commit ID |
| Example-time SCD2 validity | A prediction using feature state from its future | `example_at`, `valid_from`, `valid_to`, and availability timestamp when present |

Each source in `provenance.sources` must use `catalog.schema.table` and include `delta_version` plus `delta_commit_id`. Read the recorded Delta version with time travel for every rerun. A current table name alone is not a reproducible input.

## Per-Example Join

Use each row's `example_at` or prediction timestamp. Do not join every example to one job-start or snapshot timestamp.

```sql
SELECT
  examples.example_id,
  examples.example_at,
  features.* EXCEPT (entity_id, valid_from, valid_to, available_at)
FROM main.ml_models.training_examples VERSION AS OF ${examples_delta_version} AS examples
JOIN main.ml_models.features_scd2 VERSION AS OF ${features_delta_version} AS features
  ON examples.entity_id = features.entity_id
 AND features.valid_from <= examples.example_at
 AND (features.valid_to > examples.example_at OR features.valid_to IS NULL)
 AND features.available_at <= examples.example_at
WHERE examples.example_at >= TIMESTAMP '${split_start}'
  AND examples.example_at < TIMESTAMP '${split_end}'
```

`valid_to` is exclusive. The rule is `valid_from <= example_at < valid_to`. If a table lacks `available_at`, document the equivalent ingestion-availability contract before accepting the join.

## Split Contract

Use half-open windows, `start <= example_at < end`, and record them with an RFC 3339 offset. The JSON validator rejects reversed and overlapping train, validation, and test windows. After generating the actual sets, validate their stable `example_id` values as well:

```python
from point_in_time_contract import validate_pairwise_disjoint_split_ids

validate_pairwise_disjoint_split_ids(
    {
        "train": train_example_ids,
        "validation": validation_example_ids,
        "test": test_example_ids,
    }
)
```

Temporal disjointness does not prove entity disjointness. When repeated entities can leak information, split by the recorded `entity_key` or use a time-aware group policy and document it.

## Fixtures And Failure Tests

Keep offline fixtures for these cases:

- A **future feature** whose `valid_from` is after `example_at` must not match.
- A **late-arriving feature** whose business validity starts before `example_at` but whose `available_at` is later must not match.
- An overlapping example ID across two splits must fail even when date windows appear disjoint.

Use `select_feature_as_of` from [scripts/point_in_time_contract.py](../scripts/point_in_time_contract.py) in the fixture tests. It expects the caller to supply feature rows from the recorded physical snapshot and fails unless exactly one row is valid and available for the example.

## Required MLflow Provenance

Log source table names, Delta versions, commit IDs, split bounds and identities, `example_at` column, SCD2 rule, row counts, feature-set version, seed, and label source version. Without both source snapshots and example-time validity evidence, describe the run as non-reproducible.
