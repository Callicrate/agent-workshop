---
name: databricks-model-serving
description: "Use when deploying Databricks endpoints, validating outputs, choosing online vs batch inference, or wiring LLM routers; guides rollout. Do not trigger for training, bundle config, or API scripting."
metadata:
  short-description: Deploy Databricks serving endpoints.
---

# Databricks Model Serving

## When to Use

- Deploying or updating Databricks serving endpoints
- Choosing between batch inference, real-time endpoints, or feature-serving-backed inference
- Validating endpoint state after rollout or alias/version changes
- Validating semantic output readiness after an endpoint reports `READY`
- Building LiteLLM, LibreChat, or other LLM routers over Databricks endpoints
- Adding inference logging, monitoring, drift checks, or rollout safety rules

## When NOT to Use

- Training or tuning models
- Managing bundle deployment configuration that belongs in `databricks.yml`
- Imperatively changing a DAB-managed endpoint when the bundle should remain the source of truth
- General Databricks API scripting that is not about serving behavior

### Related Skills

- **databricks-spark-etl** - Delta table reads/writes, Spark DataFrame transforms, schema evolution, SCD2 patterns
- **databricks-ml-training** - Training classifiers, MLflow tracking, feature engineering, hyperparameter tuning
- **databricks-asset-bundles** - Creating/editing `databricks.yml`, job resources, dev/prod targets, bundle deployment
- **databricks-api-calls** - Calling Databricks REST APIs from local terminal, querying SQL warehouses

## Workflow

1. Classify the invocation before selecting a validation contract. Record the task and protocol, source kind, and topology: row `predict`/DataFrame request; chat, embeddings, audio, completions, Open Responses, or provider-native request; custom model, custom LLM, Foundation Model API, external model, or provider-native source; and direct endpoint, model service, or router. A Unity Catalog registration or a custom-looking endpoint name does not determine this branch.
2. For chat, embeddings, audio, completions, Open Responses, provider-native, custom LLM, or router work, load [references/llm-router-serving-patterns.md](references/llm-router-serving-patterns.md) immediately. Do not apply the closed row/DataFrame rollout v2 schema, its request-contract v1 protocol, or `check_endpoint.py` fixture doctor to that branch.
3. For a row `predict`/DataFrame request only, define the closed rollout contract v2 with [assets/serving-config-template.py](assets/serving-config-template.py) and [assets/endpoint-config-schema.json](assets/endpoint-config-schema.json), then run `python scripts/validate_endpoint_config.py --config <contract.json>` from this skill root. Do not deploy from an invalid, partial, or rollout v1 contract.
4. Pick the ownership mode next: DAB-managed endpoint, SDK/API-managed endpoint, or external router backed by Databricks endpoints. If the endpoint is DAB-managed, treat bundle config as source of truth; use imperative SDK/API changes only for inspection, emergency remediation, or explicitly non-bundle-managed endpoints.
5. For a row `predict`/DataFrame request only, use [references/core-serving-patterns.md](references/core-serving-patterns.md) for the serving-mode decision, rollout pattern, Unity Catalog loading approach, feature-serving rules, and upstream model handoff fields.
6. Keep the non-negotiable guardrails intact: do not embed large data assets in the model artifact, do not mix DAB declarative and SDK imperative management for the same endpoint, and do not set `DATABRICKS_HOST` inside serving containers.
7. Before destructive operations, production traffic shifts, alias moves, endpoint resets/deletes, served-entity removal, or paid long-running rebuilds, load [references/prod-target-safety.md](references/prod-target-safety.md), print a target manifest, and require explicit approval unless the current task contract already grants that live operation. This gate applies before any mutating LLM action too.
8. For a row `predict`/DataFrame request only, run [scripts/check_endpoint.py](scripts/check_endpoint.py) with `--contract <contract.json>` after deployment or config changes. Before client construction, it rejects rollout v1 and binds a fixture to the exact request-contract v1 DataFrame signature and row-cardinality policy. After any wait, it fetches one endpoint snapshot and requires `READY`, `config_update=NOT_UPDATING`, no `pending_config`, and an exact match for config version, creator, route-optimization state and URL, served entities, versions, every scaling field, routes, traffic, and current telemetry. It blocks fixture transmission when any gate differs. For a route-optimized endpoint, the Workspace Client query path is not transport proof; report readiness as unverified until an authorized service-principal probe uses the dedicated URL. Semantic checks operate on a separate bounded raw in-memory response projection, decide exact cardinality, overflow, fallback rates, predicates, and response-bound threshold actions before reporting, then discard it. Reports are independently built from fixed failure kinds, counts, hashes, and allowlisted endpoint structure, never fixture or raw response field names and values.
9. For a row `predict`/DataFrame request only, load [references/semantic-readiness-patterns.md](references/semantic-readiness-patterns.md) for output contracts, representative sample payloads, all-fallback detection, score semantics, and notebook or client validation.
10. Open [references/monitoring-patterns.md](references/monitoring-patterns.md) only when monitoring, drift, or retraining policy is part of the task.

