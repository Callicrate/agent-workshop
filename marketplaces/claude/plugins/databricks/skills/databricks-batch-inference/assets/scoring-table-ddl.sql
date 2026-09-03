-- =============================================================================
-- Purpose: Starter DDL for immutable batch-inference prediction history.
-- Replace business_key type and catalog/schema/table names before deployment.
-- score is the selected downstream score; score_kind names its source.
-- Preserve raw_score and calibrated_score as separate evidence.
-- =============================================================================

CREATE TABLE IF NOT EXISTS catalog_name.schema_name.table_name_scores (
    business_key STRING NOT NULL
    COMMENT 'Stable source key; unique within one scoring run',
    scoring_run_id STRING NOT NULL
    COMMENT 'Deterministic digest of the immutable scoring contract',
    run_contract_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical UTF-8 run-contract JSON',
    run_contract_json STRING NOT NULL
    COMMENT 'Canonical immutable run-contract JSON for retry comparison',
    run_contract_artifact_uri STRING
    COMMENT 'Optional immutable reference to the same canonical JSON',
    target_population_contract_digest STRING NOT NULL
    COMMENT 'Digest of target-population predicate and key contract',
    staged_candidate_table STRING NOT NULL
    COMMENT 'Staging Delta table used by both gate and merge',
    staged_candidate_delta_version BIGINT NOT NULL
    COMMENT 'Pinned staging Delta version used by gate and merge',
    staged_candidate_snapshot_digest STRING NOT NULL
    COMMENT 'Digest recorded for the pinned staged snapshot',
    source_table STRING NOT NULL
    COMMENT 'Fully qualified source Delta table',
    source_delta_version BIGINT NOT NULL
    COMMENT 'Pinned Delta versionAsOf for the source read',
    source_window_start TIMESTAMP NOT NULL
    COMMENT 'Inclusive source event window start',
    source_window_end TIMESTAMP NOT NULL
    COMMENT 'Exclusive source event window end',
    observation_timestamp TIMESTAMP NOT NULL
    COMMENT 'Per-row event time for point-in-time feature lookup',
    feature_lookup_strategy STRING NOT NULL
    COMMENT 'packaged_point_in_time or explicit_pinned_asof',
    feature_snapshot_pins ARRAY<STRUCT<dependency_name: STRING, snapshot_kind: STRING, snapshot_value: STRING>> NOT NULL -- noqa: LT05
    COMMENT 'Ordered physical feature dependency snapshot pins',
    feature_snapshot_pins_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of ordered feature dependency pins',
    requested_model_alias STRING
    COMMENT 'Mutable alias requested before one-time resolution',
    resolved_model_uri STRING NOT NULL
    COMMENT 'Immutable models:/name/version URI actually loaded',
    model_version STRING NOT NULL
    COMMENT 'Resolved Unity Catalog registered-model version',
    model_run_id STRING NOT NULL
    COMMENT 'Verified MLflow run ID for the loaded model version',
    prediction STRING
    COMMENT 'Predicted label after the versioned label map',
    score DOUBLE
    COMMENT 'Selected downstream score named by score_kind',
    score_kind STRING
    COMMENT 'raw or calibrated; identifies the source of score',
    raw_score DOUBLE
    COMMENT 'Direct model output before calibration',
    calibrated_score DOUBLE
    COMMENT 'Post-calibration score when calibration is configured',
    threshold_version STRING NOT NULL
    COMMENT 'Versioned threshold artifact or not_applicable',
    label_map_version STRING NOT NULL
    COMMENT 'Versioned label-map artifact or not_applicable',
    expected_label_artifact_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical ordered expected labels',
    unscorable_policy_version STRING NOT NULL
    COMMENT 'Versioned policy for legitimate unscorable reasons',
    unscorable_reason STRING
    COMMENT 'Reason no valid prediction or score was produced',
    scored_at TIMESTAMP NOT NULL
    COMMENT 'Timestamp when this immutable row was produced',
    CONSTRAINT canonical_business_key CHECK (
        business_key = TRIM(business_key)
        AND TRIM(CAST(business_key AS STRING)) <> ''
        AND LENGTH(business_key) BETWEEN 1 AND 512
        AND business_key RLIKE '^-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}$'
    ),
    CONSTRAINT run_id_matches_contract_digest CHECK (
        scoring_run_id = run_contract_digest
    ),
    CONSTRAINT canonical_run_identity CHECK (
        scoring_run_id RLIKE '^[0-9a-f]{64}$'
        AND run_contract_digest RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT valid_score_kind CHECK (
        score_kind IS NULL OR score_kind IN ('raw', 'calibrated')
    ),
    CONSTRAINT score_source_matches CHECK (
        (score IS NULL AND score_kind IS NULL)
        OR (
            score_kind = 'raw'
            AND score IS NOT NULL
            AND raw_score IS NOT NULL
            AND score = raw_score
        )
        OR (
            score_kind = 'calibrated'
            AND score IS NOT NULL
            AND calibrated_score IS NOT NULL
            AND score = calibrated_score
        )
    )
)
USING DELTA
COMMENT 'Immutable predictions; insert-only key: business_key + scoring_run_id';
