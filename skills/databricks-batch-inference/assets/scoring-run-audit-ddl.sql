-- =============================================================================
-- Purpose: Delta audit state for repeatable scheduled batch-inference attempts.
-- Replace catalog, schema, and table names before deployment.
-- =============================================================================

CREATE TABLE IF NOT EXISTS catalog_name.schema_name.scoring_run_audit (
    scoring_run_id STRING NOT NULL
    COMMENT 'Deterministic digest of the immutable scoring contract',
    attempt_id STRING NOT NULL
    COMMENT 'Digest of scoring_run_id plus explicit attempt ordinal',
    attempt_ordinal BIGINT NOT NULL
    COMMENT 'Non-negative deterministic physical-attempt ordinal',
    run_contract_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical UTF-8 run-contract JSON',
    run_contract_json STRING NOT NULL
    COMMENT 'Canonical immutable run-contract JSON compared on retry',
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
    target_table STRING NOT NULL
    COMMENT 'Fully qualified immutable prediction table',
    source_delta_version BIGINT NOT NULL
    COMMENT 'Pinned Delta source versionAsOf',
    source_filter STRING
    COMMENT 'Reviewed predicate defining scored rows',
    source_window_start TIMESTAMP NOT NULL
    COMMENT 'Inclusive source window start',
    source_window_end TIMESTAMP NOT NULL
    COMMENT 'Exclusive source window end',
    observation_timestamp_definition STRING NOT NULL
    COMMENT 'Column or fixed timestamp used for point-in-time features',
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
    COMMENT 'Resolved registered-model version',
    model_run_id STRING NOT NULL
    COMMENT 'Verified MLflow run ID for the loaded model',
    signature_digest STRING NOT NULL
    COMMENT 'Digest of ordered input and output signature contract',
    threshold_version STRING NOT NULL
    COMMENT 'Versioned threshold artifact or not_applicable',
    label_map_version STRING NOT NULL
    COMMENT 'Versioned label-map artifact or not_applicable',
    expected_label_artifact_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical ordered expected labels',
    unscorable_policy_version STRING NOT NULL
    COMMENT 'Versioned policy for legitimate unscorable reasons',
    source_row_count BIGINT
    COMMENT 'Rows in the pinned eligible population',
    expected_row_count BIGINT
    COMMENT 'Expected candidate rows from pinned source reconciliation',
    staged_row_count BIGINT
    COMMENT 'Actual contract-bound staged candidate rows',
    scoreable_row_count BIGINT
    COMMENT 'Rows with prediction and score and no reason',
    scored_row_count BIGINT
    COMMENT 'Rows with a valid prediction',
    unscorable_row_count BIGINT
    COMMENT 'Rows retained with an unscorable reason',
    unknown_label_count BIGINT
    COMMENT 'Rows whose prediction is outside the label map',
    duplicate_key_count BIGINT
    COMMENT 'Duplicate business keys; must be zero before scoring',
    unexpected_null_prediction_count BIGINT
    COMMENT 'NULL outputs without an unscorable reason',
    output_schema_hash STRING
    COMMENT 'Digest of the required prediction-table schema',
    output_columns ARRAY<STRING>
    COMMENT 'Ordered prediction output columns',
    prediction_target_commit_version BIGINT
    COMMENT 'Delta target version containing reconciled inserts',
    publication_decision STRING
    COMMENT 'Derived ALLOW_PUBLISH or BLOCK_PUBLISH decision',
    publication_reason STRING
    COMMENT 'Fixed deterministic reason from run-level gate derivation',
    publication_gate_row_count BIGINT
    COMMENT 'Number of label-gate rows summarized by the decision',
    status STRING NOT NULL
    COMMENT 'running, succeeded, or failed',
    error_message STRING
    COMMENT 'Bounded failure summary without source payloads',
    started_at TIMESTAMP NOT NULL
    COMMENT 'Attempt start timestamp',
    completed_at TIMESTAMP
    COMMENT 'Attempt terminal timestamp',
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp(),
    CONSTRAINT run_id_matches_contract_digest CHECK (
        scoring_run_id = run_contract_digest
    ),
    CONSTRAINT canonical_run_identity CHECK (
        scoring_run_id RLIKE '^[0-9a-f]{64}$'
        AND run_contract_digest RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT valid_publication_decision CHECK (
        publication_decision IS NULL
        OR publication_decision IN ('ALLOW_PUBLISH', 'BLOCK_PUBLISH')
    ),
    CONSTRAINT valid_attempt_ordinal CHECK (attempt_ordinal >= 0),
    CONSTRAINT valid_status CHECK (status IN ('running', 'succeeded', 'failed'))
)
USING DELTA
COMMENT 'Run-level audit and recovery state for insert-only prediction commits';
