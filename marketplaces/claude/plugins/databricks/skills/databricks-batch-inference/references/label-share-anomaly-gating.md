# Label-Share Anomaly Gating And Drift RCA

Use this reference to gate staged candidate predictions before their insert-only prediction commit. Never run the publication decision against already-published output or an incomplete wall-clock bucket.

## Freeze The Candidate Contract

The run contract must name:

- `scoring_run_id`, source Delta version, inclusive window start, exclusive window end, and observation-time definition
- immutable model URI, model version, model run ID, threshold version, label-map version, and unscorable-policy version
- run-contract digest, target-population contract digest, feature lookup strategy, and ordered feature-pins digest
- complete expected-label universe and canonical ordered-set SHA-256 digest from the versioned label-map artifact
- baseline horizon, `min_baseline_buckets`, `min_bucket_rows`, `min_expected_label_rows`, and `z_score_threshold`

The canonical bucket width is exactly one hour. Store `bucket_width_hours = 1`; reject every other value. Candidate start, candidate end, and baseline start must align to hour boundaries, and the candidate end must equal its start plus one hour.

Validate parameters before reading outcomes: `min_baseline_buckets >= 2`, `min_bucket_rows > 0`, `min_expected_label_rows >= 0`, and `z_score_threshold` must be finite and greater than zero. Reject NULL, NaN, infinity, reversed windows, unaligned boundaries, and a candidate end after `evaluation_as_of`.

Choose the latest fully closed window from a durable staging manifest first. Bind `evaluation_as_of` explicitly from orchestration; do not substitute `current_timestamp()`.
Use tie-preserving `RANK`, not `ROW_NUMBER`: duplicate rows at the maximum `(source_window_end, closed_at)` must remain visible so the exactly-one assertion blocks both quarantine and publication.

```sql
SELECT scoring_run_id, source_window_start, source_window_end
FROM staged_scoring_window_manifest
WHERE window_state = 'closed'
    AND source_window_end <= :evaluation_as_of
    AND source_table = :source_table
    AND target_population_contract_digest = :target_population_contract_digest
QUALIFY RANK() OVER (
    ORDER BY source_window_end DESC, closed_at DESC
) = 1;
```

Freeze that row in the canonical run-contract JSON. The gate must reject a requested run/window that differs from this selected manifest row or whose stage reconciliation is incomplete.
Canonicalize the expected-label artifact as `TO_JSON(ARRAY_SORT(COLLECT_LIST(label_name)))` over its exact rows and compare its lowercase SHA-256 digest with `expected_label_artifact_digest` from the run contract and manifest. Before either label query, fail closed if the artifact is empty, contains duplicates, or its digest differs. An empty expected-label join is not a passing gate.

Bind `staged_candidate_predictions` to `spark.read.option("versionAsOf", staged_candidate_delta_version).table(staged_candidate_table)` once for this gate. Both SQL paths and the later merge use that immutable snapshot plus `staged_candidate_snapshot_digest`; never bind the view to the current table head.

The candidate-content JSON serialization contract is `MAP('timeZone', 'UTC', 'timestampFormat', 'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX')`. Pass those exact options to every `TO_JSON(NAMED_STRUCT(...))` used for candidate content in the gate, intent materialization, retry verification, final merge assertion, and final reconciliation. The six fractional digits preserve microseconds and `XXX` emits an explicit UTC offset. Never rely on the Spark session timezone or its default timestamp format for a digest preimage.

## Quarantine Unknown Candidate Labels

Build one contract-bound staged candidate relation. Every equality below is required; do not replace it with a date-only filter. Quarantine and publication use the same `parameter_assertions`, `matched_manifest`, `matched_reconciliation`, `prerequisite_assertions`, `expected_label_assertions`, and `gate_prerequisite_assertions` shape. Materialize the result directly into the durable intent table with one assertion-bearing DML statement. Do not put named parameter markers in a view definition.

