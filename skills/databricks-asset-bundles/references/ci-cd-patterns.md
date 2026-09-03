# CI/CD Patterns For Bundles

Use this file when bundle validation, deployment, or permissions need to work without an interactive Databricks user session.

## Non-Interactive Identity

- Use a service principal for CI validation and deployment.
- Store client secrets only in the CI secret store.
- Create or provide a Databricks CLI profile for the CI identity, then pass `--profile <profile>` on every `databricks bundle` command.
- Do not depend on `${workspace.current_user.userName}` in bundle paths, permissions, or tags.
  Define `variables.user_name` and set it to the intended workspace owner or service principal contact.

## Deploying Identity Versus Run Identity

The identity that deploys the bundle and the identity that runs production jobs are separate contracts.

- **Deploying identity:** the CI service principal that runs `databricks bundle validate` and `databricks bundle deploy`.
- **Run identity:** `targets.prod.run_as.service_principal_name` or a root-level `run_as` service principal used by deployed jobs.

Give the deploying identity permission to create and update the resources.
Give the run identity the data and workspace permissions needed by the job tasks.
Do not assume deploy permissions automatically satisfy runtime access.

## Minimal CI Command Shape

```powershell
databricks bundle validate --profile <ci-profile> --target dev
databricks bundle deploy --profile <ci-profile> --target dev
```

For production, keep schedules paused until the production-activation gate is satisfied.
Then deploy the prod target explicitly:

```powershell
databricks bundle validate --profile <ci-profile> --target prod
databricks bundle deploy --profile <ci-profile> --target prod
```

## GitHub Actions Skeleton

```yaml
name: "Validate Databricks Bundle"

on:
  pull_request:
  workflow_dispatch:

jobs:
  validate-bundle:
    runs-on: "ubuntu-latest"
    steps:
      - uses: "actions/checkout@v4"
      - uses: "actions/setup-python@v5"
        with:
          python-version: "3.12"
      - name: "Install static validator dependencies"
        run: "python -m pip install -r skills/databricks-asset-bundles/scripts/requirements.txt"
      - name: "Run static validator"
        run: "python skills/databricks-asset-bundles/scripts/validate_bundle.py . --strict"
      - name: "Validate bundle against workspace"
        env:
          DATABRICKS_HOST: "${{ secrets.DATABRICKS_HOST }}"
          DATABRICKS_CLIENT_ID: "${{ secrets.DATABRICKS_CLIENT_ID }}"
          DATABRICKS_CLIENT_SECRET: "${{ secrets.DATABRICKS_CLIENT_SECRET }}"
        run: "databricks bundle validate --profile ci --target dev"
```

Replace the profile bootstrap with the repository's approved authentication setup.
The important invariant is that the bundle command names a profile and target explicitly.

## Production Gate

Before enabling an unpaused production schedule, confirm:

- `targets.prod.run_as.service_principal_name` or root `run_as.service_principal_name` is set.
- Required tags are complete and environment-specific values resolve.
- Stakeholder review criteria and explicit production approval are documented.
- The prod deploy command is run with explicit `--profile` and `--target prod`.