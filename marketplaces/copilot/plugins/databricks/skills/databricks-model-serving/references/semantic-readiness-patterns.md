# Semantic Readiness Patterns

Use this after endpoint state checks pass. `READY` means the deployment is live; it does not prove the endpoint returns useful model behavior.

## Done Criteria

Endpoint validation is complete only when all of these pass:

- endpoint state is `READY`
- endpoint `config_update` is `NOT_UPDATING` and `pending_config` is absent
- one post-wait snapshot exactly matches the contract's config version, creator, served entities, model versions, scale policy, routes, traffic, target URL, and telemetry
- actual `route_optimized` exactly matches the contract; optimized endpoints expose the exact normalized dedicated URL, while non-optimized endpoints do not expose an unexpected dedicated URL
- the expected served version has positive traffic, unless the contract explicitly identifies it as a zero-traffic fallback
- an optimized endpoint has a separately authorized service-principal probe over the dedicated data plane; a Workspace Client query alone leaves transport unverified and the rollout not ready
- a direct live query returns required response fields
- score fields have documented range, direction, units, thresholds, and action policy
- representative samples return non-default decisions where expected
- no known-good sample returns forbidden fallback labels such as `NO_OPINION`, `MODEL_SCORED`, or unapproved `UNKNOWN`
- the user-named notebook, client, or app path passes when one is part of the task

## Representative Samples

Use sample rows that exercise expected behavior, not only schema shape:

- benign examples expected to score as safe or low risk
- risky examples expected to trigger high risk or action labels
- unknown or missing-data examples where fallback labels are valid
- boundary examples around decision thresholds
- domain-specific examples supplied by the user

Run direct endpoint queries before notebook or app validation. The normal ladder is endpoint state, direct SDK query, output contract assertions, then notebook or client behavior.

## Output Contract

Every endpoint that exposes scores or labels must document:

- required and nullable response fields
- score field names, ranges, direction, and units
- allowed decision labels and human-readable meanings
- fallback labels and when they are valid
- thresholds such as `DO_NOT_CONVICT` or action cutoffs
- minimum non-fallback rate for representative samples

Publish this contract through endpoint tags, endpoint description, model card metadata, sample responses, or a metadata response if the wrapper controls output.

## Endpoint Doctor Safety And Deadline Contract

Use `scripts/check_endpoint.py` only with representative fixture files that fit its local bounds. It reads whole JSON fixtures as strict UTF-8 under a 1 MB cap and streams JSONL with total, per-record, record-count, duplicate-key, non-finite-number, depth, node, and string budgets. Fixture values and raw model responses remain transient: reports contain only redacted shapes, counts, limits, and assertion metadata.

Pass a schema-valid `--contract <contract.json>` on every doctor run. The contract owns response fields, label rules, score ranges, predicates, telemetry, routes, scale policy, and recovery state. CLI flags cannot override that closed semantic contract. Before a fixture can be transmitted, provide the exact contract profile or an exact `--workspace-host https://workspace.example` manifest. `--profile DEFAULT` is treated as a different profile and is rejected when it does not match the target manifest. The helper compares the SDK host to the contract before retrieving the endpoint and blocks the query if the post-wait snapshot differs.

Fixture readiness is not a shape-only check. The contract requires at least one non-null response field and at least one closed label, range, or predicate assertion. A `null`, empty string, empty object, empty array, missing record collection, or assertion-free contract fails. Reports retain fixed failure kinds, counts, and opaque field-path hashes; raw response field names and values never enter JSON or human output.

The current request contract is version 1 and row-oriented. It permits only explicitly listed `dataframe_records` and `dataframe_split` shapes. Record keys must exactly equal signature input names; split columns must equal signature order; row widths, logical types, and nullability must match. Reject extra or missing fields and reject opaque `instances`, `inputs`, `messages`, `prompt`, or mixed request shapes before client construction. Version a future chat or embedding contract separately rather than applying row semantics to it.

Logical types are semantic, not string labels. Validate real canonical `YYYY-MM-DD` dates; bounded RFC3339 timestamps with uppercase `T`, seconds, explicit `Z` or `±HH:MM`, and no, 3, or 6 fractional digits; and nonempty canonical padded standard Base64 whose decoded bytes stay within the configured cap. Apply the same parser before request transmission and to transient raw response values.

For each accepted DataFrame request, require one output record per input row. Project response records transiently without redaction, under independent row, node, depth, fanout, and string caps. Run schema, type, nullability, label, range, predicate, and cardinality checks against those raw values. This ordering prevents response fields such as `token_count` or `authorization_score` from being changed by credential-name redaction before numeric checks.

Capture the exact output count before any report cap. Two input rows and one output record fail cardinality even when the remaining record satisfies label and score assertions. At the row cap, 100 inputs require exactly 100 outputs; 99 fails mismatch and 101 fails both mismatch and overflow. A record or node overflow fails semantic readiness and row cardinality even when a truncated prefix would otherwise match. Only after those decisions, build a separate value-free report from fixed kinds, counts, overflow state, and opaque field hashes. Do not infer the row policy for future non-row request contracts.

Endpoint events are best-effort evidence. The default result is a warning and a partial report if events are unavailable or fail. Event reports are value-free: they retain availability, bounded counts, truncation, and fixed structural kind counts, never event text, URLs, timestamps, states, field names, or arbitrary values. Use `--require-events` when event collection is required; then an unavailable event stream fails the run. Human output says `CHECKS PASSED WITH WARNINGS`, never `ALL CHECKS PASSED`, when warnings exist.

The helper passes explicit `http_timeout_seconds` and `retry_timeout_seconds` to `WorkspaceClient`. Its readiness loop sleeps no longer than the remaining deadline and never begins another poll after expiry. An already-started SDK request can run past that deadline by at most its configured request timeout, so choose the timeout and wait budget deliberately. Databricks documents a 597-second server-side serving request limit and a 16 MB custom-model payload limit (4 MB for agent endpoints); the doctor's smaller local fixture cap is a diagnostic safety limit, not an endpoint capability claim. See [Model Serving limits](https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-limits) and [model serving timeouts](https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-timeouts).

## Bad And Good Patterns

Bad pattern:

```text
Endpoint is READY. Mark serving complete.
```

Good pattern:

```text
Endpoint is READY on the expected served entity.
Direct smoke query returned required fields, score ranges, and non-fallback decisions for representative benign and risky examples.
Notebook validation returned the same labels and score semantics.
```

## All-Fallback Failures

Fail semantic readiness when every sample returns one of these unless the fixture explicitly expects it:

- `NO_OPINION`
- `MODEL_SCORED`
- unapproved `UNKNOWN`
- null or empty prediction
- identical default responses across unrelated samples

When this happens, inspect model version, feature lookup keys, null handling, threshold constants, label mapping, and response serialization before changing traffic.