```sql
MERGE INTO ${catalog}.${schema}.${quarantine_intent_table} AS intent_target
USING (
WITH params AS (
    SELECT
        :scoring_run_id AS scoring_run_id,
        :run_contract_digest AS run_contract_digest,
        :run_contract_json AS run_contract_json,
        :source_table AS source_table,
        :source_delta_version AS source_delta_version,
        :source_window_start AS source_window_start,
        :source_window_end AS source_window_end,
        :baseline_window_start AS baseline_window_start,
        :evaluation_as_of AS evaluation_as_of,
        :resolved_model_uri AS resolved_model_uri,
        :model_version AS model_version,
        :model_run_id AS model_run_id,
        :threshold_version AS threshold_version,
        :label_map_version AS label_map_version,
        :expected_label_artifact_digest AS expected_label_artifact_digest,
        :unscorable_policy_version AS unscorable_policy_version,
        :feature_lookup_strategy AS feature_lookup_strategy,
        :feature_snapshot_pins_digest AS feature_snapshot_pins_digest,
        :target_population_contract_digest AS target_population_contract_digest,
        :staged_candidate_table AS staged_candidate_table,
        :staged_candidate_delta_version AS staged_candidate_delta_version,
        :staged_candidate_snapshot_digest AS staged_candidate_snapshot_digest,
        :bucket_width_hours AS bucket_width_hours,
        :min_baseline_buckets AS min_baseline_buckets,
        :min_bucket_rows AS min_bucket_rows,
        :min_expected_label_rows AS min_expected_label_rows,
        :z_score_threshold AS z_score_threshold
),
parameter_assertions AS (
    SELECT
        min_baseline_buckets IS NOT NULL
            AND NOT isnan(CAST(min_baseline_buckets AS DOUBLE))
            AND ABS(CAST(min_baseline_buckets AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_baseline_buckets = CAST(min_baseline_buckets AS BIGINT)
            AND min_baseline_buckets >= 2
            AND min_bucket_rows IS NOT NULL
            AND NOT isnan(CAST(min_bucket_rows AS DOUBLE))
            AND ABS(CAST(min_bucket_rows AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_bucket_rows = CAST(min_bucket_rows AS BIGINT)
            AND min_bucket_rows > 0
            AND min_expected_label_rows IS NOT NULL
            AND NOT isnan(CAST(min_expected_label_rows AS DOUBLE))
            AND ABS(CAST(min_expected_label_rows AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_expected_label_rows = CAST(min_expected_label_rows AS BIGINT)
            AND min_expected_label_rows >= 0
            AND z_score_threshold IS NOT NULL
            AND NOT isnan(z_score_threshold)
            AND ABS(z_score_threshold) < CAST('Infinity' AS DOUBLE)
            AND z_score_threshold > 0
            AND bucket_width_hours IS NOT NULL
            AND NOT isnan(CAST(bucket_width_hours AS DOUBLE))
            AND ABS(CAST(bucket_width_hours AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND bucket_width_hours = 1
            AND source_window_start = date_trunc('hour', source_window_start)
            AND source_window_end = date_trunc('hour', source_window_end)
            AND baseline_window_start = date_trunc('hour', baseline_window_start)
            AND source_window_end = source_window_start + INTERVAL 1 HOUR
            AND baseline_window_start < source_window_start
            AND source_window_end <= evaluation_as_of AS parameters_valid
    FROM params
),
latest_closed_manifest AS (
    SELECT manifest.*
    FROM staged_scoring_window_manifest AS manifest
    CROSS JOIN params
    WHERE manifest.window_state = 'closed'
        AND manifest.source_window_end <= params.evaluation_as_of
        AND manifest.source_table = params.source_table
        AND manifest.target_population_contract_digest
            = params.target_population_contract_digest
    QUALIFY RANK() OVER (
        ORDER BY manifest.source_window_end DESC, manifest.closed_at DESC
    ) = 1
),
matched_manifest AS (
    SELECT manifest.*
    FROM latest_closed_manifest AS manifest
    CROSS JOIN params
    WHERE manifest.scoring_run_id = params.scoring_run_id
        AND manifest.run_contract_digest = params.run_contract_digest
        AND manifest.source_table = params.source_table
        AND manifest.source_delta_version = params.source_delta_version
        AND manifest.source_window_start = params.source_window_start
        AND manifest.source_window_end = params.source_window_end
        AND manifest.expected_label_artifact_digest
            = params.expected_label_artifact_digest
        AND manifest.target_population_contract_digest
            = params.target_population_contract_digest
        AND manifest.staged_candidate_table = params.staged_candidate_table
        AND manifest.staged_candidate_delta_version = params.staged_candidate_delta_version
        AND manifest.staged_candidate_snapshot_digest = params.staged_candidate_snapshot_digest
),
matched_reconciliation AS (
    SELECT reconciliation.*
    FROM staged_scoring_reconciliation AS reconciliation
    CROSS JOIN params
    WHERE reconciliation.scoring_run_id = params.scoring_run_id
        AND reconciliation.run_contract_digest = params.run_contract_digest
        AND reconciliation.source_table = params.source_table
        AND reconciliation.source_delta_version = params.source_delta_version
        AND reconciliation.source_window_start = params.source_window_start
        AND reconciliation.source_window_end = params.source_window_end
        AND reconciliation.resolved_model_uri = params.resolved_model_uri
        AND reconciliation.model_version = params.model_version
        AND reconciliation.model_run_id = params.model_run_id
        AND reconciliation.threshold_version = params.threshold_version
        AND reconciliation.label_map_version = params.label_map_version
        AND reconciliation.expected_label_artifact_digest
            = params.expected_label_artifact_digest
        AND reconciliation.unscorable_policy_version = params.unscorable_policy_version
        AND reconciliation.feature_lookup_strategy = params.feature_lookup_strategy
        AND reconciliation.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest
        AND reconciliation.target_population_contract_digest
            = params.target_population_contract_digest
        AND reconciliation.staged_candidate_table = params.staged_candidate_table
        AND reconciliation.staged_candidate_delta_version
            = params.staged_candidate_delta_version
        AND reconciliation.staged_candidate_snapshot_digest
            = params.staged_candidate_snapshot_digest
        AND reconciliation.reconciliation_status = 'succeeded'
        AND reconciliation.reconciliation_complete = TRUE
),
prerequisite_assertions AS (
    SELECT
        (SELECT COUNT(*) FROM matched_manifest) AS matched_manifest_count,
        (SELECT COUNT(*) FROM matched_reconciliation) AS matched_reconciliation_count
),
expected_labels AS (
    SELECT label_name
    FROM versioned_expected_labels
    WHERE label_map_version = :label_map_version
),
expected_label_assertions AS (
    SELECT
        COUNT(*) AS expected_label_count,
        COUNT(DISTINCT label_name) AS distinct_expected_label_count,
        SHA2(TO_JSON(ARRAY_SORT(COLLECT_LIST(label_name))), 256)
            AS computed_expected_label_artifact_digest
    FROM expected_labels
),
gate_prerequisite_assertions AS (
    SELECT
        COALESCE(parameters.parameters_valid, FALSE)
            AND prerequisites.matched_manifest_count = 1
            AND prerequisites.matched_reconciliation_count = 1
            AND expected.expected_label_count > 0
            AND expected.expected_label_count = expected.distinct_expected_label_count
            AND expected.computed_expected_label_artifact_digest
                = params.expected_label_artifact_digest AS prerequisites_valid
    FROM parameter_assertions AS parameters
    CROSS JOIN prerequisite_assertions AS prerequisites
    CROSS JOIN expected_label_assertions AS expected
    CROSS JOIN params
),
candidate_contract AS (
    SELECT staged.*
    FROM staged_candidate_predictions AS staged
    INNER JOIN matched_manifest AS manifest
        ON staged.scoring_run_id = manifest.scoring_run_id
    INNER JOIN matched_reconciliation AS reconciliation
        ON staged.scoring_run_id = reconciliation.scoring_run_id
    CROSS JOIN params
    CROSS JOIN gate_prerequisite_assertions AS prerequisites
    WHERE staged.scoring_run_id = params.scoring_run_id
        AND prerequisites.prerequisites_valid
        AND staged.scoring_run_id = staged.run_contract_digest
        AND SHA2(staged.run_contract_json, 256) = staged.run_contract_digest
        AND staged.run_contract_digest = params.run_contract_digest
        AND staged.run_contract_json = params.run_contract_json
        AND staged.source_table = params.source_table
        AND staged.source_delta_version = params.source_delta_version
        AND staged.source_window_start = params.source_window_start
        AND staged.source_window_end = params.source_window_end
        AND staged.resolved_model_uri = params.resolved_model_uri
        AND staged.model_version = params.model_version
        AND staged.model_run_id = params.model_run_id
        AND staged.threshold_version = params.threshold_version
        AND staged.label_map_version = params.label_map_version
        AND staged.expected_label_artifact_digest = params.expected_label_artifact_digest
        AND staged.unscorable_policy_version = params.unscorable_policy_version
        AND staged.feature_lookup_strategy = params.feature_lookup_strategy
        AND staged.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest
        AND staged.target_population_contract_digest = params.target_population_contract_digest
        AND staged.staged_candidate_table = params.staged_candidate_table
        AND staged.staged_candidate_delta_version = params.staged_candidate_delta_version
        AND staged.staged_candidate_snapshot_digest = params.staged_candidate_snapshot_digest
        AND staged.observation_timestamp >= params.source_window_start
        AND staged.observation_timestamp < params.source_window_end
),
unknown_candidates AS (
    SELECT candidate.*, 'unknown_label' AS quarantine_reason
    FROM candidate_contract AS candidate
    LEFT ANTI JOIN expected_labels AS labels
        ON candidate.prediction = labels.label_name
    WHERE candidate.prediction IS NOT NULL
),
unknown_identified AS (
    SELECT
        SHA2(CONCAT('candidate:', scoring_run_id, ':', business_key), 256)
            AS quarantine_row_id,
        unknown_candidates.*
    FROM unknown_candidates
),
unknown_prepared AS (
    SELECT
        identified.*,
        SHA2(
            TO_JSON(
                NAMED_STRUCT(
                    'quarantine_row_id', quarantine_row_id,
                    'business_key', business_key,
                    'scoring_run_id', scoring_run_id,
                    'run_contract_digest', run_contract_digest,
                    'source_table', source_table,
                    'source_delta_version', source_delta_version,
                    'source_window_start', source_window_start,
                    'source_window_end', source_window_end,
                    'observation_timestamp', observation_timestamp,
                    'target_population_contract_digest',
                        target_population_contract_digest,
                    'staged_candidate_table', staged_candidate_table,
                    'staged_candidate_delta_version',
                        staged_candidate_delta_version,
                    'staged_candidate_snapshot_digest',
                        staged_candidate_snapshot_digest,
                    'resolved_model_uri', resolved_model_uri,
                    'model_version', model_version,
                    'model_run_id', model_run_id,
                    'label_map_version', label_map_version,
                    'expected_label_artifact_digest',
                        expected_label_artifact_digest,
                    'prediction', prediction,
                    'quarantine_reason', quarantine_reason
                ),
                MAP(
                    'timeZone', 'UTC',
                    'timestampFormat',
                        'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX'
                )
            ),
            256
        ) AS intent_content_digest
    FROM unknown_identified AS identified
),
unknown_summary AS (
    SELECT
        COUNT(*) AS actual_unknown_count,
        COUNT(DISTINCT quarantine_row_id) AS distinct_quarantine_row_count,
        COUNT_IF(
            NOT (
                business_key IS NOT NULL
                AND business_key = TRIM(business_key)
                AND LENGTH(business_key) BETWEEN 1 AND 512
                AND business_key
                    RLIKE '^-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}$'
            )
        ) AS invalid_business_key_count,
        COUNT_IF(intent_content_digest IS NULL) AS null_content_digest_count,
        SHA2(
            TO_JSON(ARRAY_SORT(COLLECT_LIST(intent_content_digest))),
            256
        ) AS actual_unknown_set_digest
    FROM unknown_prepared
),
quarantine_expected AS (
    SELECT
        CAST(:expected_unknown_label_count AS BIGINT)
            AS expected_unknown_label_count,
        CAST(:expected_unknown_label_set_digest AS STRING)
            AS expected_unknown_label_set_digest
),
quarantine_assertions AS (
    SELECT
        ASSERT_TRUE(
            COALESCE(prerequisites.prerequisites_valid, FALSE)
                AND expected.expected_unknown_label_count IS NOT NULL
                AND expected.expected_unknown_label_count >= 0
                AND expected.expected_unknown_label_set_digest
                    RLIKE '^[0-9a-f]{64}$'
                AND summary.actual_unknown_count
                    = summary.distinct_quarantine_row_count
                AND summary.invalid_business_key_count = 0
                AND summary.null_content_digest_count = 0
                AND summary.actual_unknown_count
                    = expected.expected_unknown_label_count
                AND summary.actual_unknown_set_digest
                    = expected.expected_unknown_label_set_digest,
            'unknown-label quarantine intent assertion failed'
        ) AS asserted
    FROM gate_prerequisite_assertions AS prerequisites
    CROSS JOIN unknown_summary AS summary
    CROSS JOIN quarantine_expected AS expected
),
intent_source AS (
    SELECT
        SHA2(CONCAT('header:', params.scoring_run_id), 256)
            AS intent_record_id,
        'header' AS record_kind,
        CAST(NULL AS STRING) AS quarantine_row_id,
        CAST(NULL AS STRING) AS intent_content_digest,
        CAST(NULL AS STRING) AS business_key,
        params.scoring_run_id,
        params.run_contract_digest,
        params.source_table,
        params.source_delta_version,
        params.source_window_start,
        params.source_window_end,
        CAST(NULL AS TIMESTAMP) AS observation_timestamp,
        params.target_population_contract_digest,
        params.staged_candidate_table,
        params.staged_candidate_delta_version,
        params.staged_candidate_snapshot_digest,
        params.resolved_model_uri,
        params.model_version,
        params.model_run_id,
        params.label_map_version,
        params.expected_label_artifact_digest,
        CAST(NULL AS STRING) AS prediction,
        CAST(NULL AS STRING) AS quarantine_reason,
        expected.expected_unknown_label_count,
        expected.expected_unknown_label_set_digest,
        current_timestamp() AS intent_created_at
    FROM params
    CROSS JOIN quarantine_expected AS expected
    CROSS JOIN quarantine_assertions AS assertions
    WHERE assertions.asserted IS NULL

    UNION ALL

    SELECT
        prepared.quarantine_row_id AS intent_record_id,
        'unknown_label' AS record_kind,
        prepared.quarantine_row_id,
        prepared.intent_content_digest,
        prepared.business_key,
        prepared.scoring_run_id,
        prepared.run_contract_digest,
        prepared.source_table,
        prepared.source_delta_version,
        prepared.source_window_start,
        prepared.source_window_end,
        prepared.observation_timestamp,
        prepared.target_population_contract_digest,
        prepared.staged_candidate_table,
        prepared.staged_candidate_delta_version,
        prepared.staged_candidate_snapshot_digest,
        prepared.resolved_model_uri,
        prepared.model_version,
        prepared.model_run_id,
        prepared.label_map_version,
        prepared.expected_label_artifact_digest,
        prepared.prediction,
        prepared.quarantine_reason,
        expected.expected_unknown_label_count,
        expected.expected_unknown_label_set_digest,
        current_timestamp() AS intent_created_at
    FROM unknown_prepared AS prepared
    CROSS JOIN quarantine_expected AS expected
    CROSS JOIN quarantine_assertions AS assertions
    WHERE assertions.asserted IS NULL
),
intent_source_assertions AS (
    SELECT ASSERT_TRUE(
        COUNT(*) = COUNT(DISTINCT intent_record_id),
        'unknown-label quarantine intent record IDs are not unique'
    ) AS asserted
    FROM intent_source
)
SELECT
    intent_record_id,
    record_kind,
    quarantine_row_id,
    intent_content_digest,
    business_key,
    scoring_run_id,
    run_contract_digest,
    source_table,
    source_delta_version,
    source_window_start,
    source_window_end,
    observation_timestamp,
    target_population_contract_digest,
    staged_candidate_table,
    staged_candidate_delta_version,
    staged_candidate_snapshot_digest,
    resolved_model_uri,
    model_version,
    model_run_id,
    label_map_version,
    expected_label_artifact_digest,
    prediction,
    quarantine_reason,
    expected_unknown_label_count,
    expected_unknown_label_set_digest,
    intent_created_at
FROM intent_source
CROSS JOIN intent_source_assertions AS source_assertions
WHERE source_assertions.asserted IS NULL
) AS intent_source
    ON intent_target.intent_record_id = intent_source.intent_record_id
WHEN NOT MATCHED THEN INSERT (
    intent_record_id,
    record_kind,
    quarantine_row_id,
    intent_content_digest,
    business_key,
    scoring_run_id,
    run_contract_digest,
    source_table,
    source_delta_version,
    source_window_start,
    source_window_end,
    observation_timestamp,
    target_population_contract_digest,
    staged_candidate_table,
    staged_candidate_delta_version,
    staged_candidate_snapshot_digest,
    resolved_model_uri,
    model_version,
    model_run_id,
    label_map_version,
    expected_label_artifact_digest,
    prediction,
    quarantine_reason,
    expected_unknown_label_count,
    expected_unknown_label_set_digest,
    intent_created_at
) VALUES (
    intent_source.intent_record_id,
    intent_source.record_kind,
    intent_source.quarantine_row_id,
    intent_source.intent_content_digest,
    intent_source.business_key,
    intent_source.scoring_run_id,
    intent_source.run_contract_digest,
    intent_source.source_table,
    intent_source.source_delta_version,
    intent_source.source_window_start,
    intent_source.source_window_end,
    intent_source.observation_timestamp,
    intent_source.target_population_contract_digest,
    intent_source.staged_candidate_table,
    intent_source.staged_candidate_delta_version,
    intent_source.staged_candidate_snapshot_digest,
    intent_source.resolved_model_uri,
    intent_source.model_version,
    intent_source.model_run_id,
    intent_source.label_map_version,
    intent_source.expected_label_artifact_digest,
    intent_source.prediction,
    intent_source.quarantine_reason,
    intent_source.expected_unknown_label_count,
    intent_source.expected_unknown_label_set_digest,
    intent_source.intent_created_at
);
```

