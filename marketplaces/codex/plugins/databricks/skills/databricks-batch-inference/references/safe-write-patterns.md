# Safe Batch Inference Write Patterns

Use this reference after the staged scored DataFrame passes its pre-commit publication gate.

The durable prediction path is one DDL-complete insert-only `MERGE`. Prove the key, source window, expected row-count range, duplicate policy, run contract, and idempotency behavior before mutation.

Keep table DDL out of scoring notebooks and Python modules. If a target table is missing, add or update a standalone `.sql` file and apply it before scoring.

## DDL-Complete Insert-Only `MERGE`

Use immutable prediction history. The idempotency key is `(business_key, scoring_run_id)`, not the business key alone.

```python
from functools import reduce
import hashlib

from business_key_contract import (
    BUSINESS_KEY_MAX_CHARS,
    BUSINESS_KEY_PATTERN,
    is_supported_spark_type_name,
)
from delta.tables import DeltaTable
from pyspark.sql import functions as F

PREDICTION_COLUMNS = (
    "business_key",
    "scoring_run_id",
    "run_contract_digest",
    "run_contract_json",
    "run_contract_artifact_uri",
    "target_population_contract_digest",
    "staged_candidate_table",
    "staged_candidate_delta_version",
    "staged_candidate_snapshot_digest",
    "source_table",
    "source_delta_version",
    "source_window_start",
    "source_window_end",
    "observation_timestamp",
    "feature_lookup_strategy",
    "feature_snapshot_pins",
    "feature_snapshot_pins_digest",
    "requested_model_alias",
    "resolved_model_uri",
    "model_version",
    "model_run_id",
    "prediction",
    "score",
    "score_kind",
    "raw_score",
    "calibrated_score",
    "threshold_version",
    "label_map_version",
    "expected_label_artifact_digest",
    "unscorable_policy_version",
    "unscorable_reason",
    "scored_at",
)
REQUIRED_PREDICTION_COLUMNS = (
    "business_key",
    "scoring_run_id",
    "run_contract_digest",
    "run_contract_json",
    "target_population_contract_digest",
    "staged_candidate_table",
    "staged_candidate_delta_version",
    "staged_candidate_snapshot_digest",
    "source_table",
    "source_delta_version",
    "source_window_start",
    "source_window_end",
    "observation_timestamp",
    "feature_lookup_strategy",
    "feature_snapshot_pins",
    "feature_snapshot_pins_digest",
    "resolved_model_uri",
    "model_version",
    "model_run_id",
    "threshold_version",
    "label_map_version",
    "expected_label_artifact_digest",
    "unscorable_policy_version",
    "scored_at",
)

def invalid_business_key(column):
    text = column.cast("string")
    return (
        column.isNull()
        | (F.length(text) == 0)
        | (F.length(text) > BUSINESS_KEY_MAX_CHARS)
        | (text != F.trim(text))
        | ~text.rlike(BUSINESS_KEY_PATTERN.pattern)
    )


def require_supported_business_key_schema(dataframe, boundary_name):
    type_name = dataframe.schema["business_key"].dataType.simpleString()
    if not is_supported_spark_type_name(type_name):
        raise RuntimeError(f"{boundary_name} business key type is unsupported")
    return type_name


require_supported_business_key_schema(pinned_source_df, "pinned scoring source")
canonical_pinned_keys = pinned_source_df.select(
    F.col("business_key").cast("string").alias("business_key")
)
if canonical_pinned_keys.where(
    invalid_business_key(F.col("business_key"))
).limit(1).count() != 0:
    raise RuntimeError("pinned scoring source has an invalid business key")
duplicate_pinned_keys = (
    canonical_pinned_keys.groupBy("business_key")
    .count()
    .where(F.col("count") != 1)
)
if duplicate_pinned_keys.limit(1).count() != 0:
    raise RuntimeError("pinned scoring source has invalid or duplicate business keys")

if (
    DERIVED_RUN_PUBLICATION_DECISION != "ALLOW_PUBLISH"
    or DERIVED_RUN_PUBLICATION_REASON != "all_label_gates_allow"
):
    raise RuntimeError("derived run-level publication decision blocks merge")
staged_candidate_df = (
    spark.read.option("versionAsOf", GATED_STAGED_CANDIDATE_DELTA_VERSION)
    .table(GATED_STAGED_CANDIDATE_TABLE)
)
require_supported_business_key_schema(staged_candidate_df, "staged candidate source")
source = staged_candidate_df.select(*PREDICTION_COLUMNS).withColumn(
    "business_key", F.col("business_key").cast("string")
)
missing_required = reduce(
    lambda left, right: left | right,
    (F.col(column).isNull() for column in REQUIRED_PREDICTION_COLUMNS),
)
if source.where(missing_required).limit(1).count() != 0:
    raise RuntimeError("prediction rows are missing a DDL-required value")
if source.where(invalid_business_key(F.col("business_key"))).limit(1).count() != 0:
    raise RuntimeError("final staged source has an invalid business key")

staged_snapshot_mismatch = (
    (F.col("staged_candidate_table") != F.lit(GATED_STAGED_CANDIDATE_TABLE))
    | (
        F.col("staged_candidate_delta_version")
        != F.lit(GATED_STAGED_CANDIDATE_DELTA_VERSION)
    )
    | (
        F.col("staged_candidate_snapshot_digest")
        != F.lit(GATED_STAGED_CANDIDATE_SNAPSHOT_DIGEST)
    )
)
if source.where(staged_snapshot_mismatch).limit(1).count() != 0:
    raise RuntimeError("merge source differs from the gated staged snapshot")

metadata_mismatch = (
    (F.col("target_population_contract_digest") != F.lit(EXPECTED_TARGET_POPULATION_DIGEST))
    | (F.col("source_table") != F.lit(EXPECTED_SOURCE_TABLE))
    | (F.col("source_delta_version") != F.lit(EXPECTED_SOURCE_DELTA_VERSION))
    | (F.col("source_window_start") != F.lit(EXPECTED_SOURCE_WINDOW_START))
    | (F.col("source_window_end") != F.lit(EXPECTED_SOURCE_WINDOW_END))
    | (F.col("feature_lookup_strategy") != F.lit(EXPECTED_FEATURE_LOOKUP_STRATEGY))
    | (F.col("feature_snapshot_pins_digest") != F.lit(EXPECTED_FEATURE_PINS_DIGEST))
    | (F.col("resolved_model_uri") != F.lit(EXPECTED_RESOLVED_MODEL_URI))
    | (F.col("model_version") != F.lit(EXPECTED_MODEL_VERSION))
    | (F.col("model_run_id") != F.lit(EXPECTED_MODEL_RUN_ID))
    | (F.col("threshold_version") != F.lit(EXPECTED_THRESHOLD_VERSION))
    | (F.col("label_map_version") != F.lit(EXPECTED_LABEL_MAP_VERSION))
    | (
        F.col("expected_label_artifact_digest")
        != F.lit(EXPECTED_LABEL_ARTIFACT_DIGEST)
    )
    | (F.col("unscorable_policy_version") != F.lit(EXPECTED_UNSCORABLE_POLICY_VERSION))
)
if source.where(metadata_mismatch).limit(1).count() != 0:
    raise RuntimeError("gated snapshot metadata differs from the publication contract")

recomputed_expected_digest = hashlib.sha256(
    EXPECTED_RUN_CONTRACT_JSON.encode("utf-8")
).hexdigest()
if (
    recomputed_expected_digest != EXPECTED_RUN_CONTRACT_DIGEST
    or EXPECTED_SCORING_RUN_ID != EXPECTED_RUN_CONTRACT_DIGEST
):
    raise RuntimeError("expected canonical run-contract JSON digest is invalid")
contract_mismatch = (
    (F.col("scoring_run_id") != F.col("run_contract_digest"))
    | (F.col("run_contract_digest") != F.sha2(F.col("run_contract_json"), 256))
    | (F.col("scoring_run_id") != F.lit(EXPECTED_SCORING_RUN_ID))
    | (F.col("run_contract_digest") != F.lit(EXPECTED_RUN_CONTRACT_DIGEST))
    | (F.col("run_contract_json") != F.lit(EXPECTED_RUN_CONTRACT_JSON))
)
if source.where(contract_mismatch).limit(1).count() != 0:
    raise RuntimeError("prediction rows contain a mixed, prior, or invalid run contract")

trimmed_reason = F.trim(F.col("unscorable_reason").cast("string"))
normalized_reason = F.when(trimmed_reason != "", trimmed_reason)
scoreable = (
    F.col("prediction").isNotNull()
    & F.col("score").isNotNull()
    & normalized_reason.isNull()
)
unscorable = (
    F.col("prediction").isNull()
    & F.col("score").isNull()
    & normalized_reason.isNotNull()
)
if source.where(~scoreable & ~unscorable).limit(1).count() != 0:
    raise RuntimeError("gated snapshot contains an unexplained NULL or inconsistent outcome")
unknown_labels = (
    source.where(scoreable)
    .select(F.col("prediction").alias("label_name"))
    .distinct()
    .join(EXPECTED_LABELS_DF.select("label_name"), ["label_name"], "left_anti")
)
if unknown_labels.limit(1).count() != 0:
    raise RuntimeError("gated snapshot contains an unknown prediction label")
invalid_unscorable_reasons = (
    source.where(unscorable)
    .select(normalized_reason.alias("normalized_reason"))
    .distinct()
    .join(
        ALLOWED_UNSCORABLE_REASONS_DF.select("normalized_reason"),
        ["normalized_reason"],
        "left_anti",
    )
)
if invalid_unscorable_reasons.limit(1).count() != 0:
    raise RuntimeError("gated snapshot contains a reason outside the pinned unscorable policy")

invalid_final_keys = (
    source.groupBy("business_key", "scoring_run_id")
    .count()
    .where(
        F.col("business_key").isNull()
        | F.col("scoring_run_id").isNull()
        | (F.col("count") != 1)
    )
)
if invalid_final_keys.limit(1).count() != 0:
    raise RuntimeError("final prediction source has NULL or duplicate run keys")

expected_keys = pinned_source_df.select(
    F.col("business_key").cast("string").alias("business_key")
).withColumn(
    "scoring_run_id", F.lit(EXPECTED_SCORING_RUN_ID)
)
final_keys = source.select("business_key", "scoring_run_id")
unexpected_keys = final_keys.join(
    expected_keys, ["business_key", "scoring_run_id"], "left_anti"
)
missing_keys = expected_keys.join(
    final_keys, ["business_key", "scoring_run_id"], "left_anti"
)
if unexpected_keys.limit(1).count() != 0 or missing_keys.limit(1).count() != 0:
    raise RuntimeError("scoring or feature lookup changed the pinned source cardinality")

target = DeltaTable.forName(spark, f"{CATALOG}.{SCHEMA}.{TABLE}")
target.alias("t").merge(
    source.alias("s"),
    "t.business_key = s.business_key AND t.scoring_run_id = s.scoring_run_id",
).whenNotMatchedInsert(
    values={column: f"s.{column}" for column in PREDICTION_COLUMNS}
).execute()
```

