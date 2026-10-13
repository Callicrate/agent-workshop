# Databricks Asset Bundles Quick Reference

Use this file for day-to-day bundle editing. Read a more focused reference only when the task needs more than the rules below.

## Default Contract

- Use the project's existing target topology first. If no convention exists, start with `dev` and `prod`.
- Prefer serverless for non-GPU jobs only when task APIs, libraries, runtime support, workspace policy, and cost profile fit.
- Pin serverless `environment_version` from the existing bundle or a current workspace-supported environment, not memory.
- Declare Python dependencies in the bundle, not with `%pip` inside notebooks.
- Validate with both `python scripts/validate_bundle.py` and `databricks bundle validate` using the proven profile/target command shape for this bundle.
- Treat `scripts/validate_bundle.py` as the authoritative static validator. `scripts/dab_doctor.mjs` is optional when Bun is available and a JSON-friendly companion report is useful.
- Before any Databricks CLI validation or deployment, verify the profile, workspace host, and target with [profile-workspace-preflight.md](profile-workspace-preflight.md) unless already proven in this session. For deploys, run triggers, production activation, or destructive changes, classify the action with [operation-classes.md](operation-classes.md).
- If the task changes Databricks code or notebooks in a bundle-backed project, validate and deploy the bundle before treating the change as complete when deployment is explicitly in scope.
- Verify runtime compatibility against the current target workspace: DBR, Python, Spark, MLflow, and any GPU/CPU assumptions. Do not rely on stale remembered version matrices.
- When a user asks to deploy, run, watch, monitor, or debug the live job, finish the bundle edit and then hand off to `databricks-deploy-monitor`. Do not unpause schedules, shift traffic, repair/cancel active runs, or trigger paid/long-running work unless the user explicitly requested it or the task contract already authorizes it.
- Stop at a ready-to-run plan before production activation, schedule unpause, traffic moves, alias moves, repair/cancel, delete/reset, or paid/long-running operations unless the user request or task contract authorizes the live action.

## Notebook And Runtime Coupling

- Notebook edits are deployment artifacts when the project is bundle-backed. Carry the notebook path, task type, runtime, libraries/environment, target, and deployment verification with the code edit.
- Notebook artifacts use `.ipynb`. Treat `.py` files as Python scripts or modules, not notebooks.
- Keep `%pip` out of notebooks unless the repo explicitly documents notebook-managed dependencies. Prefer bundle `libraries` or serverless `environments`.
- If a notebook requires a specific DBR, Spark, MLflow, GPU, or WorkspaceClient behavior, document it in the bundle or job configuration that runs the notebook.
- After changing notebook paths, parameters, dependencies, or runtime assumptions, run bundle validation and verify the live job settings after deploy.
- On-cluster tasks use the built-in Spark session. Do not configure Databricks Connect, `SPARK_REMOTE`, cluster IDs, or serverless connect settings inside scripts that run from a Databricks job cluster.

## Dev Versus Full Training Jobs

- Dev training jobs should use smaller meaningful caps, reduced tuning, and cheaper compute.
- Full training jobs should use the approved data window, tuning budget, and production-sized compute.
- Put workspace cache paths in variables or job parameters when extract/data-prep and training are split. Include date or timestamp segments so dev/full reruns do not collide.
- Keep cluster sizing explicit for both modes; do not silently reuse full-scale compute for dev checks or dev compute for full training.

## Workspace Owner Variable

Prefer an explicit `var.user_name` over `${workspace.current_user.userName}` for paths and permissions. The `current_user` lookup requires an interactive session and breaks headless validate/deploy (CI, service principals). Define a variable with a sensible default instead:

```yaml
variables:
  user_name:
    description: "Workspace owner for paths and permissions"
    default: "your.name@example.com"
```

Then reference `${var.user_name}` wherever you would have used `${workspace.current_user.userName}`.

## Permission Levels

Each permission entry must define exactly one non-empty principal key (`user_name`, `group_name`, or `service_principal_name`) and a non-empty `level`. Permission levels are surface-specific:

| Surface | Allowed levels |
|---------|----------------|
| Root `permissions` and `targets.<target>.permissions` | `CAN_MANAGE`, `CAN_RUN`, `CAN_VIEW` |
| `resources.alerts.<alert>.permissions` | `CAN_EDIT`, `CAN_MANAGE`, `CAN_READ`, `CAN_RUN` |
| `resources.apps.<app>.permissions` | `CAN_MANAGE`, `CAN_USE` |
| `resources.cluster_policies.<policy>.permissions` | `CAN_USE` |
| `resources.clusters.<cluster>.permissions` | `CAN_ATTACH_TO`, `CAN_MANAGE`, `CAN_RESTART` |
| `resources.dashboards.<dashboard>.permissions` | `CAN_EDIT`, `CAN_MANAGE`, `CAN_READ`, `CAN_RUN` |
| `resources.database_instances.<instance>.permissions` | `CAN_CREATE`, `CAN_MANAGE`, `CAN_USE` |
| `resources.genie_spaces.<space>.permissions` | `CAN_EDIT`, `CAN_MANAGE`, `CAN_RUN`, `CAN_VIEW` |
| `resources.experiments.<experiment>.permissions` | `CAN_EDIT`, `CAN_MANAGE`, `CAN_READ`, `CAN_RUN` |
| `resources.jobs.<job>.permissions` | `CAN_MANAGE`, `CAN_MANAGE_RUN`, `CAN_VIEW`, `IS_OWNER` |
| `resources.instance_pools.<pool>.permissions` | `CAN_ATTACH_TO`, `CAN_MANAGE` |
| `resources.model_serving_endpoints.<endpoint>.permissions` | `CAN_MANAGE`, `CAN_QUERY`, `CAN_VIEW` |
| `resources.models.<model>.permissions` | `CAN_EDIT`, `CAN_MANAGE`, `CAN_MANAGE_PRODUCTION_VERSIONS`, `CAN_MANAGE_STAGING_VERSIONS`, `CAN_READ` |
| `resources.pipelines.<pipeline>.permissions` | `CAN_MANAGE`, `CAN_RUN`, `CAN_VIEW`, `IS_OWNER` |
| `resources.secret_scopes.<scope>.permissions` | `MANAGE`, `READ`, `WRITE` |
| `resources.sql_warehouses.<warehouse>.permissions` | `CAN_MANAGE`, `CAN_MONITOR`, `CAN_USE`, `CAN_VIEW`, `IS_OWNER` |
| `resources.vector_search_endpoints.<endpoint>.permissions` | `CAN_CREATE`, `CAN_MANAGE`, `CAN_USE` |

Do not reuse a plausible-looking level across resource types. Target-level permissions layer above root top-level permissions. A target resource's permission list combines with the same root resource's list instead of replacing it; when the same principal appears at multiple scopes, Databricks applies its documented precedence order. Validate both declarations and do not reject an intentional repeated principal solely because it appears at more than one scope.

The static validators apply the exact closed set above for documented resource types. For another resource type that exposes `permissions`, they still validate every principal and use the current documented workspace `PermissionLevel` enum as a closed forward-compatible fallback; secret-scope-only `READ`, `WRITE`, and `MANAGE` are not part of that fallback. Run `databricks bundle validate` for the unknown type's exact schema. Resources without a `permissions` field are not permission-linted. Recheck the [current bundle permissions reference](https://docs.databricks.com/aws/en/dev-tools/bundles/permissions) and [workspace access-management enum](https://docs.databricks.com/api/access-management/v1/workspace) before changing these sets.

## Required Tags

Every job template should apply `tags: ${var.tags}` and `variables.tags.default` should include the enterprise-required keys:

| Tag | Default Pattern |
|-----|-----------------|
| `Team` | Owning team or group |
| `Project` | Project identifier or `${bundle.name}` |
| `Owner` | `${var.user_name}` or another primary contact |
| `DataClassification` | Data sensitivity level |
| `Environment` | `${bundle.target}` |
| `ApplicationName` | `${bundle.name}` |
| `ResourceOwner` | Resource owner org |
| `CiscoMailAlias` | Mailing list for alerts |
| `DataTaxonomy` | Data taxonomy description |
| `IntendedPublic` | String value such as `"false"` |