The header is always inserted, including for an expected zero-row set. The named expected count and content-set digest must come from the successful gate output before this statement starts. A retry uses those same values. If an upstream relation changes, the recomputed count or content-set digest fails before mutation; existing intent rows are neither updated nor deleted. Every candidate record is `BLOCK_PUBLISH_UNKNOWN_LABEL`; it is not silently excluded from the denominator.

## Gate The Staged Candidate

The gate reads the same contract-bound staged relation. Historical predictions provide baseline buckets only; unrelated historical rows never become the candidate bucket.

```sql
WITH params AS (
    SELECT
        :scoring_run_id AS scoring_run_id,
        :run_contract_digest AS run_contract_digest,
        :run_contract_json AS run_contract_json,
        :source_table AS source_table,
        :source_delta_version AS source_delta_version,
        :source_window_start AS source_window_start,
        :source_window_end AS source_window_end,
        :baseline_window_start AS baseline_window_start,
        :evaluation_as_of AS evaluation_as_of,
        :resolved_model_uri AS resolved_model_uri,
        :model_version AS model_version,
        :model_run_id AS model_run_id,
        :threshold_version AS threshold_version,
        :label_map_version AS label_map_version,
        :expected_label_artifact_digest AS expected_label_artifact_digest,
        :unscorable_policy_version AS unscorable_policy_version,
        :feature_lookup_strategy AS feature_lookup_strategy,
        :feature_snapshot_pins_digest AS feature_snapshot_pins_digest,
        :target_population_contract_digest AS target_population_contract_digest,
        :staged_candidate_table AS staged_candidate_table,
        :staged_candidate_delta_version AS staged_candidate_delta_version,
        :staged_candidate_snapshot_digest AS staged_candidate_snapshot_digest,
        :bucket_width_hours AS bucket_width_hours,
        :min_baseline_buckets AS min_baseline_buckets,
        :min_bucket_rows AS min_bucket_rows,
        :min_expected_label_rows AS min_expected_label_rows,
        :z_score_threshold AS z_score_threshold
),
parameter_assertions AS (
    SELECT
        min_baseline_buckets IS NOT NULL
            AND NOT isnan(CAST(min_baseline_buckets AS DOUBLE))
            AND ABS(CAST(min_baseline_buckets AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_baseline_buckets = CAST(min_baseline_buckets AS BIGINT)
            AND min_baseline_buckets >= 2
            AND min_bucket_rows IS NOT NULL
            AND NOT isnan(CAST(min_bucket_rows AS DOUBLE))
            AND ABS(CAST(min_bucket_rows AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_bucket_rows = CAST(min_bucket_rows AS BIGINT)
            AND min_bucket_rows > 0
            AND min_expected_label_rows IS NOT NULL
            AND NOT isnan(CAST(min_expected_label_rows AS DOUBLE))
            AND ABS(CAST(min_expected_label_rows AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND min_expected_label_rows = CAST(min_expected_label_rows AS BIGINT)
            AND min_expected_label_rows >= 0
            AND z_score_threshold IS NOT NULL
            AND NOT isnan(z_score_threshold)
            AND ABS(z_score_threshold) < CAST('Infinity' AS DOUBLE)
            AND z_score_threshold > 0
            AND bucket_width_hours IS NOT NULL
            AND NOT isnan(CAST(bucket_width_hours AS DOUBLE))
            AND ABS(CAST(bucket_width_hours AS DOUBLE)) < CAST('Infinity' AS DOUBLE)
            AND bucket_width_hours = 1
            AND source_window_start = date_trunc('hour', source_window_start)
            AND source_window_end = date_trunc('hour', source_window_end)
            AND baseline_window_start = date_trunc('hour', baseline_window_start)
            AND source_window_end = source_window_start + INTERVAL 1 HOUR
            AND baseline_window_start < source_window_start
            AND source_window_end <= evaluation_as_of AS parameters_valid
    FROM params
),
latest_closed_manifest AS (
    SELECT manifest.*
    FROM staged_scoring_window_manifest AS manifest
    CROSS JOIN params
    WHERE manifest.window_state = 'closed'
        AND manifest.source_window_end <= params.evaluation_as_of
        AND manifest.source_table = params.source_table
        AND manifest.target_population_contract_digest
            = params.target_population_contract_digest
    QUALIFY RANK() OVER (
        ORDER BY manifest.source_window_end DESC, manifest.closed_at DESC
    ) = 1
),
matched_manifest AS (
    SELECT manifest.*
    FROM latest_closed_manifest AS manifest
    CROSS JOIN params
    WHERE manifest.scoring_run_id = params.scoring_run_id
        AND manifest.run_contract_digest = params.run_contract_digest
        AND manifest.source_table = params.source_table
        AND manifest.source_delta_version = params.source_delta_version
        AND manifest.source_window_start = params.source_window_start
        AND manifest.source_window_end = params.source_window_end
        AND manifest.expected_label_artifact_digest
            = params.expected_label_artifact_digest
        AND manifest.target_population_contract_digest
            = params.target_population_contract_digest
        AND manifest.staged_candidate_table = params.staged_candidate_table
        AND manifest.staged_candidate_delta_version = params.staged_candidate_delta_version
        AND manifest.staged_candidate_snapshot_digest = params.staged_candidate_snapshot_digest
),
matched_reconciliation AS (
    SELECT reconciliation.*
    FROM staged_scoring_reconciliation AS reconciliation
    CROSS JOIN params
    WHERE reconciliation.scoring_run_id = params.scoring_run_id
        AND reconciliation.run_contract_digest = params.run_contract_digest
        AND reconciliation.source_table = params.source_table
        AND reconciliation.source_delta_version = params.source_delta_version
        AND reconciliation.source_window_start = params.source_window_start
        AND reconciliation.source_window_end = params.source_window_end
        AND reconciliation.resolved_model_uri = params.resolved_model_uri
        AND reconciliation.model_version = params.model_version
        AND reconciliation.model_run_id = params.model_run_id
        AND reconciliation.threshold_version = params.threshold_version
        AND reconciliation.label_map_version = params.label_map_version
        AND reconciliation.expected_label_artifact_digest
            = params.expected_label_artifact_digest
        AND reconciliation.unscorable_policy_version = params.unscorable_policy_version
        AND reconciliation.feature_lookup_strategy = params.feature_lookup_strategy
        AND reconciliation.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest
        AND reconciliation.target_population_contract_digest
            = params.target_population_contract_digest
        AND reconciliation.staged_candidate_table = params.staged_candidate_table
        AND reconciliation.staged_candidate_delta_version
            = params.staged_candidate_delta_version
        AND reconciliation.staged_candidate_snapshot_digest
            = params.staged_candidate_snapshot_digest
        AND reconciliation.reconciliation_status = 'succeeded'
        AND reconciliation.reconciliation_complete = TRUE
),
prerequisite_assertions AS (
    SELECT
        (SELECT COUNT(*) FROM matched_manifest) AS matched_manifest_count,
        (SELECT COUNT(*) FROM matched_reconciliation) AS matched_reconciliation_count
),
reconciliation_snapshot AS (
    SELECT
        MAX(expected_row_count) AS expected_row_count,
        MAX(staged_row_count) AS staged_row_count,
        MAX(scoreable_row_count) AS scoreable_row_count,
        MAX(unscorable_row_count) AS unscorable_row_count,
        MAX(unknown_label_count) AS unknown_label_count,
        MAX(unexpected_null_count) AS unexpected_null_count
    FROM matched_reconciliation
),
expected_labels AS (
    SELECT label_name
    FROM versioned_expected_labels
    WHERE label_map_version = :label_map_version
),
expected_label_assertions AS (
    SELECT
        COUNT(*) AS expected_label_count,
        COUNT(DISTINCT label_name) AS distinct_expected_label_count,
        SHA2(TO_JSON(ARRAY_SORT(COLLECT_LIST(label_name))), 256)
            AS computed_expected_label_artifact_digest
    FROM expected_labels
),
gate_label_universe AS (
    SELECT label_name, FALSE AS is_contract_gate_row
    FROM expected_labels
    UNION ALL
    SELECT CAST(NULL AS STRING) AS label_name, TRUE AS is_contract_gate_row
    FROM expected_label_assertions
    WHERE expected_label_count = 0
),
allowed_unscorable_reasons AS (
    SELECT DISTINCT normalized_reason
    FROM versioned_unscorable_policy
    WHERE unscorable_policy_version = :unscorable_policy_version
),
unscorable_policy_assertions AS (
    SELECT
        COUNT(*) AS policy_reason_count,
        COUNT(DISTINCT normalized_reason) AS distinct_policy_reason_count
    FROM versioned_unscorable_policy
    WHERE unscorable_policy_version = :unscorable_policy_version
),
gate_prerequisite_assertions AS (
    SELECT
        COALESCE(parameters.parameters_valid, FALSE)
            AND prerequisites.matched_manifest_count = 1
            AND prerequisites.matched_reconciliation_count = 1
            AND expected.expected_label_count > 0
            AND expected.expected_label_count = expected.distinct_expected_label_count
            AND expected.computed_expected_label_artifact_digest
                = params.expected_label_artifact_digest AS prerequisites_valid
    FROM parameter_assertions AS parameters
    CROSS JOIN prerequisite_assertions AS prerequisites
    CROSS JOIN expected_label_assertions AS expected
    CROSS JOIN params
),
candidate_contract AS (
    SELECT staged.*
    FROM staged_candidate_predictions AS staged
    CROSS JOIN params
    INNER JOIN matched_manifest AS manifest
        ON staged.scoring_run_id = manifest.scoring_run_id
    INNER JOIN matched_reconciliation AS reconciliation
        ON staged.scoring_run_id = reconciliation.scoring_run_id
    CROSS JOIN gate_prerequisite_assertions AS gate_prerequisites
    WHERE staged.scoring_run_id = params.scoring_run_id
        AND gate_prerequisites.prerequisites_valid
        AND staged.scoring_run_id = staged.run_contract_digest
        AND SHA2(staged.run_contract_json, 256) = staged.run_contract_digest
        AND staged.run_contract_digest = params.run_contract_digest
        AND staged.run_contract_json = params.run_contract_json
        AND staged.source_table = params.source_table
        AND staged.source_delta_version = params.source_delta_version
        AND staged.source_window_start = params.source_window_start
        AND staged.source_window_end = params.source_window_end
        AND staged.resolved_model_uri = params.resolved_model_uri
        AND staged.model_version = params.model_version
        AND staged.model_run_id = params.model_run_id
        AND staged.threshold_version = params.threshold_version
        AND staged.label_map_version = params.label_map_version
        AND staged.expected_label_artifact_digest = params.expected_label_artifact_digest
        AND staged.unscorable_policy_version = params.unscorable_policy_version
        AND staged.feature_lookup_strategy = params.feature_lookup_strategy
        AND staged.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest
        AND staged.target_population_contract_digest = params.target_population_contract_digest
        AND staged.staged_candidate_table = params.staged_candidate_table
        AND staged.staged_candidate_delta_version = params.staged_candidate_delta_version
        AND staged.staged_candidate_snapshot_digest = params.staged_candidate_snapshot_digest
        AND staged.observation_timestamp >= params.source_window_start
        AND staged.observation_timestamp < params.source_window_end
),
candidate_classified AS (
    SELECT
        candidate.*,
        NULLIF(TRIM(CAST(candidate.unscorable_reason AS STRING)), '')
            AS normalized_unscorable_reason,
        CASE
            WHEN candidate.prediction IS NOT NULL
                AND candidate.score IS NOT NULL
                AND NULLIF(TRIM(CAST(candidate.unscorable_reason AS STRING)), '') IS NULL
                THEN 'scoreable'
            WHEN candidate.prediction IS NULL
                AND candidate.score IS NULL
                AND policy.normalized_reason IS NOT NULL
                THEN 'unscorable'
            ELSE 'unexpected_null'
        END AS outcome_kind
    FROM candidate_contract AS candidate
    LEFT JOIN allowed_unscorable_reasons AS policy
        ON NULLIF(TRIM(CAST(candidate.unscorable_reason AS STRING)), '')
            = policy.normalized_reason
),
candidate_outcomes AS (
    SELECT
        COUNT(*) AS staged_row_count,
        COUNT_IF(outcome_kind = 'scoreable') AS scoreable_row_count,
        COUNT_IF(outcome_kind = 'unscorable') AS unscorable_row_count,
        COUNT_IF(outcome_kind = 'unexpected_null') AS unexpected_null_count
    FROM candidate_classified
),
candidate_totals AS (
    SELECT COUNT(*) AS bucket_rows
    FROM candidate_classified
),
candidate_counts AS (
    SELECT prediction AS label_name, COUNT(*) AS label_rows
    FROM candidate_classified
    WHERE outcome_kind = 'scoreable'
    GROUP BY prediction
),
candidate_labels AS (
    SELECT
        labels.label_name,
        labels.is_contract_gate_row,
        totals.bucket_rows,
        COALESCE(counts.label_rows, 0) AS label_rows,
        CASE
            WHEN totals.bucket_rows = 0 THEN 0.0
            ELSE COALESCE(counts.label_rows, 0) * 1.0 / totals.bucket_rows
        END AS label_share
    FROM gate_label_universe AS labels
    CROSS JOIN candidate_totals AS totals
    LEFT JOIN candidate_counts AS counts
        ON labels.label_name = counts.label_name
),
unknown_candidates AS (
    SELECT candidate.*, 'unknown_label' AS quarantine_reason
    FROM candidate_contract AS candidate
    LEFT ANTI JOIN expected_labels AS labels
        ON candidate.prediction = labels.label_name
    WHERE candidate.prediction IS NOT NULL
),
unknown_identified AS (
    SELECT
        SHA2(CONCAT('candidate:', scoring_run_id, ':', business_key), 256)
            AS quarantine_row_id,
        unknown_candidates.*
    FROM unknown_candidates
),
unknown_prepared AS (
    SELECT
        identified.*,
        SHA2(
            TO_JSON(
                NAMED_STRUCT(
                    'quarantine_row_id', quarantine_row_id,
                    'business_key', business_key,
                    'scoring_run_id', scoring_run_id,
                    'run_contract_digest', run_contract_digest,
                    'source_table', source_table,
                    'source_delta_version', source_delta_version,
                    'source_window_start', source_window_start,
                    'source_window_end', source_window_end,
                    'observation_timestamp', observation_timestamp,
                    'target_population_contract_digest',
                        target_population_contract_digest,
                    'staged_candidate_table', staged_candidate_table,
                    'staged_candidate_delta_version',
                        staged_candidate_delta_version,
                    'staged_candidate_snapshot_digest',
                        staged_candidate_snapshot_digest,
                    'resolved_model_uri', resolved_model_uri,
                    'model_version', model_version,
                    'model_run_id', model_run_id,
                    'label_map_version', label_map_version,
                    'expected_label_artifact_digest',
                        expected_label_artifact_digest,
                    'prediction', prediction,
                    'quarantine_reason', quarantine_reason
                ),
                MAP(
                    'timeZone', 'UTC',
                    'timestampFormat',
                        'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX'
                )
            ),
            256
        ) AS intent_content_digest
    FROM unknown_identified AS identified
),
candidate_unknown AS (
    SELECT
        COUNT(*) AS unknown_label_count,
        SHA2(
            TO_JSON(ARRAY_SORT(COLLECT_LIST(intent_content_digest))),
            256
        ) AS unknown_label_set_digest
    FROM unknown_prepared
),
baseline_source AS (
    SELECT published.*
    FROM published_predictions AS published
    CROSS JOIN params
    WHERE published.observation_timestamp >= params.baseline_window_start
        AND published.observation_timestamp < params.source_window_start
        AND published.target_population_contract_digest = params.target_population_contract_digest
        AND published.label_map_version = params.label_map_version
),
baseline_totals AS (
    SELECT
        date_trunc('hour', observation_timestamp) AS bucket_ts,
        COUNT(*) AS bucket_rows
    FROM baseline_source
    GROUP BY date_trunc('hour', observation_timestamp)
),
baseline_counts AS (
    SELECT
        date_trunc('hour', observation_timestamp) AS bucket_ts,
        prediction AS label_name,
        COUNT(*) AS label_rows
    FROM baseline_source
    WHERE prediction IS NOT NULL
        AND score IS NOT NULL
    GROUP BY date_trunc('hour', observation_timestamp), prediction
),
baseline_label_buckets AS (
    SELECT
        totals.bucket_ts,
        labels.label_name,
        totals.bucket_rows,
        COALESCE(counts.label_rows, 0) * 1.0 / totals.bucket_rows AS label_share
    FROM baseline_totals AS totals
    CROSS JOIN expected_labels AS labels
    LEFT JOIN baseline_counts AS counts
        ON counts.bucket_ts = totals.bucket_ts
        AND counts.label_name = labels.label_name
),
baseline AS (
    SELECT
        buckets.label_name,
        AVG(buckets.label_share) AS mean_share,
        STDDEV_SAMP(buckets.label_share) AS std_share,
        COUNT(*) AS observed_buckets
    FROM baseline_label_buckets AS buckets
    CROSS JOIN params
    WHERE buckets.bucket_rows >= params.min_bucket_rows
    GROUP BY buckets.label_name
)
SELECT
    params.scoring_run_id AS gate_scoring_run_id,
    params.run_contract_digest AS gate_run_contract_digest,
    candidate.label_name,
    candidate.is_contract_gate_row,
    candidate.bucket_rows,
    candidate.label_rows,
    candidate.label_share,
    baseline.mean_share,
    baseline.std_share,
    expected.expected_label_count,
    reconciliation.expected_row_count,
    outcomes.staged_row_count,
    outcomes.scoreable_row_count,
    outcomes.unscorable_row_count,
    unknown.unknown_label_count,
    unknown.unknown_label_set_digest,
    outcomes.unexpected_null_count,
    CASE
        WHEN baseline.std_share > 0
            THEN (candidate.label_share - baseline.mean_share) / baseline.std_share
        ELSE NULL
    END AS share_z_score,
    CASE
        WHEN NOT COALESCE(parameter_assertions.parameters_valid, FALSE)
            THEN 'BLOCK_PUBLISH_INVALID_PARAMETERS'
        WHEN prerequisites.matched_manifest_count <> 1
            THEN 'BLOCK_PUBLISH_MANIFEST_MISMATCH'
        WHEN prerequisites.matched_reconciliation_count <> 1
            THEN 'BLOCK_PUBLISH_RECONCILIATION_MISSING'
        WHEN expected.expected_label_count = 0
            OR expected.expected_label_count <> expected.distinct_expected_label_count
            OR expected.computed_expected_label_artifact_digest
                <> params.expected_label_artifact_digest
            THEN 'BLOCK_PUBLISH_EXPECTED_LABEL_CONTRACT'
        WHEN policy.policy_reason_count <> policy.distinct_policy_reason_count
            THEN 'BLOCK_PUBLISH_UNSCORABLE_POLICY_CONTRACT'
        WHEN reconciliation.expected_row_count IS NULL
            OR reconciliation.staged_row_count IS NULL
            OR reconciliation.scoreable_row_count IS NULL
            OR reconciliation.unscorable_row_count IS NULL
            OR reconciliation.unknown_label_count IS NULL
            OR reconciliation.unexpected_null_count IS NULL
            OR reconciliation.expected_row_count <> outcomes.staged_row_count
            OR reconciliation.staged_row_count <> outcomes.staged_row_count
            OR reconciliation.scoreable_row_count <> outcomes.scoreable_row_count
            OR reconciliation.unscorable_row_count <> outcomes.unscorable_row_count
            OR reconciliation.unknown_label_count <> unknown.unknown_label_count
            OR reconciliation.unexpected_null_count <> outcomes.unexpected_null_count
            THEN 'BLOCK_PUBLISH_RECONCILIATION_MISMATCH'
        WHEN outcomes.unexpected_null_count > 0
            THEN 'BLOCK_PUBLISH_UNEXPECTED_NULL'
        WHEN unknown.unknown_label_count > 0
            THEN 'BLOCK_PUBLISH_UNKNOWN_LABEL'
        WHEN baseline.observed_buckets IS NULL
            OR baseline.observed_buckets < params.min_baseline_buckets
            THEN 'insufficient_baseline'
        WHEN baseline.std_share IS NULL
            THEN 'insufficient_baseline'
        WHEN candidate.bucket_rows < params.min_bucket_rows
            THEN 'insufficient_current_sample'
        WHEN baseline.std_share = 0 AND candidate.label_share <> baseline.mean_share
            THEN 'BLOCK_PUBLISH_ZERO_VARIANCE_SHIFT'
        WHEN baseline.mean_share * candidate.bucket_rows < params.min_expected_label_rows
            THEN 'rare_label'
        WHEN baseline.std_share = 0
            THEN 'ok_zero_variance_unchanged'
        WHEN ABS((candidate.label_share - baseline.mean_share) / baseline.std_share)
            > params.z_score_threshold
            THEN 'BLOCK_PUBLISH_TWO_SIDED_DRIFT'
        ELSE 'ok'
    END AS publish_gate
FROM candidate_labels AS candidate
LEFT JOIN baseline USING (label_name)
CROSS JOIN candidate_unknown AS unknown
CROSS JOIN candidate_outcomes AS outcomes
CROSS JOIN expected_label_assertions AS expected
CROSS JOIN unscorable_policy_assertions AS policy
CROSS JOIN prerequisite_assertions AS prerequisites
CROSS JOIN reconciliation_snapshot AS reconciliation
CROSS JOIN parameter_assertions
CROSS JOIN params;
```