The pinned input check and the final `source` check are distinct. A model UDF or feature join can fan one valid input key into multiple scored rows; validate the actual final DataFrame immediately before `MERGE`. Databricks insert-only `MERGE` does not remove duplicates inside the incoming DataFrame.

## Deterministic Run And Attempt Identity

Serialize the run contract as canonical UTF-8 JSON: sorted object keys, no insignificant whitespace, stable decimal/timestamp encodings, ordered feature names, and an ordered `feature_snapshot_pins` array. Compute `run_contract_digest` as its lowercase SHA-256 hex digest and set `scoring_run_id` to that digest. Persist the exact `run_contract_json`, digest, optional immutable artifact URI, lookup strategy, and ordered pins in both prediction and audit rows.

The canonical JSON contains:

- source table, source Delta version, inclusive/exclusive window, and observation timestamp definition
- immutable model URI, resolved version, and model run ID
- ordered feature/signature digest, `feature_lookup_strategy`, and every ordered feature dependency snapshot pin
- threshold version, label-map version, canonical expected-label artifact digest, unscorable-policy version, and output schema digest
- target-population predicate/key contract and its digest
- fully qualified staged-candidate Delta table, gated table version, and staged snapshot digest
- fixed one-hour bucket width, baseline window, and every finite publication-gate threshold