If a project-specific `AGENTS.md` or instruction file overrides this list, follow the project-specific contract and update `variables.tags.default` in the same bundle edit.

## Minimal Root Shape

```yaml
bundle:
  name: my-project
  databricks_cli_version: ">=0.218.0"

variables:
  user_name:
    description: "Workspace owner for paths and permissions"
    default: "your.name@example.com"
  catalog:
    default: "main"
  schema:
    default: "default"
  serverless_environment_version:
    description: "Workspace-supported serverless environment version; verify before changing"
    default: "4"
  tags:
    type: complex
    default:
      Team: "REPLACE_WITH_TEAM"
      Project: "${bundle.name}"
      Owner: "${var.user_name}"
      DataClassification: "REPLACE_WITH_DATA_CLASSIFICATION"
      Environment: "${bundle.target}"
      ApplicationName: "${bundle.name}"
      ResourceOwner: "REPLACE_WITH_RESOURCE_OWNER"
      CiscoMailAlias: "REPLACE_WITH_MAIL_ALIAS"
      DataTaxonomy: "REPLACE_WITH_DATA_TAXONOMY"
      IntendedPublic: "false"

permissions:
  - user_name: "${var.user_name}"
    level: CAN_MANAGE

workspace:
  root_path: /Workspace/Users/${var.user_name}/bundles/${bundle.name}

resources:
  jobs: {}

targets:
  dev:
    default: true
    mode: development
  prod:
    mode: production
```

## Dependency Rules

- Classic clusters: use `libraries` with pinned `pypi` or wheel entries.
- Serverless: use `environments[].spec.dependencies` with pinned versions.
- Keep wheel versions synchronized with the package build metadata and bundle variables.

## Version Assumptions

- Runtime and template dependency defaults live in [supported-runtimes.yml](supported-runtimes.yml).
- Last verified date for the current defaults: `2026-05-29`.
- Before treating a runtime warning as a hard product limit, verify the target workspace runtime selector and current Databricks documentation.
- When Databricks releases a newer approved DBR family, update `supported-runtimes.yml` or pass an explicit validator override after that runtime is verified for the project.
- Template package versions are examples, not evergreen recommendations. Verify current package compatibility, then pin exact versions in the bundle.

Static validator override example:

```powershell
python scripts/validate_bundle.py . --allow-runtime-prefix "18.0.x-scala2.12"
```

## CI/CD Pattern

- Use a service principal for non-interactive validation and deployment.
- Create or supply an explicit Databricks CLI profile for CI. Pass `--profile <profile>` when the bundle authentication model or CI runner requires it; otherwise record the resolved authentication source and do not silently fall back to DEFAULT.
- Keep production job execution under `targets.prod.run_as.service_principal_name` or root `run_as.service_principal_name`.
- Keep schedules paused until production approval is documented.

For the full pattern, read [ci-cd-patterns.md](ci-cd-patterns.md).

## Split-Bundle Rule

When the bundle grows large or mixes many resource types, keep variables, targets, and permissions in the root file and move resource definitions into `resources/*.yml` include files.

## Read Next

- Job, task, compute, or DLT wiring: [job-configuration.md](job-configuration.md)
- Job graph edits, target moves, one-off repair jobs, or parallelism: [job-topology-changes.md](job-topology-changes.md)
- GPU job clusters or training topology: [gpu-cluster-patterns.md](gpu-cluster-patterns.md)
- Python dependencies or wheel artifacts: [python-dependency-management.md](python-dependency-management.md)
- Include-based bundles or workspace sync: [resources-splitting.md](resources-splitting.md)
- Uncommon top-level keys: [dab-config-schema.md](dab-config-schema.md)
- Headless validation or deployment: [ci-cd-patterns.md](ci-cd-patterns.md)