## Derive One Run-Level Publication Decision

Persist the label-gate query above as `label_gate_results` for the same staged snapshot. Derive exactly one admission decision; merge never interprets label statuses directly.

The projection helper accepts only this closed JSON row shape; no fields may be added or omitted:

```json
{
  "gate_scoring_run_id": "<64 lowercase hex>",
  "gate_run_contract_digest": "<same 64 lowercase hex>",
  "label_name": "ham",
  "is_contract_gate_row": false,
  "bucket_rows": 100,
  "label_rows": 90,
  "label_share": 0.9,
  "mean_share": 0.88,
  "std_share": 0.02,
  "expected_label_count": 2,
  "expected_row_count": 100,
  "staged_row_count": 100,
  "scoreable_row_count": 100,
  "unscorable_row_count": 0,
  "unknown_label_count": 0,
  "unknown_label_set_digest": "<64 lowercase hex>",
  "unexpected_null_count": 0,
  "share_z_score": 1.0,
  "publish_gate": "ok"
}
```

`label_name`, `expected_row_count`, `mean_share`, `std_share`, and `share_z_score` may be JSON null only where the SQL output permits it. Counts are non-negative signed 64-bit integers. `unknown_label_set_digest` is the same deterministic lowercase SHA-256 on every row. Diagnostics are finite numbers; exact integers have absolute value at most `10^308`. Larger exact integers, NaN, and infinities are malformed. From the installed skill root, pass the JSON array without projecting it by hand:

```powershell
$gateRows = Get-Content -Raw .\label-gate-results.json
python scripts/derive_publication_decision.py `
  --expected-scoring-run-id $expectedRunId `
  --expected-run-contract-digest $expectedRunId `
  --label-gate-query-results-json $gateRows
```

The helper validates the full row and projects exactly `gate_scoring_run_id`, `gate_run_contract_digest`, and `publish_gate` before invoking the strict core. For the diagnostic-rich path, its result also returns the validated run-level `unknown_label_count` and `unknown_label_set_digest` unchanged. Bind those exact values into intent materialization; never reconstruct either from a later read. Use `--gate-results-json` only when the caller already owns that exact three-field schema.

```sql
WITH expected_contract AS (
    SELECT
        CAST(:scoring_run_id AS STRING) AS expected_scoring_run_id,
        CAST(:run_contract_digest AS STRING) AS expected_run_contract_digest
),
expected_parameter_assertions AS (
    SELECT
        expected_scoring_run_id IS NOT NULL
            AND expected_run_contract_digest IS NOT NULL
            AND expected_scoring_run_id = TRIM(expected_scoring_run_id)
            AND expected_run_contract_digest = TRIM(expected_run_contract_digest)
            AND expected_scoring_run_id RLIKE '^[0-9a-f]{64}$'
            AND expected_run_contract_digest RLIKE '^[0-9a-f]{64}$'
            AND expected_scoring_run_id = expected_run_contract_digest
                AS expected_parameters_valid,
        expected_scoring_run_id,
        expected_run_contract_digest
    FROM expected_contract
),
run_gate_summary AS (
    SELECT
        (SELECT expected_parameters_valid FROM expected_parameter_assertions)
            AS expected_parameters_valid,
        COUNT(*) AS gate_row_count,
        COUNT_IF(
            gate_scoring_run_id IS DISTINCT FROM (
                SELECT expected_scoring_run_id FROM expected_parameter_assertions
            )
            OR gate_run_contract_digest IS DISTINCT FROM (
                SELECT expected_run_contract_digest FROM expected_parameter_assertions
            )
            OR gate_scoring_run_id IS DISTINCT FROM gate_run_contract_digest
            OR NOT COALESCE(gate_scoring_run_id RLIKE '^[0-9a-f]{64}$', FALSE)
            OR NOT COALESCE(gate_run_contract_digest RLIKE '^[0-9a-f]{64}$', FALSE)
        ) AS contract_mismatch_count,
        COUNT_IF(
            publish_gate IS NULL
            OR publish_gate NOT IN ('ok', 'ok_zero_variance_unchanged')
        ) AS non_allow_status_count
    FROM label_gate_results
)
SELECT
    CASE
        WHEN NOT COALESCE(expected_parameters_valid, FALSE) THEN 'BLOCK_PUBLISH'
        WHEN gate_row_count = 0 THEN 'BLOCK_PUBLISH'
        WHEN contract_mismatch_count > 0 THEN 'BLOCK_PUBLISH'
        WHEN non_allow_status_count > 0 THEN 'BLOCK_PUBLISH'
        ELSE 'ALLOW_PUBLISH'
    END AS publication_decision,
    CASE
        WHEN NOT COALESCE(expected_parameters_valid, FALSE)
            THEN 'invalid_expected_contract'
        WHEN gate_row_count = 0 THEN 'empty_gate_result'
        WHEN contract_mismatch_count > 0 THEN 'mixed_gate_contract'
        WHEN non_allow_status_count > 0 THEN 'non_allow_status'
        ELSE 'all_label_gates_allow'
    END AS publication_reason,
    gate_row_count