Do not include wall-clock time, cluster ID, notebook run ID, or a random UUID. Before every retry, byte-compare the canonical JSON and compare its recomputed digest, lookup strategy, and ordered pins with the durable audit row. A mismatch is a new logical run; never reuse the prior `scoring_run_id`.

Require an explicit non-negative `attempt_ordinal`. Derive `attempt_id = sha256(scoring_run_id + ":" + decimal_attempt_ordinal)`. Scheduler retries of the same physical attempt reuse its ordinal; a deliberately new attempt increments it. Store both IDs in the audit table.

## Audit State Machine And Recovery

Delta does not provide one transaction across prediction and audit tables. Use this order:

1. insert or reconcile one audit row for `(scoring_run_id, attempt_id)` as `running`
2. compare canonical run-contract JSON, digest, feature strategy, and ordered pins with any durable rows for the run
3. score once into the pinned staged-candidate Delta version and run the publication gate against that exact snapshot
4. reread that same staged table `versionAsOf`, repeat final contract/key/outcome/label/policy checks, and insert-only `MERGE` on `(business_key, scoring_run_id)`
5. reconcile exact expected keys, counts, metadata, and no duplicate target keys
6. update the audit row to `succeeded` with the prediction target commit version
7. on a pre-commit or scoring failure, record `failed` with a bounded error summary

