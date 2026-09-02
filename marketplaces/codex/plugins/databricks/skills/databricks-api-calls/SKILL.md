---
name: databricks-api-calls
description: "Use when calling Databricks APIs, SQL Statement API, inspecting workspace resources, or using local profiles. Do not trigger for notebook code, bundle config, serving rollout, or browsers."
metadata:
  short-description: Call Databricks APIs locally.
---

# Databricks API Calls


## When to Use

- Calling Databricks REST APIs from a local terminal or helper script
- Executing SQL Statement Execution API queries against a warehouse
- Inspecting Unity Catalog, MLflow, jobs, clusters, or other workspace resources
- Converting Databricks browser context into API identifiers before a local API call
- Debugging failed Databricks API requests, API-version mistakes, or shell-quoting problems
- Investigating unknown Databricks tables, schemas, model outputs, prediction columns, or timing questions
- Building local services that consume Databricks endpoints through CLI profiles or Databricks environment credentials

## When NOT to Use

- Code that already runs inside Databricks notebooks or jobs
- Databricks Asset Bundle configuration work
- Model serving endpoint design or rollout tasks, except for local model-gateway credential inspection before handing off
- Browser automation or fetching Databricks UI URLs directly

## Workflow

1. Classify the auth and execution context: CLI profile call, generated helper, local SDK script, containerized service, Databricks job, or model gateway. Preserve any user-named profile exactly and prove host/principal before using it.
2. Start with [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) when the profile, host, workspace, credential source, or target object is not already proven in this session. For multi-profile tasks, build a profile matrix before the first API call.
3. Read [references/api-call-workflows.md](references/api-call-workflows.md) and choose the call shape: direct CLI GET, generated GET or POST helper, generated SQL helper, or data investigation loop.
4. If the input is a Databricks browser URL, treat the URL as context only. Do not fetch the UI URL directly; extract IDs or full names from it, then map them to a real REST endpoint from [references/databricks-api-endpoints.md](references/databricks-api-endpoints.md).
5. Use direct CLI only for simple GET requests that do not need a JSON body or follow-up logic.
6. Before POST, PATCH, DELETE, SQL Statement Execution, registry mutation, job submit, repair, cancel, or any API with required `content` fields, load [references/operation-classes.md](references/operation-classes.md) and [references/request-body-contracts.md](references/request-body-contracts.md). Classify the request as inspect-only, local validation, workspace definition update, execution trigger, active execution control, production activation, or destructive operation; do not execute mutating work when the user requested only a definition or script. For a paginated list that supplies a bulk mutation set, also use [references/bulk-list-mutation.md](references/bulk-list-mutation.md).
7. Use [scripts/render_api_script.py](scripts/render_api_script.py) for reusable GET requests, JSON-body POST requests, and SQL Statement Execution requests. Supply every text-bearing POST input through `--body-file` and all SQL through `--statement-file`; generated source intentionally contains neither. Keep those runtime files and generated helpers in a private, access-controlled, untracked directory. Before Git classification or private file access, the helpers reject every existing symlink or Windows reparse-point component in the lexical path, then refuse targets Git currently sees as tracked or unignored. These are point-in-time safeguards, not proof against post-check filesystem races or ACL changes.
8. For Unity Catalog model version tags, enumerate with search when needed but fetch each version with `MlflowClient.get_model_version(name, version)` before reading tags.
9. For local services or model backends over Databricks endpoints, load [references/model-backend-gateways.md](references/model-backend-gateways.md), then hand off serving rollout or endpoint behavior to `databricks-model-serving`.
10. Re-run the helper after each edit and use its redacted structural diagnostics (exit code, statement ID, state, and machine-readable error code) before changing quoting or API versions. Do not add raw response, error, SQL, message, row, or external-link logging to diagnose a failure.


## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/render_api_script.py](scripts/render_api_script.py) | You need a reusable GET, POST, or SQL Statement Execution helper | Stable Python helper script |
| [scripts/check_databricks_context.py](scripts/check_databricks_context.py) | You need a redacted local context report for one or more profiles | JSON profile/auth matrix without printing secrets |
| [references/api-call-workflows.md](references/api-call-workflows.md) | You need the canonical routing workflow and failure patterns | Correct call selection |
| [references/databricks-api-endpoints.md](references/databricks-api-endpoints.md) | You need the right REST path or API version | Correct endpoint selection |
| [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) | You need to prove CLI profile and workspace identity | Correct target workspace before API calls |
| [references/request-body-contracts.md](references/request-body-contracts.md) | You need JSON body contracts before mutating, submitting, repairing, canceling, or SQL Statement calls | Required-field and pre/post-state checks |
| [references/bulk-list-mutation.md](references/bulk-list-mutation.md) | A paginated list supplies the target set for multiple mutations | Exhaustive enumeration, per-item outcome proof, and full post-state verification |
| [references/operation-classes.md](references/operation-classes.md) | An API call may mutate state, trigger compute, control active execution, affect production, or delete/reset resources | Permission-aware operation class before execution |
| [references/model-backend-gateways.md](references/model-backend-gateways.md) | You need Databricks endpoints behind a local model router or app | Credential and endpoint mapping before serving handoff |

## References

- [references/api-call-workflows.md](references/api-call-workflows.md) - routing, data investigation, examples, and anti-patterns
- [references/databricks-api-endpoints.md](references/databricks-api-endpoints.md) - common Databricks endpoints and state-changing labels
- [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) - profile, host, auth source, and workspace identity checks
- [references/request-body-contracts.md](references/request-body-contracts.md) - JSON body validation and state-changing proof bundles
- [references/bulk-list-mutation.md](references/bulk-list-mutation.md) - paginated list-to-mutation workflow and success accounting
- [references/operation-classes.md](references/operation-classes.md) - inspect, validation, definition-update, execution, production, and destructive operation gates
- [references/model-backend-gateways.md](references/model-backend-gateways.md) - Databricks-backed local model gateway credential routing
