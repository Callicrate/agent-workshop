# DAB Config Surface

Use this file only when [dab-quick-reference.md](dab-quick-reference.md) is not enough.
For the official full specification, use the Databricks bundle settings documentation.

## Authoritative Sources

- Databricks bundle settings reference: <https://docs.databricks.com/aws/en/dev-tools/bundles/settings>
- Databricks bundle CLI commands: <https://docs.databricks.com/aws/en/dev-tools/cli/bundle-commands>
- Local enterprise guardrails: `instructions/databricks-asset-bundle.instructions.md`

## Common Top-Level Sections

| Key | Required | Use It For |
|-----|----------|------------|
| `bundle` | Yes | Bundle identity, CLI version, deployment defaults |
| `include` | No | Split bundle files loaded into the root config |
| `workspace` | No | Host, root path, artifact path, and state path defaults |
| `variables` | No | Reusable parameters referenced throughout the bundle |
| `permissions` | No | Shared access rules applied to bundle resources |
| `artifacts` | No | Wheel, JAR, or other build outputs deployed with the bundle |
| `resources` | No | Jobs, pipelines, models, serving endpoints, dashboards, and similar deployable resources |
| `targets` | Yes | Per-environment overrides such as `dev` and `prod` |
| `run_as` | No | Execution identity for deployed resources |
| `sync` | No | Workspace include and exclude rules |

## Minimal Root Pattern

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

## Section Notes

### `bundle`

- Keep `bundle.name` stable. Changing it changes the deployed identity.
- Pin `databricks_cli_version` when the bundle depends on newer keys or behaviors.

### `include`

- Use it for split bundles only.
- Keep the root file authoritative for shared sections such as `permissions`, `variables`, `workspace`, `artifacts`, `sync`, and `targets`.

### `workspace`

- Put shared path defaults here.
- Put environment-specific hosts in `targets.<name>.workspace`.

### `variables`

- Use simple defaults for scalars.
- Use `type: complex` only when the value is a map or list used as a whole.

### `artifacts`

- Use `type: whl` for Python wheel builds.
- Keep artifact build commands reproducible and rooted at the project source of truth.

### `resources`

The most common resource families in this skill are:

- `jobs`
- `pipelines`
- `model_serving_endpoint`
- `registered_models`
- `experiments`
- `dashboards`
- `apps`
- `schemas`
- `volumes`

### `targets`

- Use exactly one default target.
- Keep environment-specific overrides under the target instead of hardcoding them in the root.
- Common target overrides are `workspace`, `variables`, `permissions`, `resources`, and `run_as`.

## Read Next

- Job or DLT resource details: [job-configuration.md](job-configuration.md)
- Dependencies, wheels, and version sync: [python-dependency-management.md](python-dependency-management.md)
- Split bundles and workspace sync: [resources-splitting.md](resources-splitting.md)