FROM run_gate_summary;
```

The canonical run identity is exactly 64 lowercase hexadecimal characters, has no surrounding whitespace, and is identical to the run-contract SHA-256 digest. The allow set is exactly `{ok, ok_zero_variance_unchanged}`. A nonempty mixture of those two statuses is allowed. Invalid expected identity, empty results, invalid/NULL/blank row identity, NULL or unknown statuses, any block/insufficient/rare status, or any run/digest mismatch produce `BLOCK_PUBLISH` with the fixed reason selected above. Persist the decision and reason with the gated staged table/version/digest.

## Persist And Reconcile Unknown Labels

Create both tables from [the standalone DDL](../assets/scoring-quarantine-table-ddl.sql) before the scoring job. Bind `${catalog}`, `${schema}`, `${quarantine_intent_table}`, and `${quarantine_table}` through deployment. The DDL contains no value-parameter markers. Use the same serialized writer or run-contract lock as the prediction merge.

The first DML statement above is the only operation allowed to read `staged_candidate_predictions`, manifest, reconciliation, or expected-label relations for quarantine persistence. It writes one durable header plus the exact candidate set. After it completes, verify the materialized intent using only its run, snapshot, expected count, and expected content-set digest. This catches partial retries, duplicate records, mutated content, and matching-key conflicts without consulting mutable upstream state.

```sql
WITH expected_contract AS (
    SELECT
        CAST(:scoring_run_id AS STRING) AS scoring_run_id,
        CAST(:run_contract_digest AS STRING) AS run_contract_digest,
        CAST(:staged_candidate_delta_version AS BIGINT)
            AS staged_candidate_delta_version,
        CAST(:staged_candidate_snapshot_digest AS STRING)
            AS staged_candidate_snapshot_digest,
        CAST(:expected_unknown_label_count AS BIGINT)
            AS expected_unknown_label_count,
        CAST(:expected_unknown_label_set_digest AS STRING)
            AS expected_unknown_label_set_digest
),
pinned_intent AS (
    SELECT intent.*
    FROM ${catalog}.${schema}.${quarantine_intent_table} AS intent
    CROSS JOIN expected_contract AS expected
    WHERE intent.scoring_run_id = expected.scoring_run_id
),
verified_intent AS (
    SELECT
        intent.*,
        CASE
            WHEN record_kind = 'unknown_label' THEN SHA2(
                TO_JSON(
                    NAMED_STRUCT(
                        'quarantine_row_id', quarantine_row_id,
                        'business_key', business_key,
                        'scoring_run_id', scoring_run_id,
                        'run_contract_digest', run_contract_digest,
                        'source_table', source_table,
                        'source_delta_version', source_delta_version,
                        'source_window_start', source_window_start,
                        'source_window_end', source_window_end,
                        'observation_timestamp', observation_timestamp,
                        'target_population_contract_digest',
                            target_population_contract_digest,
                        'staged_candidate_table', staged_candidate_table,
                        'staged_candidate_delta_version',
                            staged_candidate_delta_version,
                        'staged_candidate_snapshot_digest',
                            staged_candidate_snapshot_digest,
                        'resolved_model_uri', resolved_model_uri,
                        'model_version', model_version,
                        'model_run_id', model_run_id,
                        'label_map_version', label_map_version,
                        'expected_label_artifact_digest',
                            expected_label_artifact_digest,
                    'prediction', prediction,
                    'quarantine_reason', quarantine_reason
                ),
                MAP(
                    'timeZone', 'UTC',
                    'timestampFormat',
                        'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX'
                )
            ),
                256
            )
        END AS recomputed_content_digest
    FROM pinned_intent AS intent
),
intent_summary AS (
    SELECT
        COUNT(*) AS intent_record_count,
        COUNT_IF(record_kind = 'header') AS header_count,
        COUNT_IF(record_kind = 'unknown_label') AS candidate_count,
        COUNT(DISTINCT CASE
            WHEN record_kind = 'unknown_label' THEN quarantine_row_id
        END) AS distinct_candidate_count,
        COUNT_IF(
            record_kind = 'unknown_label'
            AND intent_content_digest IS DISTINCT FROM recomputed_content_digest
        ) AS content_mismatch_count,
        COUNT_IF(
            record_kind NOT IN ('header', 'unknown_label')
            OR run_contract_digest IS DISTINCT FROM expected.run_contract_digest
            OR staged_candidate_delta_version
                IS DISTINCT FROM expected.staged_candidate_delta_version
            OR staged_candidate_snapshot_digest
                IS DISTINCT FROM expected.staged_candidate_snapshot_digest
            OR expected_unknown_label_count
                IS DISTINCT FROM expected.expected_unknown_label_count
            OR expected_unknown_label_set_digest
                IS DISTINCT FROM expected.expected_unknown_label_set_digest
        ) AS contract_mismatch_count,
        SHA2(
            TO_JSON(
                ARRAY_SORT(
                    COLLECT_LIST(
                        CASE
                            WHEN record_kind = 'unknown_label'
                                THEN recomputed_content_digest
                        END
                    )
                )
            ),
            256
        ) AS actual_unknown_set_digest
    FROM verified_intent
    CROSS JOIN expected_contract AS expected
),
intent_assertions AS (
    SELECT ASSERT_TRUE(
        expected.scoring_run_id = expected.run_contract_digest
            AND expected.scoring_run_id RLIKE '^[0-9a-f]{64}$'
            AND expected.staged_candidate_delta_version >= 0
            AND expected.staged_candidate_snapshot_digest RLIKE '^[0-9a-f]{64}$'
            AND expected.expected_unknown_label_count >= 0
            AND expected.expected_unknown_label_set_digest RLIKE '^[0-9a-f]{64}$'
            AND summary.header_count = 1
            AND summary.intent_record_count
                = expected.expected_unknown_label_count + 1
            AND summary.candidate_count
                = expected.expected_unknown_label_count
            AND summary.distinct_candidate_count
                = expected.expected_unknown_label_count
            AND summary.content_mismatch_count = 0
            AND summary.contract_mismatch_count = 0
            AND summary.actual_unknown_set_digest
                = expected.expected_unknown_label_set_digest,
        'unknown-label quarantine intent reconciliation failed'
    ) AS asserted
    FROM intent_summary AS summary
    CROSS JOIN expected_contract AS expected
)
SELECT asserted AS quarantine_intent_asserted
FROM intent_assertions;
```

The final insert-only merge reads only the durable intent table. Its filters bind the exact run, staged snapshot, expected count, and expected content-set digest. It never re-evaluates the manifest, expected labels, staged candidates, or gate result.

```sql
MERGE INTO ${catalog}.${schema}.${quarantine_table} AS quarantine_target
USING (
WITH expected_contract AS (
    SELECT
        CAST(:scoring_run_id AS STRING) AS scoring_run_id,
        CAST(:run_contract_digest AS STRING) AS run_contract_digest,
        CAST(:staged_candidate_delta_version AS BIGINT)
            AS staged_candidate_delta_version,
        CAST(:staged_candidate_snapshot_digest AS STRING)
            AS staged_candidate_snapshot_digest,
        CAST(:expected_unknown_label_count AS BIGINT)
            AS expected_unknown_label_count,
        CAST(:expected_unknown_label_set_digest AS STRING)
            AS expected_unknown_label_set_digest
),
exact_intent AS (
    SELECT intent.*
    FROM ${catalog}.${schema}.${quarantine_intent_table} AS intent
    CROSS JOIN expected_contract AS expected
    WHERE intent.scoring_run_id = expected.scoring_run_id
),
verified_intent AS (
    SELECT
        intent.*,
        CASE
            WHEN record_kind = 'unknown_label' THEN SHA2(
                TO_JSON(
                    NAMED_STRUCT(
                        'quarantine_row_id', quarantine_row_id,
                        'business_key', business_key,
                        'scoring_run_id', scoring_run_id,
                        'run_contract_digest', run_contract_digest,
                        'source_table', source_table,
                        'source_delta_version', source_delta_version,
                        'source_window_start', source_window_start,
                        'source_window_end', source_window_end,
                        'observation_timestamp', observation_timestamp,
                        'target_population_contract_digest',
                            target_population_contract_digest,
                        'staged_candidate_table', staged_candidate_table,
                        'staged_candidate_delta_version',
                            staged_candidate_delta_version,
                        'staged_candidate_snapshot_digest',
                            staged_candidate_snapshot_digest,
                        'resolved_model_uri', resolved_model_uri,
                        'model_version', model_version,
                        'model_run_id', model_run_id,
                        'label_map_version', label_map_version,
                        'expected_label_artifact_digest',
                            expected_label_artifact_digest,
                    'prediction', prediction,
                    'quarantine_reason', quarantine_reason
                ),
                MAP(
                    'timeZone', 'UTC',
                    'timestampFormat',
                        'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX'
                )
            ),
                256
            )
        END AS recomputed_content_digest
    FROM exact_intent AS intent
),
source_summary AS (
    SELECT
        COUNT(*) AS intent_record_count,
        COUNT_IF(record_kind = 'header') AS header_count,
        COUNT_IF(record_kind = 'unknown_label') AS candidate_count,
        COUNT(DISTINCT CASE
            WHEN record_kind = 'unknown_label' THEN quarantine_row_id
        END) AS distinct_candidate_count,
        COUNT_IF(
            record_kind = 'unknown_label'
            AND intent_content_digest IS DISTINCT FROM recomputed_content_digest
        ) AS content_mismatch_count,
        COUNT_IF(
            record_kind NOT IN ('header', 'unknown_label')
            OR run_contract_digest IS DISTINCT FROM expected.run_contract_digest
            OR staged_candidate_delta_version
                IS DISTINCT FROM expected.staged_candidate_delta_version
            OR staged_candidate_snapshot_digest
                IS DISTINCT FROM expected.staged_candidate_snapshot_digest
            OR expected_unknown_label_count
                IS DISTINCT FROM expected.expected_unknown_label_count
            OR expected_unknown_label_set_digest
                IS DISTINCT FROM expected.expected_unknown_label_set_digest
        ) AS contract_mismatch_count,
        SHA2(
            TO_JSON(
                ARRAY_SORT(
                    COLLECT_LIST(
                        CASE
                            WHEN record_kind = 'unknown_label'
                                THEN recomputed_content_digest
                        END
                    )
                )
            ),
            256
        ) AS actual_unknown_set_digest
    FROM verified_intent
    CROSS JOIN expected_contract AS expected
),
source_assertions AS (
    SELECT ASSERT_TRUE(
        expected.scoring_run_id = expected.run_contract_digest
            AND expected.scoring_run_id RLIKE '^[0-9a-f]{64}$'
            AND expected.staged_candidate_delta_version >= 0
            AND expected.staged_candidate_snapshot_digest RLIKE '^[0-9a-f]{64}$'
            AND expected.expected_unknown_label_count >= 0
            AND expected.expected_unknown_label_set_digest RLIKE '^[0-9a-f]{64}$'
            AND summary.header_count = 1
            AND summary.intent_record_count
                = expected.expected_unknown_label_count + 1
            AND summary.candidate_count
                = expected.expected_unknown_label_count
            AND summary.distinct_candidate_count
                = expected.expected_unknown_label_count
            AND summary.content_mismatch_count = 0
            AND summary.contract_mismatch_count = 0
            AND summary.actual_unknown_set_digest
                = expected.expected_unknown_label_set_digest,
        'unknown-label quarantine intent changed before final merge'
    ) AS asserted
    FROM source_summary AS summary
    CROSS JOIN expected_contract AS expected
)
SELECT
    intent.quarantine_row_id,
    intent.business_key,
    intent.scoring_run_id,
    intent.run_contract_digest,
    intent.source_table,
    intent.source_delta_version,
    intent.source_window_start,
    intent.source_window_end,
    intent.observation_timestamp,
    intent.target_population_contract_digest,
    intent.staged_candidate_table,
    intent.staged_candidate_delta_version,
    intent.staged_candidate_snapshot_digest,
    intent.resolved_model_uri,
    intent.model_version,
    intent.model_run_id,
    intent.label_map_version,
    intent.expected_label_artifact_digest,
    intent.prediction,
    intent.quarantine_reason,
    current_timestamp() AS quarantined_at
FROM verified_intent AS intent
CROSS JOIN source_assertions AS assertions
WHERE intent.record_kind = 'unknown_label'
    AND assertions.asserted IS NULL
) AS intent_source
    ON quarantine_target.quarantine_row_id = intent_source.quarantine_row_id
