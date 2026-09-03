# Python Dependencies And Wheel Artifacts

Never use `%pip install`, `%pip install -r requirements.txt`, or notebook-scoped package setup in a bundle-managed workflow.
Declare dependencies in `databricks.yml` so they are version-controlled and deployable.

## Pinning Rule

- Pin exact versions with `==`.
- Do not use `>=`, `~=`, or unpinned package names in bundle-managed dependencies.

```yaml
- pypi:
    package: "elasticsearch==8.17.2"
- pypi:
    package: "tenacity==9.1.2"
```

## Classic Clusters: `libraries`

Use `libraries` on tasks that run on classic compute.

### Shared variable for reuse

```yaml
variables:
  libraries:
    type: complex
    default:
      - pypi:
          package: "elasticsearch==8.17.2"
      - pypi:
          package: "tenacity==9.1.2"

resources:
  jobs:
    etl_job:
      tasks:
        - task_key: extract
          job_cluster_key: main_cluster
          libraries: ${var.libraries}
```

### Inline for one task

```yaml
tasks:
  - task_key: train
    new_cluster:
      spark_version: "17.3.x-gpu-ml-scala2.12"
      node_type_id: "g5.xlarge"
      num_workers: 0
    libraries:
      - pypi:
          package: "mlflow==2.21.3"
      - pypi:
          package: "scikit-learn==1.6.1"
```

## Serverless: `environments[].spec.dependencies`

Use a flat dependency list for serverless environments. Resolve `environment_version` from the existing bundle or current workspace-supported versions, preferably via a project variable.

```yaml
environments:
  - environment_key: default
    spec:
      client: "1"
      environment_version: "${var.serverless_environment_version}"
      dependencies:
        - pandas==2.2.3
        - pydantic==2.11.3
        - tenacity==9.1.2
```

Serverless `dependencies` do not use the `pypi: package:` mapping syntax.

## Wheel Artifacts

Use bundle artifacts when project code needs to ship as a wheel.

```yaml
artifacts:
  package_wheel:
    type: whl
    path: "."
    build: "python -m build --wheel"
```

Reference the built wheel from classic or serverless compute:

```yaml
# Classic cluster task
libraries:
  - whl: "${workspace.root_path}/artifacts/package-${var.package_version}-py3-none-any.whl"

# Serverless environment
dependencies:
  - ${workspace.root_path}/artifacts/package-${var.package_version}-py3-none-any.whl
```

## Wheel Version Sync

If the wheel filename includes a version variable, keep the bundle variable synchronized with the package build metadata.
Otherwise the bundle can keep deploying an old wheel path even after the source changed.

```yaml
variables:
  package_version:
    default: "1.2.0"
```

```toml
[project]
version = "1.2.0"
```

## Migrating Existing Notebooks

If an existing notebook still uses `%pip`:

1. Identify every package it installs.
2. Move each package into classic `libraries` or serverless `dependencies`.
3. Remove the `%pip` lines from the notebook.
4. Validate the bundle before deploying.
