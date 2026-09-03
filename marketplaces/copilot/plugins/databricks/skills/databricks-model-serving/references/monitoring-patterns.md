# Model Monitoring Patterns

Use this reference only when endpoint telemetry, inference logging, drift detection, or retraining policy is in scope.

## Current Telemetry Contract

Telemetry is a closed tagged union. Disabled telemetry is exactly `{"required": false, "mode": "disabled"}` and carries no table, sampling, feature, profile, or destination expectations. Any telemetry expectation selects the enabled tag and therefore requires `required=true` plus the complete current configuration below. Do not retain dormant destinations under a disabled tag.

Use the current `telemetry_config` surface for custom model and agent serving endpoints.
Declare all three Unity Catalog destinations in `table_names`: `logs_table`, `metrics_table`, and `traces_table`.
Declare `inference_table_config.sampling_fraction` from 0 through 1 and the exact `enabled_telemetry_features` expected after rollout.
Updating telemetry triggers a new endpoint deployment, so the rollout doctor must wait and then verify the settled config snapshot.

Databricks documents the API shape and destination requirements in [Persist custom model serving data to Unity Catalog](https://docs.databricks.com/aws/en/machine-learning/model-serving/custom-model-serving-uc-logs) and the [Serving Endpoint API](https://docs.databricks.com/api/model-serving/v1/serving-endpoint).

The generated OpenTelemetry tables use different schemas:

- Logs expose `timestamp`, `severity_text`, `body`, `trace_id`, `span_id`, and `attributes`.
- Spans and metrics use their published OpenTelemetry table schemas. Inspect them with `DESCRIBE TABLE`; do not query them as if they were payload rows.
- Delivery is at least once. Deduplicate with a stable event identity before deriving durable aggregates.

Unity AI Gateway inference tables are a separate payload-logging surface.
Their published columns include `request_id`, `invocation_id`, `request_tags`, `event_time`, `status_code`, `sampling_fraction`, `latency_ms`, `time_to_first_byte_ms`, `request`, `response`, destination fields, logging error codes, requester, and schema version.
See [Unity AI Gateway inference table schema](https://docs.databricks.com/aws/en/ai-gateway/inference-tables).

## Legacy Auto-Capture Boundary

`auto_capture_config` belongs only in migration and retirement work.
Databricks retired the legacy inference-table documentation and states that the legacy experience is unsupported after April 30, 2026.
Do not create new rollout contracts with `auto_capture_config`, and do not report legacy auto-capture as current telemetry readiness.
For an inherited endpoint, inventory its old payload table, preserve required retention and access controls, migrate consumers to current telemetry or Unity AI Gateway inference tables, verify row continuity, then remove the legacy dependency under an approved recovery plan.
See the [retired legacy inference-table API page](https://docs.databricks.com/aws/en/archive/machine-learning/enable-model-serving-inference-tables).

## Published-Schema Queries

Run operational aggregates directly against the current Unity AI Gateway inference table only when that table is the configured source:

```sql
SELECT
  date_trunc('hour', event_time) AS hour,
  destination_name,
  COUNT(*) AS request_count,
  AVG(CASE WHEN status_code >= 500 THEN 1.0 ELSE 0.0 END) AS error_rate,
  percentile_approx(latency_ms, 0.95) AS p95_latency_ms
FROM IDENTIFIER(:inference_table)
WHERE event_time >= current_timestamp() - INTERVAL 24 HOURS
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
```

Do not invent columns such as `model_version`, `prediction`, or `timestamp` on this table.
For OpenTelemetry logs, query only documented columns:

```sql
SELECT timestamp, severity_text, trace_id, span_id, attributes
FROM IDENTIFIER(:logs_table)
WHERE severity_text = 'ERROR'
  AND timestamp > current_timestamp() - INTERVAL 1 HOUR
ORDER BY timestamp DESC;
```

## Normalized Monitoring View

Prediction drift requires a project-owned normalized view because request and response payloads are JSON strings with model-specific structure.
Define and test the view before using generic drift queries.
The minimum contract is:

- `request_id STRING NOT NULL`
- `invocation_id STRING`
- `event_time TIMESTAMP NOT NULL`
- `destination_name STRING`
- `status_code INT`
- `latency_ms BIGINT`
- `model_version STRING`, populated from an explicitly versioned routing or response field, never guessed from `destination_name`
- one typed column per monitored feature, parsed from `request` with the model signature and documented null handling
- `prediction_label STRING` and/or `prediction_score DOUBLE`, parsed from `response` with the rollout output contract
- `source_schema_version STRING`

Keep the exact `from_json` schemas, JSON paths, parsing-error policy, and deduplication key in the view definition.
Fail the pipeline when the payload cannot be reconciled to the rollout signature or output contract.
Do not silently convert malformed payloads to nulls and then treat them as valid monitoring rows.

## Drift And Alert Workflow

1. Verify telemetry is enabled in the settled endpoint snapshot and that rows arrive within the documented latency.
2. Validate the inference-table or normalized-view schema before every query release.
3. Pick an accepted training or baseline window and record model version, feature list, and source schema version.
4. Measure only model-critical inputs and declared outputs. Account for `sampling_fraction` when estimating volume.
5. Alert on sustained drift and pair it with endpoint errors, latency, and labeled performance when labels exist.
6. Prefer route shift or restore-config recovery when degradation starts immediately after rollout. Retrain only when evidence points to data or concept drift.

Route latency and error alerts to serving owners.
Route drift and performance alerts to model owners.
Every alert includes endpoint, config version, model version if proven, metric, threshold, window, and a stable query or dashboard reference.
