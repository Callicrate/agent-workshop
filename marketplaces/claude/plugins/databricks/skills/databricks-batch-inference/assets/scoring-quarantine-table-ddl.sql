-- =============================================================================
-- Purpose: Durable intent and final unknown-label quarantine tables.
-- Bind both three-part targets through the deployment environment.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.${quarantine_intent_table} (
    intent_record_id STRING NOT NULL
    COMMENT 'Namespace-separated SHA-256 for one header or candidate row',
    record_kind STRING NOT NULL
    COMMENT 'header or unknown_label',
    quarantine_row_id STRING
    COMMENT 'Stable target-row identity; NULL only for the header',
    intent_content_digest STRING
    COMMENT 'Immutable row-content SHA-256; NULL only for the header',
    business_key STRING
    COMMENT 'Canonical source key; NULL only for the header',
    scoring_run_id STRING NOT NULL
    COMMENT 'Deterministic digest of the immutable scoring contract',
    run_contract_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical UTF-8 run-contract JSON',
    source_table STRING NOT NULL
    COMMENT 'Fully qualified source Delta table',
    source_delta_version BIGINT NOT NULL
    COMMENT 'Pinned source Delta version',
    source_window_start TIMESTAMP NOT NULL
    COMMENT 'Inclusive source event window start',
    source_window_end TIMESTAMP NOT NULL
    COMMENT 'Exclusive source event window end',
    observation_timestamp TIMESTAMP
    COMMENT 'Per-row event time; NULL only for the header',
    target_population_contract_digest STRING NOT NULL
    COMMENT 'Digest of target-population predicate and key contract',
    staged_candidate_table STRING NOT NULL
    COMMENT 'Fully qualified staged candidate Delta table',
    staged_candidate_delta_version BIGINT NOT NULL
    COMMENT 'Pinned staged candidate Delta version',
    staged_candidate_snapshot_digest STRING NOT NULL
    COMMENT 'Digest recorded for the pinned staged snapshot',
    resolved_model_uri STRING NOT NULL
    COMMENT 'Immutable models:/name/version URI used for scoring',
    model_version STRING NOT NULL
    COMMENT 'Resolved Unity Catalog registered-model version',
    model_run_id STRING NOT NULL
    COMMENT 'Verified MLflow run ID for the loaded model',
    label_map_version STRING NOT NULL
    COMMENT 'Versioned label-map artifact',
    expected_label_artifact_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical ordered expected labels',
    prediction STRING
    COMMENT 'Candidate label absent from the expected-label artifact',
    quarantine_reason STRING
    COMMENT 'unknown_label for candidate rows; NULL for the header',
    expected_unknown_label_count BIGINT NOT NULL
    COMMENT 'Pinned successful-gate count for this intent snapshot',
    expected_unknown_label_set_digest STRING NOT NULL
    COMMENT 'Pinned digest of ordered immutable candidate-content digests',
    intent_created_at TIMESTAMP NOT NULL
    COMMENT 'Time of the first durable insert for this intent record',
    CONSTRAINT canonical_intent_record_id CHECK (
        intent_record_id RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT canonical_intent_run_identity CHECK (
        scoring_run_id = run_contract_digest
        AND scoring_run_id RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT valid_intent_versions CHECK (
        source_delta_version >= 0
        AND staged_candidate_delta_version >= 0
    ),
    CONSTRAINT valid_intent_window CHECK (
        source_window_start < source_window_end
    ),
    CONSTRAINT canonical_expected_unknown_set CHECK (
        expected_unknown_label_count >= 0
        AND expected_unknown_label_set_digest RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT valid_intent_record_shape CHECK (
        (
            record_kind = 'header'
            AND quarantine_row_id IS NULL
            AND intent_content_digest IS NULL
            AND business_key IS NULL
            AND observation_timestamp IS NULL
            AND prediction IS NULL
            AND quarantine_reason IS NULL
        )
        OR (
            record_kind = 'unknown_label'
            AND quarantine_row_id RLIKE '^[0-9a-f]{64}$'
            AND intent_content_digest RLIKE '^[0-9a-f]{64}$'
            AND business_key IS NOT NULL
            AND business_key = TRIM(business_key)
            AND LENGTH(business_key) BETWEEN 1 AND 512
            AND business_key RLIKE '^-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}$'
            AND observation_timestamp IS NOT NULL
            AND prediction IS NOT NULL
            AND quarantine_reason = 'unknown_label'
        )
    )
)
USING DELTA
COMMENT 'Insert-only gate intent: one header plus exact unknown-label rows';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.${quarantine_table} (
    quarantine_row_id STRING NOT NULL
    COMMENT 'SHA-256 of candidate namespace, scoring run, and business key',
    business_key STRING NOT NULL
    COMMENT 'Stable source key; unique within one scoring run',
    scoring_run_id STRING NOT NULL
    COMMENT 'Deterministic digest of the immutable scoring contract',
    run_contract_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical UTF-8 run-contract JSON',
    source_table STRING NOT NULL
    COMMENT 'Fully qualified source Delta table',
    source_delta_version BIGINT NOT NULL
    COMMENT 'Pinned source Delta version',
    source_window_start TIMESTAMP NOT NULL
    COMMENT 'Inclusive source event window start',
    source_window_end TIMESTAMP NOT NULL
    COMMENT 'Exclusive source event window end',
    observation_timestamp TIMESTAMP NOT NULL
    COMMENT 'Per-row event time used for scoring',
    target_population_contract_digest STRING NOT NULL
    COMMENT 'Digest of target-population predicate and key contract',
    staged_candidate_table STRING NOT NULL
    COMMENT 'Fully qualified staged candidate Delta table',
    staged_candidate_delta_version BIGINT NOT NULL
    COMMENT 'Pinned staged candidate Delta version',
    staged_candidate_snapshot_digest STRING NOT NULL
    COMMENT 'Digest recorded for the pinned staged snapshot',
    resolved_model_uri STRING NOT NULL
    COMMENT 'Immutable models:/name/version URI used for scoring',
    model_version STRING NOT NULL
    COMMENT 'Resolved Unity Catalog registered-model version',
    model_run_id STRING NOT NULL
    COMMENT 'Verified MLflow run ID for the loaded model',
    label_map_version STRING NOT NULL
    COMMENT 'Versioned label-map artifact',
    expected_label_artifact_digest STRING NOT NULL
    COMMENT 'SHA-256 digest of canonical ordered expected labels',
    prediction STRING NOT NULL
    COMMENT 'Candidate label absent from the expected-label artifact',
    quarantine_reason STRING NOT NULL
    COMMENT 'Fixed reason; only unknown_label is admitted',
    quarantined_at TIMESTAMP NOT NULL
    COMMENT 'Time of the first durable insert for this row identity',
    CONSTRAINT canonical_quarantine_row_id CHECK (
        quarantine_row_id RLIKE '^[0-9a-f]{64}$'
    ),
    CONSTRAINT canonical_business_key CHECK (
        business_key IS NOT NULL
        AND business_key = TRIM(business_key)
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
    CONSTRAINT valid_source_version CHECK (source_delta_version >= 0),
    CONSTRAINT valid_staged_version CHECK (staged_candidate_delta_version >= 0),
    CONSTRAINT valid_source_window CHECK (
        source_window_start < source_window_end
    ),
    CONSTRAINT unknown_label_only CHECK (quarantine_reason = 'unknown_label')
)
USING DELTA
COMMENT 'Insert-only unknown-label quarantine keyed by quarantine_row_id';
