# Profile Workspace Preflight

Use this before `databricks bundle validate`, `deploy`, or `run` when the profile or workspace target is not already proven in the current session.

## Required Checks

1. Identify the intended profile and bundle target from the request, `databricks.yml`, repo docs, or prior commands.
2. Verify CLI identity:

```powershell
databricks current-user me --profile <profile> -o json
```

3. Confirm the workspace host and user or service principal match the intended environment.
4. Confirm the bundle target matches the work requested. Use the project's existing target names; `dev` and `prod` are defaults only when the project has no convention.
5. Use the command shape that matches the project auth model. Prefer explicit profile and target for live work when the bundle does not already encode the intended auth/target. If the bundle owns auth or the CLI docs/project convention require omitting a flag, record why and capture the resolved host, target, and principal from validation output before continuing.

```powershell
databricks bundle validate --profile <profile> --target <target>
# Or the repository's documented auth shape, with resolved host/principal recorded.
databricks bundle deploy --profile <profile> --target <target>
databricks bundle run <resource-key> --profile <profile> --target <target> --no-wait
```

If the target or profile is omitted intentionally, record that fact, the source of truth, and the resolved target/profile evidence before any live update or run trigger.

## Guardrails

- Do not rely on the CLI default profile for live bundle work unless the bundle explicitly owns auth and the resolved host/principal was just proven.
- Do not rely on the bundle default target for live work unless it was explicitly requested and just verified.
- Do not switch profiles to make a command pass unless the original workspace mismatch is proven.
- If a browser URL is used as context, compare its host with the CLI profile host before extracting job, run, model, or workspace IDs.
- Treat missing or wrong-profile authentication as a blocker, not a code defect.