Recovery rules:

- **No prediction rows:** rerun from the same pins; the insert-only merge creates all rows.
- **Partial prediction rows:** reread the already-gated staged Delta version and merge; never recompute scoring after the gate. Existing run keys remain unchanged and only missing keys insert.
- **All prediction rows committed but final audit update failed:** do not rescore. Reconcile the committed rows and repair the same audit attempt to `succeeded` with the observed target commit version.
- **Committed rows differ in immutable metadata or prediction value:** mark the attempt failed and stop. Never update history in place.
- **Existing `succeeded` audit:** treat the run as complete only after prediction reconciliation still passes.

Keep the publication gate before prediction commit. An indeterminate or blocked gate can write audit/quarantine evidence but no publishable prediction rows.
Derive and persist one run-level decision with [../scripts/derive_publication_decision.py](../scripts/derive_publication_decision.py) or the identical SQL mapping in [label-share-anomaly-gating.md](label-share-anomaly-gating.md). Merge consumes only that derived decision and its fixed reason; it never treats raw per-label statuses as admission.

Offline tests must cover an empty retry, a partial retry, audit-finalization recovery, duplicate-source rejection, and conflicting committed rows.

## Write Rules

- Never overwrite already-scored history. Rescoring uses a new scoring contract and therefore a new `scoring_run_id`.
- Use [../scripts/business_key_contract.py](../scripts/business_key_contract.py) as the single type/grammar source. Before any cast, both pinned input and staged candidate must have Spark `string`, `tinyint`, `smallint`, `int`, `bigint`, or `decimal(p,s)` with `1 <= p <= 38` and `0 <= s <= min(p,18)`. Enforce each signed integer domain exactly. A Decimal must fit the declared integer digits and scale without rounding; trailing zeroes and exponent notation are canonicalized numerically, and signed zero becomes `0`. Reject Boolean, Float/Double, Binary, Array, Map, Struct, collection, nonfinite Decimal, or other schemas. Then canonicalize to 1-512 ASCII characters with no leading/trailing whitespace and grammar `-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}`; reject NULL, blank, whitespace-only, control/Unicode, oversized, or mixed invalid values in both boundaries.
- Match on a stable primary key. If chunk windows can overlap, serialize writers per key space or partition window.
- Keep scoring-table DDL in a dedicated `.sql` file. Do not embed `CREATE TABLE` or rely on `saveAsTable` to create the target from scoring logic.
- Fail loudly if the target scoring table is missing or if its schema no longer matches the scoring output.
- Prefer explicit column lists over `INSERT *` when source and target schemas can drift.
- Keep all required columns from [../assets/scoring-table-ddl.sql](../assets/scoring-table-ddl.sql) in the written output.
- Treat `NOT NULL` and `CHECK` clauses as informational/runtime validation, not uniqueness or idempotency enforcement. Delta primary-key style uniqueness is not enforced here.
- If source evidence is missing, do not write a valid prediction by substituting a default value. Write no score row or write an explicit `unscorable_reason` to the audit path.

