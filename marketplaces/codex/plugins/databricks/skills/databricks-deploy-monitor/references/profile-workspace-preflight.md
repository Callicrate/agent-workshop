# Profile Workspace Preflight

Use this before bundle validation, deployment, run monitoring, or live job-setting checks when the profile or target workspace is not already proven in the current session.

## Required Checks

1. Identify the intended profile from the repo, bundle target, user request, or prior command. Do not silently use `DEFAULT`.
2. Verify authentication and identity:

```powershell
databricks auth profiles
databricks current-user me --profile <profile> -o json
```

3. Confirm the workspace host, user or service principal, and bundle target match the task.
4. Before `bundle deploy`, name the bundle target and workspace being changed.
5. Before monitoring or fetching output, confirm the `run_id` belongs to the same workspace and profile.
6. Record the profile, workspace host, target, job key, job ID, and run ID in the run ledger when a loop may span waits, compaction, or handoff.

## Read-Only Source Catalogs

When the job reads external or shared catalogs such as `tap`, inspect them as read-only sources:

- verify the named profile and workspace host before every live read
- confirm source table access mode and fully qualified table names
- check max update, ingestion, or partition timestamps when freshness matters
- do not write, create, alter, repair, optimize, or delete objects outside owned target schemas
- route fixes for stale external feeds to the source owner instead of changing deployment state

Output tables, audit tables, temporary repair tables, and bundle-managed resources must stay in owned project schemas unless the user explicitly gives a different write contract.

## Blockers

- Missing, expired, or wrong-host profile
- Ambiguous profile with no repo or task evidence to choose from
- Run/job URL host does not match the CLI profile host
- Permissions prevent validation, deploy, run, or log retrieval

Treat these as concrete blockers instead of guessing another profile.