## Troubleshooting

- Endpoint not `READY`: inspect endpoint events and served entity build logs before changing model code.
- Endpoint `READY` while `config_update` is failed, canceled, or in progress: treat the rollout as incomplete. Do not query a stale active config or a zero-traffic version as proof of readiness.
- Route-optimized row/custom-model snapshot matches but transport is unverified: keep the rollout not ready. A separately authorized live probe must use the dedicated `endpoint_url`, OAuth M2M, `CAN_QUERY`, and endpoint-scoped `authorization_details`; do not mint or read credentials during an offline check. First confirm route optimization is supported for the classified source; it is not a generic LLM transport rule.
- Row/DataFrame fixture rejected before client construction: reconcile the exact signature fields, logical types, nullability, DataFrame columns or record keys, and request-contract version. Do not use opaque `instances`, `inputs`, `messages`, or `prompt` shapes for this row contract.
- Rollout contract v1 rejected before client construction: obtain an explicit owner classification for every threshold action, migrate it to `response_field` plus `action_field` or `downstream_only`, then set `contract_version` to 2. Keep `request_contract.version` at 1. Do not rewrite contracts automatically.
- LLM request or response mismatch: keep the protocol-specific request shape. For example, a Responses request uses `input` and requires a response `output` array, rather than a chat `messages` request or a row fixture. Use the LLM reference instead of changing the row contract.
- Router-only proof: direct evidence for every selected backend is required before the router's logical model can be called ready.
- Row/DataFrame endpoint `READY` but all fallback: verify model version or alias, feature lookup keys, null handling, thresholds, and response mapping.
- Row/DataFrame endpoint `READY` but score meaning is unclear: update the output contract and endpoint-visible metadata before calling rollout done.
- Row/DataFrame endpoint events unavailable: the doctor returns a warning by default; use `--require-events` when event availability is a release criterion. A warning means the output is partial, never an unqualified pass.
- Row/DataFrame readiness deadline: choose a positive `--wait-ready` budget. The doctor does not start another poll after the deadline, but one in-flight SDK request can exceed it by at most the configured request timeout; see [semantic-readiness-patterns.md](references/semantic-readiness-patterns.md).
- CLI hangs before checks: inspect import-time side effects and move heavy serving, model, or agent imports inside the command that needs them.
- Target mismatch risk: re-read the latest requested environment, print the target manifest from [references/prod-target-safety.md](references/prod-target-safety.md), then proceed.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [assets/serving-config-template.py](assets/serving-config-template.py) | You need a stable row/DataFrame endpoint configuration block | Consistent row-serving config |
| [assets/endpoint-config-schema.json](assets/endpoint-config-schema.json) | You need the machine-readable row/DataFrame endpoint contract | Stable row-serving parameter surface |
| [scripts/validate_endpoint_config.py](scripts/validate_endpoint_config.py) | You need to validate a row/DataFrame rollout contract offline | Value-free row-serving schema and semantic validation |
| [scripts/check_endpoint.py](scripts/check_endpoint.py) | You need to validate a live row/DataFrame endpoint | One-snapshot, bounded, redacted rollout and semantic report |
| [references/prod-target-safety.md](references/prod-target-safety.md) | You are deleting, resetting, or changing production routing | Target manifest and approval gate |
| [references/operation-classes.md](references/operation-classes.md) | Endpoint work may mutate config, shift traffic, move aliases, or delete/reset resources | Operation class and approval gate |

## References

- [references/core-serving-patterns.md](references/core-serving-patterns.md) - row/DataFrame endpoint lifecycle, rollout, and feature-serving patterns
- [references/semantic-readiness-patterns.md](references/semantic-readiness-patterns.md) - row/DataFrame semantic smoke tests, score meanings, fallback labels, and client validation
- [references/prod-target-safety.md](references/prod-target-safety.md) - target manifest and destructive-operation guardrails
- [references/operation-classes.md](references/operation-classes.md) - endpoint operation classes and approval gates
- [references/llm-router-serving-patterns.md](references/llm-router-serving-patterns.md) - LiteLLM, LibreChat, profile isolation, and backend health checks over Databricks endpoints
- [references/monitoring-patterns.md](references/monitoring-patterns.md) - monitoring and drift checks
