# Model Backend Gateways

Use this when a local app, LiteLLM router, LibreChat instance, Pydantic agent, or service config consumes Databricks model endpoints as backends.

## Routing Boundary

This skill owns profile, credential, endpoint inventory, and local API-call proof. Switch to `databricks-model-serving` for endpoint rollout, traffic routing, model serving configuration, semantic readiness, and live endpoint health.

Return here only when the remaining task is local credential wiring, request payload inspection, or API troubleshooting.

## Gateway Contract

Before editing service config, record:

- logical model name exposed to the app
- Databricks endpoint name or URL
- profile or credential source for each backend
- workspace host for each backend
- secret injection mechanism, such as env vars, Docker secrets, `.env`, or platform secret manager
- health-check command that does not print secrets

## Multi-Profile Model Backends

If profiles such as `TAP` and `EMAIL` map to different backends, keep them separate in the config and in tests. Do not collapse them into `DEFAULT` or a single `DATABRICKS_HOST` unless the user explicitly asks for that consolidation and the profile matrix proves it is safe.

## Container Env Rules

- Do not bake Databricks tokens into images, source files, or committed `.env` files.
- Document which secret names enter the container.
- Verify boolean presence of secrets, not their values.
- Prefer one health-check route per logical backend.

Example secret-safe check:

```powershell
[pscustomobject]@{
  DATABRICKS_HOST = [bool]$env:DATABRICKS_HOST
  DATABRICKS_TOKEN = [bool]$env:DATABRICKS_TOKEN
  TAP_TOKEN = [bool]$env:TAP_TOKEN
  EMAIL_TOKEN = [bool]$env:EMAIL_TOKEN
}
```