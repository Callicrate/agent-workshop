# Databricks API Endpoints

Use this reference only to choose the REST path and API version. Call-shape rules live in [api-call-workflows.md](api-call-workflows.md).

## Unity Catalog — Models

| Operation | Method | Path |
|-----------|--------|------|
| List models in schema | GET | `/api/2.1/unity-catalog/models?catalog_name=X&schema_name=Y` |
| Get model details | GET | `/api/2.1/unity-catalog/models/{full_name}` |
| List model versions | GET | `/api/2.1/unity-catalog/models/{full_name}/versions?max_results=200` |
| Create registered model | POST | `/api/2.1/unity-catalog/models` with `{"name": "catalog.schema.model", "catalog_name": "X", "schema_name": "Y"}` |
| Delete model version | DELETE | `/api/2.1/unity-catalog/models/{full_name}/versions/{version}` |

- UC model endpoints use `/api/2.1/`, not `/api/2.0/`.
- Always use the three-part model name: `catalog.schema.model_name`.
- For pagination, pass `page_token` from `next_page_token`. Use `max_results=200` for bulk listing.

## MLflow — Runs and Model Versions

| Operation | Method | Path |
|-----------|--------|------|
| Get run details | GET | `/api/2.0/mlflow/runs/get?run_id=X` |
| Search runs | POST | `/api/2.0/mlflow/runs/search` |
| List artifacts | GET | `/api/2.0/mlflow/artifacts/list?run_id=X` |
| Create UC model version | POST | `/api/2.0/mlflow/unity-catalog/model-versions/create` |
| Copy model version | POST | `/api/2.0/mlflow/unity-catalog/model-versions/copy` |
| Set version tag | POST | `/api/2.0/mlflow/unity-catalog/model-versions/set-tag` |
| Get model version | GET | `/api/2.0/mlflow/model-versions/get?name=full.model.name&version=N` |
| Search model versions | GET | `/api/2.0/mlflow/model-versions/search?filter=name%3D%27full.model.name%27` |

- MLflow endpoints use `/api/2.0/`.
- URL-encode query parameters such as `filter`. In Python, use `urllib.parse.urlencode()`.
- For Unity Catalog models, do not read tags from `search_model_versions()` results. Use search only to enumerate versions, then call `MlflowClient.get_model_version(name, version)` or the get endpoint for exact version metadata.
- Tag-based filters are not supported for Unity Catalog model-version search. Search by exact model name and filter tags client-side after fetching each version.

## SQL Statement Execution

The field and result-completion rules below follow the [official Statement Execution API](https://docs.databricks.com/api/statement-execution/v1). Treat `external_link` values as credentials even when they are short-lived.

| Operation | Method | Path |
|-----------|--------|------|
| List warehouses | GET | `/api/2.0/sql/warehouses` |
| Execute SQL | POST | `/api/2.0/sql/statements` |
| Check statement status | GET | `/api/2.0/sql/statements/{statement_id}` |
| Cancel statement | POST | `/api/2.0/sql/statements/{statement_id}/cancel` |

**Execute SQL body**

```json
{
    "warehouse_id": "abc123def456",
    "statement": "SELECT * FROM catalog.schema.table LIMIT 10",
    "wait_timeout": "50s"
}
```

- Response states move through `PENDING`, `RUNNING`, and a terminal state.
- If `wait_timeout` expires while the query runs, poll `GET /api/2.0/sql/statements/{statement_id}` only under an explicit monotonic total deadline. On expiry, exit nonzero with statement ID and state; do not cancel or retry automatically.

**Result structure**

```text
resp["manifest"]["schema"]["columns"]  -> column metadata
resp["result"]["data_array"]           -> rows as arrays of strings
```

- A helper may treat a `SUCCEEDED` statement as eligible for its structural completion output only when `manifest` is an object with `truncated` exactly `false` and `result` is an object with no next-chunk, continuation, or external-link field. Any missing, malformed, truncated, externally linked, or chunked result fails closed. Do not silently skip chunks or claim completeness.
- `external_link` values are short-lived credentials. Never print, save, or include them in diagnostics. Design large-result retrieval separately with an explicit bounded, cycle-safe chunk policy.

## Jobs

| Operation | Method | Path |
|-----------|--------|------|
| List jobs | GET | `/api/2.1/jobs/list` |
| Get job | GET | `/api/2.1/jobs/get?job_id=X` |
| List runs | GET | `/api/2.1/jobs/runs/list?job_id=X` |
| Get run | GET | `/api/2.1/jobs/runs/get?run_id=X` |
| Run now | POST | `/api/2.1/jobs/run-now` |
| Repair run | POST | `/api/2.1/jobs/runs/repair` |
| Cancel run | POST | `/api/2.1/jobs/runs/cancel` |

- `list`, `get`, `runs/list`, and `runs/get` are inspect operations.
- `run-now`, `runs/repair`, and `runs/cancel` are state-changing operations and require the proof bundle in [request-body-contracts.md](request-body-contracts.md).
- Use this skill to inspect payloads. Use `databricks-deploy-monitor` for watch, fix, rerun, and output verification loops.

## State-Changing MLflow And Registry Operations

Operations such as delete model version, set tag, set alias, promote, repair, `run-now`, and cancel are state-changing even when they look like metadata updates. Fetch the current object immediately before mutation and fetch it again after mutation. A metric summary, UI URL, or remembered state is not enough proof.

## Clusters

| Operation | Method | Path |
|-----------|--------|------|
| List clusters | GET | `/api/2.0/clusters/list` |
| Get cluster | GET | `/api/2.0/clusters/get?cluster_id=X` |
| Start cluster | POST | `/api/2.0/clusters/start` |

## Error Codes

Common `error_code` values in Databricks API error responses:

| Code | Meaning | Common Cause |
|------|---------|--------------|
| `BAD_REQUEST` | Malformed request body or parameters | Missing required field, wrong type |
| `NOT_FOUND` | Resource doesn't exist | Wrong model name, wrong API version |
| `INVALID_PARAMETER_VALUE` | Parameter value out of range or wrong format | Bad warehouse ID, invalid filter syntax |
| `PERMISSION_DENIED` | Token lacks required ACL | Need workspace admin or specific object permission |
| `TEMPORARILY_UNAVAILABLE` | Service temporarily down | SQL warehouse starting up; retry with backoff |
| `RESOURCE_DOES_NOT_EXIST` | Specific resource not found | Model version deleted, run expired |
