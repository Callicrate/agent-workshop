# Profile Workspace Preflight

Use this before Databricks API calls when the profile or target workspace is not already proven in the current session.

## Required Checks

1. Identify the intended profile from the user request, repo docs, environment, or prior command. Do not silently fall back to `DEFAULT`.
2. Select the exact profile, then check the effective authentication context and host:

```powershell
databricks auth describe --profile <profile> -o json
databricks current-user me --profile <profile> -o json
```

3. Confirm the effective profile and host from `auth describe`, then the returned user/service principal, match the task context. A config-file host is only a hint and must not substitute for the effective target.
4. If the task includes a browser URL, compare the URL host to the CLI profile host before extracting IDs.
5. For destructive or state-changing calls, name the target object and workspace in the plan or command note before execution.

## SQL Warehouse Selection Before Statement Submission

Before rendering or posting a Statement Execution request, select its warehouse through the same profile that passed the checks above. Warehouse discovery is inspect-only. SQL submission remains an execution trigger and needs its separate authorization.

Do not echo raw list or detail payloads into terminal transcripts, source control, or shared logs. Keep them local and project only the selection fields.

1. When the installed CLI exposes the `warehouses` command group, enumerate candidate IDs with the selected profile:

```powershell
$raw = databricks warehouses list --profile <profile> -o json 2>$null
if ($LASTEXITCODE -ne 0) { throw "Warehouse enumeration failed." }
$document = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
$warehouses = if ($document -is [System.Array]) { $document } else { @($document.warehouses) }
$warehouses | Select-Object id, state | ConvertTo-Json -Compress
```

   Do not claim the CLI result is a complete inventory because its pagination behavior can vary. If a complete inventory is required or the command group is unavailable, use the generic CLI API command and follow every `next_page_token` before making that claim:

```powershell
$token = $null
do {
  $path = "/api/2.0/sql/warehouses"
  if ($token) { $path += "?page_token=$([uri]::EscapeDataString([string]$token))" }
  $raw = databricks --profile <profile> api get $path 2>$null
  if ($LASTEXITCODE -ne 0) { throw "Warehouse enumeration failed." }
  $page = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
  @($page.warehouses) | Select-Object id, state | ConvertTo-Json -Compress
  $token = $page.next_page_token
} while ($token)
```

2. Select one nonempty `id` from that response, then inspect that exact ID with the same profile. Prefer the CLI command group when it is available:

```powershell
$raw = databricks warehouses get <warehouse-id> --profile <profile> -o json 2>$null
if ($LASTEXITCODE -ne 0) { throw "Warehouse inspection failed." }
(($raw -join [Environment]::NewLine) | ConvertFrom-Json) | Select-Object id, state | ConvertTo-Json -Compress
```

   If the command group is unavailable, use the generic CLI API command and the same bounded projection:

```powershell
$raw = databricks --profile <profile> api get /api/2.0/sql/warehouses/<warehouse-id> 2>$null
if ($LASTEXITCODE -ne 0) { throw "Warehouse inspection failed." }
(($raw -join [Environment]::NewLine) | ConvertFrom-Json) | Select-Object id, state | ConvertTo-Json -Compress
```

3. Confirm the inspection response identifies that exact warehouse. An empty value, `AUTO`, a display name, a remembered ID, or an ID from another profile is not a valid selection. Listing or getting a warehouse proves selection and observed state, not that the caller has permission to execute a statement.
4. Before SQL rendering or submission, retain this minimal local receipt. Keep raw warehouse payloads out of source control and shared logs:

```text
profile: <profile>
warehouse_id: <warehouse-id>
observed_state: <state>
selection_verified: <true | false>
submission_authorized: <true | false>
permission_precheck: <verified | unknown>
start_policy: <permitted auto-start | explicit start authorization required | not applicable>
ready_to_render: <true | false>
ready_to_submit: <true | false>
statement_api_permission_result: <not-yet-attempted | state-or-error-code>
```

An observed state alone does not determine readiness. A stopped warehouse can be eligible when the task's policy permits auto-start. Set `ready_to_render` to `true` after selection is verified, even when `submission_authorized` is `false`; render the local helper with `ready_to_submit: false` for script-only work. Stop only before the actual POST when selection is unverified, the task does not authorize submission, or the necessary start policy is absent. `permission_precheck: unknown` does not block an otherwise authorized submission. The Statement API response's safe state or machine-readable error code is the definitive permission outcome; record it, then stop on failure without changing profile or warehouse ID or retrying blindly.

## Auth Source Selection

Choose and record one credential source before calling APIs:

| Context | Preferred Source | Rule |
|---------|------------------|------|
| Local Databricks CLI call | explicit `--profile <profile>` | Preserve user-named profiles such as `TAP` or `EMAIL`; do not use bare `databricks` unless the default was just proven equivalent |
| Generated helper script | explicit `--profile <profile>` embedded in the helper | Regenerate or edit the helper when the profile changes; do not let SDK defaults choose the workspace |
| Local SDK script | documented profile or `DATABRICKS_HOST`/`DATABRICKS_TOKEN` | State which one wins and verify it without printing token values |
| Containerized service | injected secrets or env vars | Document the secret names, mount or env path, and a secret-safe health check |
| Databricks job | job-provided workspace identity | Do not require Databricks Connect, `SPARK_REMOTE`, local profile files, or local tokens inside the job |

Never leave precedence implicit. If both a profile and `DATABRICKS_HOST` or `DATABRICKS_TOKEN` are present, state which path the code uses before running it.

## Local Context Preflight

For app/backend work or unfamiliar local environments, run the safe context helper with the exact named profiles. It reports only command availability, allowlisted HTTPS hosts, selected principal identifiers, and boolean secret presence. It never prints the config path, raw CLI output, malformed current-user content, failed stderr, display names, or secret values. It also refuses to read a profile config from a Git-tracked or unignored path and reports only a `safe_to_read` boolean:

```powershell
python skills/databricks-api-calls/scripts/check_databricks_context.py --profiles TAP EMAIL
```

The helper never chooses `DEFAULT` or any config profile implicitly. For each named profile it also emits a stable effective-context receipt after the CLI resolves the profile:

Its config metadata has the same field scope as the Databricks SDK: a named profile reports only fields explicitly declared in that named section, never fields inherited from `[DEFAULT]`. `[DEFAULT]` is listed and reported when it contains fields, and can then be passed explicitly with `--profiles DEFAULT`; an empty `[DEFAULT]` is not a configured profile. This local metadata does not resolve SDK fallback, environment precedence, or authentication success.

```json
{
  "version": 1,
  "ok": true,
  "profile": "TAP",
  "host": "https://workspace.example"
}
```

Use it as a two-step handoff: first write the helper's JSON to a temporary file outside the project root, then pass that file to a status or API workflow. A consumer may treat the profile and host as verified only when `effective_context.version` is `1`, `ok` is `true`, and both strings are present. On any failure, `ok` is `false`, `host` is omitted, and the receipt must not be used to select a workspace.

```powershell
$contextFile = New-TemporaryFile
try {
  python skills/databricks-api-calls/scripts/check_databricks_context.py --profiles TAP | Set-Content -LiteralPath $contextFile -Encoding utf8
  python skills/databricks-project-status/scripts/status_report.py create --project-root . --profile TAP --context-file $contextFile
} finally {
  Remove-Item -LiteralPath $contextFile -ErrorAction SilentlyContinue
}
```

If using the helper outside the agents repo, copy or invoke it by absolute path and pass the exact profiles from the user request.

If copying the context helper, copy its sibling `safe_databricks_diagnostics.py` beside it. The context helper is intentionally not standalone: the sibling enforces bounded process output, redaction budgets, and the same Git/private-runtime boundary used by generated helpers.

## Multi-Profile Matrix

When a task mentions more than one profile or profile-backed model backend, fill this matrix before the first API call:

```text
Profile | Host | Principal | Intended use | Operation mode | Credential source
TAP     | ...  | ...       | Vector/search | read/write     | CLI profile
EMAIL   | ...  | ...       | Model/data    | read-only      | CLI profile
```

Operation mode must say whether the profile is inspect-only, read/write, registry mutation, job submit, or service backend. Do not collapse named profiles into `DEFAULT`, SDK defaults, or env vars unless a same-host and same-principal proof exists in the current session.

## Live-State And Freshness Scope

A proven profile does not prove the work is current. For "is the job keeping up / current / healthy" questions, after the profile resolves, verify scope, then freshness:

1. Resolve the concrete object IDs in scope (job IDs, pipeline IDs, table names), not just the workspace host.
2. Check recent run states for those IDs (for example `/api/2.1/jobs/runs/list?job_id=...`).
3. Confirm output freshness against expectation: latest output-row timestamp, max event time, or last successful run end time.

Report the object, its recent run state, and the freshness gap together. Do not infer currency from a live profile alone.

## Failure Handling

- If the profile is missing, expired, or points to the wrong host, stop and report that as the blocker.
- If a dead or blocking proxy env var breaks auth, clear it for one non-persistent command scope, not a persistent machine/user change:

```powershell
# WRONG - persistent change for a one-off read
setx HTTPS_PROXY ""

# CORRECT - clear only for this process, then run the read
$env:HTTPS_PROXY = ""; databricks current-user me --profile <profile> -o json
```

- If multiple plausible profiles exist, prefer the one documented by the repo or current task. Ask only when no local evidence distinguishes them.
- Do not retry failed API calls by changing profiles unless the workspace mismatch is proven.
