# API Call Workflows

Use this reference when the base skill routes you into local-terminal Databricks API work.

## Choose the Call Shape

| Situation | Use | Why |
|-----------|-----|-----|
| One-off read-only GET request | Direct CLI `api get` | Fastest path, no helper file needed |
| GET request you will rerun or customize | `render_api_script.py get` | Stable file for iteration |
| POST request with a JSON body | `render_api_script.py post` | Avoids shell-quoted JSON and keeps sensitive bodies in runtime files |
| SQL Statement Execution API call | `render_api_script.py sql` | Reads SQL at runtime, uses a bounded monotonic polling deadline, and fails closed on incomplete results |
| Paginated list feeding a bulk mutation | [Bulk List Mutation](bulk-list-mutation.md) | Freezes the full target set and proves every item outcome |
| Unknown tables, columns, metrics, overlap, timing, or correctness question | Data Investigation Loop | Proves schema and row counts before final SQL |
| Databricks browser URL or UI page | Extract identifiers, then call the matching REST endpoint | UI URLs are context only and are often behind SSO |

## Critical Rules

- Treat Databricks browser URLs as context, not as fetchable endpoints.
- Never use browser automation or raw HTTP fetches to get data from a Databricks UI URL. Use the URL only to identify the job, run, experiment, model, table, or workspace object, then call the API or CLI.
- Always pass `--profile` to the Databricks CLI.
- If the request names a profile, preserve that profile exactly in every CLI command, helper script, generated service config, and proof bundle unless you explicitly document the credential translation.
- Before changing or querying state, prove the profile points at the intended workspace with [profile-workspace-preflight.md](profile-workspace-preflight.md) unless this was already verified in the current session.
- Use [databricks-api-endpoints.md](databricks-api-endpoints.md) to choose the API path and version before making the call.
- Never use `python -c` for Databricks API calls that need JSON payloads, polling, or reusable helpers.
- Check the CLI return code and safe structural diagnostics before treating a failure as a quoting problem. Generated helpers deliberately do not print CLI stdout/stderr, error messages, SQL literals, rows, messages/content, or external links.
- Keep generated helper files, `--body-file` inputs, and `--statement-file` inputs in an access-controlled private directory that is excluded from source control. A runtime file read failure reports only its kind, never its path or contents.
- The renderer creates a new private output file and refuses an existing target rather than overwriting it. Choose a fresh untracked filename when regenerating a helper.

## Direct CLI Pattern

Use direct CLI only for simple GET requests that do not require post-processing:

```powershell
databricks --profile MY_PROFILE api get "/api/2.0/mlflow/runs/get?run_id=abc123"
```

## Generated Helper Runtime Inputs

Use a body file when a body contains any text, including credentials, messages, content, queries, statements, identifiers, or token-like values. Inline `--body` remains available only for fixed JSON structure with numeric, boolean, and null leaves, such as a numeric page size. The renderer refuses known secret-like keys and token patterns inline.

```powershell
# Keep this directory private, access-controlled, and untracked.
python skills/databricks-api-calls/scripts/render_api_script.py post `
  --profile MY_PROFILE `
  --path /api/2.0/mlflow/runs/search `
  --body-file C:/private/databricks/run-search.json `
  --output C:/private/databricks/search_runs.py
```

SQL text is always read at execution time:

```powershell
python skills/databricks-api-calls/scripts/render_api_script.py sql `
  --profile MY_PROFILE `
  --warehouse-id abc123 `
  --statement-file C:/private/databricks/query.sql `
  --poll-deadline-seconds 300 `
  --output C:/private/databricks/query.py
```

The generated SQL helper accepts a finite runtime `--poll-deadline-seconds` override between 1 and 3600 seconds. The deadline covers submission and polling, uses a monotonic clock, makes no cancel request, and exits nonzero with only statement ID and state when it expires. A `SUCCEEDED` response is accepted only when `manifest` is an object with `truncated: false` and `result` is an object with no next-chunk, continuation, or external-link field. Otherwise it exits nonzero rather than silently omitting chunks. On success it reports only statement ID and state; it never claims result materialization or completeness. Full-result retrieval requires a separately designed bounded, cycle-safe chunk-retrieval workflow.

Bad and good profile preservation:

```powershell
# WRONG - drops the requested profile and may hit DEFAULT
databricks api get "/api/2.1/jobs/list"

# CORRECT - preserves the named workspace context
databricks --profile EMAIL api get "/api/2.1/jobs/list"
```

## Data Investigation Loop

Use this for statistics, correctness, overlap, timing, table discovery, prediction-column discovery, or model comparison questions.

1. Prove the profile, host, principal, and UC namespace.
2. List candidate tables or models from the known catalog/schema or prefix.
3. Inspect schemas before writing the final query.
4. Run bounded samples only when schema does not identify the needed columns.
5. Map column roles: identifier, prediction, label, score, timestamp, model name/version, and source-specific filters.
6. Run cheap validation counts: total rows, non-null counts for chosen columns, timestamp min/max, distinct labels, and join-key uniqueness.
7. Write the final analysis SQL only after the column roles are proven.
8. Validate the result shape before answering the analytical question.

Do not answer model-overlap, confusion-matrix, or timing questions from table names alone.

Reusable discovery checklist:

```text
Table | Join key | Prediction | Label | Score | Timestamp | Filters | Row count check
...   | ...      | ...        | ...   | ...   | ...       | ...     | ...
```

## Unity Catalog Namespace Parsing

When the user provides a dotted UC name, parse it explicitly before building API paths, SQL, DDL, or table filters.

Good example:

```text
User input: users.jcallicr with prefix canon_
catalog: users
schema: jcallicr
object prefix: canon_
example table: users.jcallicr.canon_entities
```

Do not invent nested schemas such as `users.jcallicr.canon.entities` unless the user gave a four-part object contract.

## Generated Script Workflow

### GET Helper

Use the helper generator for reusable GET requests:

```powershell
python scripts/render_api_script.py get `
  --profile MY_PROFILE `
  --path "/api/2.1/unity-catalog/models/catalog.schema.model/versions?max_results=10" `
  --output C:/private/databricks/query_model_versions.py
```