## Traceable Writes

Each prediction row should carry enough metadata to reproduce the scoring decision:

- `scoring_run_id`
- canonical run-contract JSON, digest, and optional artifact reference
- source Delta version, source window, and observation timestamp
- target-population contract digest; staged-candidate table, Delta version, and snapshot digest; feature lookup strategy, ordered feature snapshot pins, and their digest
- requested model alias and immutable resolved model URI
- `model_version`
- `model_run_id`
- `scored_at`
- `score`, `score_kind`, `raw_score`, and `calibrated_score`, preserving their distinct meanings
- threshold and label-map versions, canonical expected-label artifact digest, and unscorable-policy version
- `unscorable_reason` when the row could not be scored

Each scheduled job should also write or log a run-level audit record with:

- source table, target table, and source filter
- resolved model URI, model version, and model run ID
- deterministic run and attempt IDs plus prediction target commit version
- before and after counts
- duplicate-key counts
- skipped or unscorable counts
- unexpected null prediction count
- schema hash or explicit output column list

Use [../assets/scoring-run-audit-ddl.sql](../assets/scoring-run-audit-ddl.sql) when a durable Delta audit table is useful.

## Reconciliation Checks

Always run after scoring:

```sql
-- Before/after counts
SELECT
    COUNT(*) AS total_rows,
    COUNT(prediction) AS scored_rows,
    COUNT(*) - COUNT(prediction) AS unscored_rows,
    COUNT(DISTINCT model_version) AS model_versions_present
FROM ${catalog}.${schema}.${table}
WHERE scoring_run_id = :scoring_run_id;

-- Sample scored rows for sanity
SELECT business_key, prediction, score, score_kind, model_version, scored_at
FROM ${catalog}.${schema}.${table}
WHERE scoring_run_id = :scoring_run_id
ORDER BY business_key
LIMIT 20;

-- Score distribution check
SELECT
    prediction,
    COUNT(*) AS cnt,
    AVG(score) AS avg_score,
    MIN(score) AS min_score,
    MAX(score) AS max_score
FROM ${catalog}.${schema}.${table}
WHERE scoring_run_id = :scoring_run_id
    AND score IS NOT NULL
GROUP BY prediction;
```

For richer checks, including duplicate keys, unscorable counts, stale source windows, mixed model versions, and table-role inventory, use [reconciliation-sql-contract.md](reconciliation-sql-contract.md) with [../scripts/emit_reconciliation_sql.py](../scripts/emit_reconciliation_sql.py).
Its single-key duplicate diagnostic is not a proof that the `MERGE` condition is safe: Databricks can reject a merge when multiple source rows match one target row, and the exact matching semantics depend on the merge condition and runtime behavior.

Official insert-only `MERGE` semantics and duplicate-source warning: [Databricks Delta `MERGE`](https://docs.databricks.com/aws/en/delta/merge).

## Concurrency Guardrail

- Serialize every merge that can touch the same `(business_key, scoring_run_id)` space. Set the scoring job to one concurrent run or acquire a durable run-contract lock before `MERGE`; release it only after prediction reconciliation and audit finalization. Disjoint partitions are not sufficient unless the business-key/run spaces are proven disjoint.
- Delta optimistic concurrency and table constraints do not replace this serialization contract. If lock ownership is lost or another writer is active, fail the attempt before mutation.
