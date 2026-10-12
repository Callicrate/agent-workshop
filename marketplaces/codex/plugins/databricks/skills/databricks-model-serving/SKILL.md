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

1. Define the closed rollout contract with [assets/serving-config-template.py](assets/serving-config-template.py) and [assets/endpoint-config-schema.json](assets/endpoint-config-schema.json), then run `python scripts/validate_endpoint_config.py --config <contract.json>` from this skill root. Do not deploy from an invalid or partial contract.
2. Pick the ownership mode first: DAB-managed endpoint, SDK/API-managed endpoint, or external router backed by Databricks endpoints. If the endpoint is DAB-managed, treat bundle config as source of truth; use imperative SDK/API changes only for inspection, emergency remediation, or explicitly non-bundle-managed endpoints.
3. Use [references/core-serving-patterns.md](references/core-serving-patterns.md) for the serving-mode decision, rollout pattern, Unity Catalog loading approach, feature-serving rules, and upstream model handoff fields.
4. Keep the non-negotiable guardrails intact: do not embed large data assets in the model artifact, do not mix DAB declarative and SDK imperative management for the same endpoint, and do not set `DATABRICKS_HOST` inside serving containers.
5. Before destructive operations, production traffic shifts, alias moves, endpoint resets/deletes, served-entity removal, or paid long-running rebuilds, load [references/prod-target-safety.md](references/prod-target-safety.md), print a target manifest, and require explicit approval unless the current task contract already grants that live operation.
6. Run [scripts/check_endpoint.py](scripts/check_endpoint.py) with `--contract <contract.json>` after deployment or config changes. Before client construction, it binds a fixture to the exact versioned DataFrame request signature and row-cardinality policy. After any wait, it fetches one endpoint snapshot and requires `READY`, `config_update=NOT_UPDATING`, no `pending_config`, and an exact match for config version, creator, route-optimization state and URL, served entities, versions, every scaling field, routes, traffic, and current telemetry. It blocks fixture transmission when any gate differs. For a route-optimized endpoint, the Workspace Client query path is not transport proof; report readiness as unverified until an authorized service-principal probe uses the dedicated URL. Semantic checks operate on a separate bounded raw in-memory response projection, decide exact cardinality and overflow before reporting, then discard it. Reports are independently built from fixed failure kinds, counts, hashes, and allowlisted endpoint structure, never fixture or raw response field names and values.
7. Load [references/semantic-readiness-patterns.md](references/semantic-readiness-patterns.md) for output contracts, representative sample payloads, all-fallback detection, score semantics, and notebook or client validation.
8. Load [references/llm-router-serving-patterns.md](references/llm-router-serving-patterns.md) when Databricks endpoints are LLM backends behind LiteLLM, LibreChat, OpenAI-compatible routers, or profile-specific model gateways.
9. Open [references/monitoring-patterns.md](references/monitoring-patterns.md) only when monitoring, drift, or retraining policy is part of the task.

## Troubleshooting

- Endpoint not `READY`: inspect endpoint events and served entity build logs before changing model code.
- Endpoint `READY` while `config_update` is failed, canceled, or in progress: treat the rollout as incomplete. Do not query a stale active config or a zero-traffic version as proof of readiness.
- Route-optimized snapshot matches but transport is unverified: keep the rollout not ready. A separately authorized live probe must use the dedicated `endpoint_url`, OAuth M2M, `CAN_QUERY`, and endpoint-scoped `authorization_details`; do not mint or read credentials during an offline check.
- Fixture rejected before client construction: reconcile the exact signature fields, logical types, nullability, DataFrame columns or record keys, and request-contract version. Do not fall back to opaque `instances`, `inputs`, `messages`, or `prompt` shapes.
- `READY` but all fallback: verify model version or alias, feature lookup keys, null handling, thresholds, and response mapping.
- `READY` but score meaning is unclear: update the output contract and endpoint-visible metadata before calling rollout done.
- Endpoint events unavailable: the doctor returns a warning by default; use `--require-events` when event availability is a release criterion. A warning means the output is partial, never an unqualified pass.
- Readiness deadline: choose a positive `--wait-ready` budget. The doctor does not start another poll after the deadline, but one in-flight SDK request can exceed it by at most the configured request timeout; see [semantic-readiness-patterns.md](references/semantic-readiness-patterns.md).
- CLI hangs before checks: inspect import-time side effects and move heavy serving, model, or agent imports inside the command that needs them.
- Target mismatch risk: re-read the latest requested environment, print the target manifest from [references/prod-target-safety.md](references/prod-target-safety.md), then proceed.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [assets/serving-config-template.py](assets/serving-config-template.py) | You need a stable endpoint configuration block | Consistent serving config |
| [assets/endpoint-config-schema.json](assets/endpoint-config-schema.json) | You need the machine-readable endpoint contract | Stable serving parameter surface |
| [scripts/validate_endpoint_config.py](scripts/validate_endpoint_config.py) | You need to validate a rollout contract offline | Value-free schema and semantic validation |
| [scripts/check_endpoint.py](scripts/check_endpoint.py) | You need to validate a live endpoint | One-snapshot, bounded, redacted rollout and semantic report |
| [references/prod-target-safety.md](references/prod-target-safety.md) | You are deleting, resetting, or changing production routing | Target manifest and approval gate |
| [references/operation-classes.md](references/operation-classes.md) | Endpoint work may mutate config, shift traffic, move aliases, or delete/reset resources | Operation class and approval gate |

## References

- [references/core-serving-patterns.md](references/core-serving-patterns.md) - endpoint lifecycle, rollout, and feature-serving patterns
- [references/semantic-readiness-patterns.md](references/semantic-readiness-patterns.md) - semantic smoke tests, score meanings, fallback labels, and client validation
- [references/prod-target-safety.md](references/prod-target-safety.md) - target manifest and destructive-operation guardrails
- [references/operation-classes.md](references/operation-classes.md) - endpoint operation classes and approval gates
- [references/llm-router-serving-patterns.md](references/llm-router-serving-patterns.md) - LiteLLM, LibreChat, profile isolation, and backend health checks over Databricks endpoints
- [references/monitoring-patterns.md](references/monitoring-patterns.md) - monitoring and drift checks
