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