WHEN NOT MATCHED THEN INSERT (
    quarantine_row_id,
    business_key,
    scoring_run_id,
    run_contract_digest,
    source_table,
    source_delta_version,
    source_window_start,
    source_window_end,
    observation_timestamp,
    target_population_contract_digest,
    staged_candidate_table,
    staged_candidate_delta_version,
    staged_candidate_snapshot_digest,
    resolved_model_uri,
    model_version,
    model_run_id,
    label_map_version,
    expected_label_artifact_digest,
    prediction,
    quarantine_reason,
    quarantined_at
) VALUES (
    intent_source.quarantine_row_id,
    intent_source.business_key,
    intent_source.scoring_run_id,
    intent_source.run_contract_digest,
    intent_source.source_table,
    intent_source.source_delta_version,
    intent_source.source_window_start,
    intent_source.source_window_end,
    intent_source.observation_timestamp,
    intent_source.target_population_contract_digest,
    intent_source.staged_candidate_table,
    intent_source.staged_candidate_delta_version,
    intent_source.staged_candidate_snapshot_digest,
    intent_source.resolved_model_uri,
    intent_source.model_version,
    intent_source.model_run_id,
    intent_source.label_map_version,
    intent_source.expected_label_artifact_digest,
    intent_source.prediction,
    intent_source.quarantine_reason,
    intent_source.quarantined_at
);
```

After every attempt, compare the complete target run with the durable intent and the pinned expected count and digest. The header and digest checks prevent a missing intent plus empty target from passing as zero/zero. The anti-joins prove exact keys, and the content join detects a matched key with different immutable content.

```sql
WITH expected_contract AS (
    SELECT
        CAST(:scoring_run_id AS STRING) AS scoring_run_id,
        CAST(:run_contract_digest AS STRING) AS run_contract_digest,
        CAST(:staged_candidate_delta_version AS BIGINT)
            AS staged_candidate_delta_version,
        CAST(:staged_candidate_snapshot_digest AS STRING)
            AS staged_candidate_snapshot_digest,
        CAST(:expected_unknown_label_count AS BIGINT)
            AS expected_unknown_label_count,
        CAST(:expected_unknown_label_set_digest AS STRING)
            AS expected_unknown_label_set_digest
),
intent_run AS (
    SELECT intent.*
    FROM ${catalog}.${schema}.${quarantine_intent_table} AS intent
    CROSS JOIN expected_contract AS expected
    WHERE intent.scoring_run_id = expected.scoring_run_id
),
verified_intent_run AS (
    SELECT
        intent.*,
        CASE
            WHEN record_kind = 'unknown_label' THEN SHA2(
                TO_JSON(
                    NAMED_STRUCT(
                        'quarantine_row_id', quarantine_row_id,
                        'business_key', business_key,
                        'scoring_run_id', scoring_run_id,
                        'run_contract_digest', run_contract_digest,
                        'source_table', source_table,
                        'source_delta_version', source_delta_version,
                        'source_window_start', source_window_start,
                        'source_window_end', source_window_end,
                        'observation_timestamp', observation_timestamp,
                        'target_population_contract_digest',
                            target_population_contract_digest,
                        'staged_candidate_table', staged_candidate_table,
                        'staged_candidate_delta_version',
                            staged_candidate_delta_version,
                        'staged_candidate_snapshot_digest',
                            staged_candidate_snapshot_digest,
                        'resolved_model_uri', resolved_model_uri,
                        'model_version', model_version,
                        'model_run_id', model_run_id,
                        'label_map_version', label_map_version,
                        'expected_label_artifact_digest',
                            expected_label_artifact_digest,
                    'prediction', prediction,
                    'quarantine_reason', quarantine_reason
                ),
                MAP(
                    'timeZone', 'UTC',
                    'timestampFormat',
                        'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX'
                )
            ),
                256
            )
        END AS recomputed_content_digest
    FROM intent_run AS intent
),
intent_candidates AS (
    SELECT *
    FROM verified_intent_run
    WHERE record_kind = 'unknown_label'
),
target_run AS (
    SELECT target.*
    FROM ${catalog}.${schema}.${quarantine_table} AS target
    CROSS JOIN expected_contract AS expected
    WHERE target.scoring_run_id = expected.scoring_run_id
),
missing_keys AS (
    SELECT intent.quarantine_row_id
    FROM intent_candidates AS intent
    LEFT ANTI JOIN target_run AS target
        ON target.quarantine_row_id = intent.quarantine_row_id
),
unexpected_keys AS (
    SELECT target.quarantine_row_id
    FROM target_run AS target
    LEFT ANTI JOIN intent_candidates AS intent
        ON intent.quarantine_row_id = target.quarantine_row_id
),
mismatched_rows AS (
    SELECT intent.quarantine_row_id
    FROM intent_candidates AS intent
    INNER JOIN target_run AS target
        ON target.quarantine_row_id = intent.quarantine_row_id
    WHERE NOT (
        target.business_key <=> intent.business_key
        AND target.scoring_run_id <=> intent.scoring_run_id
        AND target.run_contract_digest <=> intent.run_contract_digest
        AND target.source_table <=> intent.source_table
        AND target.source_delta_version <=> intent.source_delta_version
        AND target.source_window_start <=> intent.source_window_start
        AND target.source_window_end <=> intent.source_window_end
        AND target.observation_timestamp <=> intent.observation_timestamp
        AND target.target_population_contract_digest
            <=> intent.target_population_contract_digest
        AND target.staged_candidate_table <=> intent.staged_candidate_table
        AND target.staged_candidate_delta_version
            <=> intent.staged_candidate_delta_version
        AND target.staged_candidate_snapshot_digest
            <=> intent.staged_candidate_snapshot_digest
        AND target.resolved_model_uri <=> intent.resolved_model_uri
        AND target.model_version <=> intent.model_version
        AND target.model_run_id <=> intent.model_run_id
        AND target.label_map_version <=> intent.label_map_version
        AND target.expected_label_artifact_digest
            <=> intent.expected_label_artifact_digest
        AND target.prediction <=> intent.prediction
        AND target.quarantine_reason <=> intent.quarantine_reason
    )
),
intent_aggregate AS (
    SELECT
        COUNT_IF(intent.record_kind = 'header') AS header_count,
        COUNT_IF(intent.record_kind = 'unknown_label') AS intent_candidate_count,
        COUNT_IF(
            intent.record_kind = 'unknown_label'
            AND intent.intent_content_digest
                IS DISTINCT FROM intent.recomputed_content_digest
        ) AS intent_content_mismatch_count,
        COUNT_IF(
            intent.run_contract_digest IS DISTINCT FROM expected.run_contract_digest
            OR intent.staged_candidate_delta_version
                IS DISTINCT FROM expected.staged_candidate_delta_version
            OR intent.staged_candidate_snapshot_digest
                IS DISTINCT FROM expected.staged_candidate_snapshot_digest
            OR intent.expected_unknown_label_count
                IS DISTINCT FROM expected.expected_unknown_label_count
            OR intent.expected_unknown_label_set_digest
                IS DISTINCT FROM expected.expected_unknown_label_set_digest
        ) AS intent_contract_mismatch_count,
        SHA2(
            TO_JSON(
                ARRAY_SORT(
                    COLLECT_LIST(
                        CASE
                            WHEN intent.record_kind = 'unknown_label'
                                THEN intent.recomputed_content_digest
                        END
                    )
                )
            ),
            256
        ) AS actual_unknown_set_digest
    FROM verified_intent_run AS intent
    CROSS JOIN expected_contract AS expected
),
target_aggregate AS (
    SELECT COUNT(*) AS target_count
    FROM target_run
),
reconciliation AS (
    SELECT
        intent.header_count,
        intent.intent_candidate_count,
        target.target_count,
        intent.intent_content_mismatch_count,
        intent.intent_contract_mismatch_count,
        intent.actual_unknown_set_digest,
        (SELECT COUNT(*) FROM missing_keys) AS missing_key_count,
        (SELECT COUNT(*) FROM unexpected_keys) AS unexpected_key_count,
        (SELECT COUNT(*) FROM mismatched_rows) AS mismatched_row_count
    FROM intent_aggregate AS intent
    CROSS JOIN target_aggregate AS target
),
final_assertion AS (
    SELECT ASSERT_TRUE(
        expected.scoring_run_id = expected.run_contract_digest
            AND expected.expected_unknown_label_count >= 0
            AND expected.expected_unknown_label_set_digest RLIKE '^[0-9a-f]{64}$'
            AND reconciliation.header_count = 1
            AND reconciliation.intent_candidate_count
                = expected.expected_unknown_label_count
            AND reconciliation.target_count
                = expected.expected_unknown_label_count
            AND reconciliation.intent_content_mismatch_count = 0
            AND reconciliation.intent_contract_mismatch_count = 0
            AND reconciliation.actual_unknown_set_digest
                = expected.expected_unknown_label_set_digest
            AND reconciliation.missing_key_count = 0
            AND reconciliation.unexpected_key_count = 0
            AND reconciliation.mismatched_row_count = 0,
        'unknown-label quarantine persistence reconciliation failed'
    ) AS asserted
    FROM reconciliation
    CROSS JOIN expected_contract AS expected
)
SELECT asserted AS quarantine_persistence_asserted
FROM final_assertion;
```

## Decision Rules

- Gate only the latest manifest-selected closed candidate window. Never derive the candidate bucket from the wall clock or mix a partial current hour into candidate counts.
- Preserve duplicate or tied latest manifest rows through ranking; exactly-one cardinality rejects them before quarantine or publication.
- Join the exact selected manifest and successful staged-reconciliation record before classifying outcomes. Require exact expected, staged, scoreable, unscorable, unknown-label, and unexpected-NULL counts.
- Quarantine uses the identical fail-closed prerequisite assertion as publication. An invalid parameter, manifest, reconciliation, or expected-label contract fails before mutation and persists zero rows.
- Quarantine materializes one insert-only header plus the exact unknown-label rows before later writes. Every retry recomputes immutable content, count, and set digest against the pinned successful-gate values without updating existing intent.
- Reconcile staged candidate keys and row counts against the pinned source before evaluating the gate.
- Gate and prediction merge read the identical staged-candidate Delta table/version/digest. The final quarantine merge reads only the durable intent for that identity; later upstream changes cannot erase or redefine it.
- Missing expected labels remain zero-share candidate rows through the expected-label left join.
- An empty expected-label artifact produces one `is_contract_gate_row = TRUE` result with `BLOCK_PUBLISH_EXPECTED_LABEL_CONTRACT`; it never produces an empty result. Duplicate or digest-mismatched artifacts also block.
- Unknown candidate labels are quarantined by anti-join and block the commit.
- A legitimate unscorable row has NULL prediction and score plus a normalized reason allowed by the exact `unscorable_policy_version`. It remains in the bucket denominator. Every unexplained or inconsistent NULL blocks publication.
- Apply sample sufficiency to the complete candidate bucket and each eligible baseline bucket.
- Use exactly one-hour buckets and hour-aligned closed boundaries. Reject another configured width rather than silently continuing with the hardcoded `date_trunc('hour', ...)` logic.
- Gate both positive and negative shifts with `ABS(z)`.
- Treat `std_share IS NULL` as `insufficient_baseline`. This includes a one-bucket baseline. For finite zero variance, a changed share blocks and an unchanged sufficiently common share is valid.
- Classify rarity from baseline expected rows, not current observed rows. A normally common label that vanishes must remain gateable.
- Treat `insufficient_baseline`, `insufficient_current_sample`, and `rare_label` as non-publish states requiring review.
- Persist the candidate gate inputs and output with the audit attempt. A retry uses the same frozen baseline window and gate parameters.

Offline fixtures must distinguish staged candidates from unrelated historical rows and cover duplicate/tied latest manifest rows blocking both paths, quarantine under invalid parameters, invalid non-NULL business keys, manifest source/version/window mismatches, missing or duplicate reconciliation, an empty expected-label artifact, expected-label digest mismatch, explicit empty-artifact gate-row emission, forged mutually consistent run IDs/digests, gated staged-snapshot mutation isolation, an upstream relation changing after intent materialization, absent intent with an empty target, exact outcome counts, legitimate unscorable rows, unexplained NULLs, unknown labels, an incomplete latest bucket, an absent expected label, negative drift, a one-bucket baseline, zero-variance change, unchanged zero variance, rare labels, undersized buckets, non-finite parameters, and one-hour boundary alignment.