Generated helpers print only allowlisted structural summaries. Do not edit one to add response-specific raw printing; design a separately reviewed, data-minimizing consumer when raw result handling is genuinely required.

### POST Helper

Use the POST generator when the endpoint needs a JSON body:

```powershell
python scripts/render_api_script.py post `
  --profile MY_PROFILE `
  --path "/api/2.0/mlflow/runs/search" `
  --body-file C:/private/databricks/request-body.json `
  --output C:/private/databricks/search_runs.py
```

The body file must contain valid JSON and belongs in a private, access-controlled, untracked directory. Regenerate only when the endpoint or initial body structure changes; do not edit the generated helper to embed request content.

### SQL Helper

Use the SQL generator when the request body or polling logic would otherwise be handwritten:

```powershell
python scripts/render_api_script.py sql `
  --profile MY_PROFILE `
  --warehouse-id YOUR_WAREHOUSE_ID `
  --statement-file C:/private/databricks/query.sql `
  --poll-deadline-seconds 300 `
  --output C:/private/databricks/query_warehouse.py
```

The generated script reads SQL at runtime, handles `PENDING` and `RUNNING` only until its total monotonic deadline, and fails closed when results are truncated or require another chunk. It never auto-cancels or retries a statement.

## Browser URL Handling

- Treat the URL as a source of IDs or full names only.
- Do not try to fetch the UI URL itself. Databricks UI pages require browser SSO and are not stable API surfaces for agents.
- Extract the identifier that the UI page represents, then call the matching REST endpoint.
- Common mappings:
  - Jobs page -> `job_id` or `run_id` -> Jobs API endpoints
  - MLflow run page -> `run_id` -> `/api/2.0/mlflow/runs/get?run_id=...`
  - Unity Catalog model page -> full model name -> UC model endpoints

## Unity Catalog Model Version Metadata

When tags or exact metadata matter, do not rely on search results alone:

```python
from mlflow import MlflowClient

client = MlflowClient()
for match in client.search_model_versions("name = 'catalog.schema.model'"):
    version = client.get_model_version(match.name, match.version)
    print(version.version, dict(version.tags))
```

- `search_model_versions()` is useful for enumeration, but Unity Catalog search results can be incomplete for tag-driven workflows.
- Call `get_model_version(name, version)` before reading tags, aliases, source run IDs, or other per-version metadata.
- Tag-based filters are not supported for Unity Catalog model searches. Search by exact model name, then inspect individual versions.

## Jobs Intent Boundaries

Classify Jobs API work before calling POST endpoints:

- inspect: list jobs, get job, list runs, get run
- define-only: create or render job config, validation commands, or repair payload without running it
- submit: `run-now`
- repair: `runs/repair`
- cancel: `runs/cancel`
- monitor: hand off to `databricks-deploy-monitor` when watching, fixing, or iterating runs

If the user says `don't run it`, `make me a job`, or asks only for a script/config, stay in define-only mode and do not call `run-now`, `repair`, or `cancel`.

Use this skill for inspecting job and run payloads. Use `databricks-deploy-monitor` for deploy, run, watch, fix, output verification, or long-running monitor loops. Use `databricks-spark-etl` when a successful or failed run requires table backfill validation.

## Retroactive Fixes For Running Jobs

When code changes after long-running Databricks jobs already started, inspect before proposing a backfill:

- run start time and source version or notebook revision if available
- expected side effects, such as MLflow registration, Delta writes, or promotion table rows
- which side effects the in-flight run can no longer produce
- table rows or model versions missing because code changed after run start

Then propose a bounded one-off backfill script or job, or hand off to the owning ETL/training/deploy-monitor skill.

## Delta Table As Request/Response Bridge

When Databricks cannot reach an external API directly (firewall block) but both sides can reach a shared Delta table, use the table as a synchronous request/response bridge instead of an async queue:

1. The in-workspace caller inserts a request row (for example a `request` field) into a shared table and records its key.
2. The external side polls that row (for example every 2 seconds) via the SQL Statement Execution API, does the work, and writes the result back into the same row (`response`/`status` fields).
3. The caller polls the same key until the response/status field is populated, then reads the result.

- Prefer this in-place synchronous pattern for validation-style, request-then-result calls where the caller blocks for one answer.
- Key each request uniquely and treat a missing/`NULL` response as "not ready", not as failure, until a timeout.
- Reuse the SQL polling helper (`render_api_script.py sql`) for the external side instead of handwritten polling.

## Failure Checks

- Wrong API version for the resource family, especially `/api/2.0/` versus `/api/2.1/`
- Shell-quoted JSON bodies instead of a helper script or body file
- Parsing stdout as JSON after the Databricks CLI already returned a non-zero exit code
- Treating SQL `PENDING` or `RUNNING` as empty results instead of polling
- Forgetting to URL-encode query parameters for endpoints such as MLflow search
- Reading Unity Catalog model-version tags from `search_model_versions()` results instead of fetching each version with `get_model_version()`

## Anti-Patterns

- `python -c` with inline dicts or list comprehensions
- triple-escaped JSON strings that try to compensate for shell quoting
- using the Databricks UI URL itself as the request target
- producing comparison SQL before schema and column-role discovery
- executing a Jobs API POST after a define-only request

When one of these patterns appears, stop and switch back to a generated or checked-in script file.
