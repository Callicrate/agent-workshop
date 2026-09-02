# Production Target Safety

Use this before endpoint deletion, reset, redeploy, alias movement, traffic shift, or artifact cleanup.
Classify the action with [operation-classes.md](operation-classes.md) before execution.

## Target Manifest

Print or record a target manifest before any destructive or production-affecting operation:

- latest user-requested environment: dev, staging, or prod
- Databricks CLI profile
- workspace host
- DAB target when a bundle owns deployment
- endpoint name
- UC model full name
- model version or alias
- served entity name
- current config version, exact pre-state routes, and intended traffic route changes
- exact scaling mode and every applicable workload, concurrency, throughput, model-unit, burst, and scale-to-zero field
- current `telemetry_config` and expected post-state telemetry
- immutable creator principal and required Unity Catalog grants
- whether the operation is destructive
- exact command, API call, or script to run
- durable manifest path, pre-state digest, and reverse operation artifact

Do not continue if the latest user message changed the target and the manifest has not been rebuilt.

Production activation, traffic movement, alias movement, endpoint reset/delete, and served-entity removal require explicit user approval or an existing task contract that already authorizes the live operation.

## Destructive Operations

Destructive operations include:

- endpoint delete or reset
- served entity removal
- model or alias cleanup
- production traffic movement
- legacy auto-capture table cleanup during an approved migration
- removal of deployment artifacts

For production, keep the manifest in the final note or handoff artifact and verify the endpoint name contains the intended environment only when the project naming convention requires it. Do not infer prod or dev from stale shell history.

## Recovery Contract

Persist the pre-state before mutation, including endpoint config version, served entities, routes, traffic percentages, scale policy, telemetry, target URL, and creator identity.
Hash the canonical pre-state and record a bounded relative artifact path in the rollout contract.
Define one executable reverse operation: shift routes back to the previously healthy entity, or restore the prior config.
Prefer a route shift over endpoint deletion because it preserves the endpoint identity, permissions, telemetry binding, and immutable creator relationship.
Delete and recreate only when the creator identity is invalid or the endpoint cannot be repaired in place, and only with explicit destructive approval plus a complete reconstruction manifest.

## Bad And Good Patterns

Bad pattern:

```text
User said remove dev earlier, so delete the dev endpoint after a later correction says prod.
```

Good pattern:

```text
Latest target: prod.
Profile: prod-profile.
Host: https://example.cloud.databricks.com.
Endpoint: risk-model-prod.
Model: main.ml.risk_model alias Production.
Operation: destructive endpoint reset followed by redeploy.
Proceed only after this manifest matches the latest user request.
```

## Reset Redeploy Verify Loop

1. Re-read the latest requested target.
2. Build the target manifest.
3. Inspect current endpoint, served entities, traffic routes, and model aliases.
4. Persist and hash the pre-state plus reverse operation before any mutation.
5. Prefer a traffic route shift or config restore. Remove only artifacts in an explicitly approved destructive manifest.
6. Deploy or update the intended endpoint.
7. Wait for readiness, fetch one post-wait snapshot, and require `READY`, `NOT_UPDATING`, no pending config, exact route-optimization state, and an exact contract match.
8. For route optimization, keep readiness unverified until a separately authorized service-principal probe uses the dedicated URL and endpoint-scoped authorization detail. Never mint or inspect its credential in an offline validation pass.
9. Run direct semantic smoke queries only after the snapshot and transport gates pass.
10. Validate the notebook, client, or app path when one is part of the task.
