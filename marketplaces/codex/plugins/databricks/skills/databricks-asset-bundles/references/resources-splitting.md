# Split Bundles And Workspace Sync

Workflow for breaking a monolithic `databricks.yml` into modular files using the `include` directive.

## When to Split

Split when any of these apply:

- `databricks.yml` exceeds ~150 lines
- The bundle defines 3+ jobs or a mix of resource types (jobs, pipelines, experiments)
- Multiple team members edit different resources in the same file
- You want per-resource diffs in version control

Do **not** split bundles with a single job and no pipelines — the overhead isn't worth it.

## Target Directory Layout

```
project/
├── databricks.yml              # Core config: bundle, permissions, variables, workspace, targets
├── resources/
│   ├── daily_etl_job.yml       # One file per job
│   ├── ml_training_job.yml
│   ├── feature_pipeline.yml    # DLT pipelines
│   └── experiment.yml          # ML experiments
```

## What Stays in databricks.yml

Keep these sections in the root `databricks.yml`:

| Section | Why |
|---------|-----|
| `bundle` | Identity — must be in root |
| `include` | Loads the split files |
| `permissions` | Global access control |
| `variables` | Shared across all resources |
| `workspace` | Workspace path defaults |
| `artifacts` | Build definitions |
| `sync` | File sync rules |
| `targets` | Environment overrides |
| `run_as` | Execution identity |

## What Moves to resources/

Each resource gets its own file containing **only** the `resources:` key with that resource's definition:

```yaml
# resources/daily_etl_job.yml
resources:
  jobs:
    daily_etl:
      name: "${var.job_basename} - Daily ETL (${bundle.target})"
      tags: ${var.tags}
      # ... full job definition
```

### Naming Convention

Use the resource key as the filename, in kebab-case:

| Resource Key | Filename |
|-------------|----------|
| `daily_etl` | `daily_etl_job.yml` |
| `ml_training` | `ml_training_job.yml` |
| `feature_pipeline` | `feature_pipeline.yml` |

Suffix `_job` for jobs to distinguish from pipelines when names are ambiguous.

## Step-by-Step Workflow

### 1. Add the include directive

Add `include` to the root `databricks.yml`, immediately after the `bundle` section:

```yaml
bundle:
  name: my-project
  databricks_cli_version: ">=0.218.0"

include:
  - "resources/*.yml"
```

### 2. Create the resources directory

```bash
mkdir resources
```

### 3. Extract each resource into its own file

For each job, pipeline, or other resource under `resources:` in `databricks.yml`:

1. Create a new file in `resources/` named after the resource key
2. Copy the resource definition, **keeping the full `resources:` → `jobs:` (or `pipelines:`, etc.) nesting**
3. Remove the resource from `databricks.yml`

Example — extracting `daily_etl` from a monolith:

**Before** (in `databricks.yml`):
```yaml
resources:
  jobs:
    daily_etl:
      name: "..."
      # 50 lines of config
    ml_training:
      name: "..."
      # 40 lines of config
```

**After** — `resources/daily_etl_job.yml`:
```yaml
resources:
  jobs:
    daily_etl:
      name: "..."
      # 50 lines of config
```

**After** — `resources/ml_training_job.yml`:
```yaml
resources:
  jobs:
    ml_training:
      name: "..."
      # 40 lines of config
```

**After** — `databricks.yml` has no `resources:` section at all (or only non-extracted resources).

### 4. Validate

```bash
databricks bundle validate --profile <profile> --target <target>
# Or the documented project-specific auth/target shape after profile and target are proven.
```

Fix any errors before proceeding. Common issues:

| Error | Cause | Fix |
|-------|-------|-----|
| `duplicate key: daily_etl` | Resource still in both root and include file | Remove from `databricks.yml` |
| `unknown variable ${var.tags}` | Variable not in root config | Ensure `variables` stays in `databricks.yml` |
| `file not found: resources/*.yml` | Empty directory or wrong glob | Verify files exist and glob matches |

### 5. Verify deployment equivalence

Deploy to dev and confirm all resources appear:

```bash
databricks bundle deploy --profile <profile> --target <target>
# Or the documented project-specific auth/target shape after profile and target are proven.
```

## Rules

- **Variables stay in the root file.** Resource files reference variables with `${var.X}` — those resolve at deploy time from the root config.
- **Targets stay in the root file.** Target overrides apply to all included resources automatically.
- **Each include file must be a valid YAML fragment.** It must start with a top-level key that DAB recognizes (`resources`, `variables`, `targets`, etc.). Only root `databricks.yml` owns `include`; an `include` key in a fragment is ignored by the static validators with a warning.
- **Resource keys must be globally unique.** Two include files cannot define the same job key — DAB will error.
- **The glob pattern matters.** Keep include files flat and use `resources/*.yml`. The static validators intentionally reject recursive `**` patterns to bound traversal cost; split nested layouts into explicit flat include directories instead.
- **Include order is deterministic.** Files are loaded in lexicographic order. This matters only if multiple files define the same top-level section (e.g., both add to `variables`), which should be avoided.

## Splitting Target Overrides (Advanced)

For very large bundles, targets can also be split:

```yaml
include:
  - "resources/*.yml"
  - "targets/*.yml"
```

With `targets/dev.yml` and `targets/prod.yml` containing environment-specific overrides. Only do this when target configs are large enough to justify the split (50+ lines each).

## Workspace Sync Rules

Use `sync` when the bundle should deploy only a subset of the repo into the Databricks workspace.
Keep `sync` in the root file even when resources are split across includes.

```yaml
sync:
  include:
    - "src/**/*.py"
    - "notebooks/**/*.ipynb"
    - "sql/**/*.sql"
  exclude:
    - "**/__pycache__"
    - "**/.pytest_cache"
    - "databricks.yml"
    - ".gitignore"
    - "README.md"
    - "tests/**"
    - "docs/**"
```

- Keep `include` narrow. Do not sync the whole repo when only `src/`, `notebooks/`, and `sql/` are needed.
- Notebook include globs should target `notebooks/**/*.ipynb`; `.py` belongs under script or module paths such as `src/**/*.py`.
- Keep local-only files such as tests, docs, and caches in `exclude`.
- Treat `sync` as deployment scope, not as a substitute for job task paths or artifact builds.

## Static Validator Path Boundary

`scripts/validate_bundle.py` and the optional `scripts/dab_doctor.mjs` classify every task and pipeline library path before checking the filesystem.

- Dynamic substitutions and supported Databricks workspace or cloud paths are not probed locally.
- Drive-letter, UNC, reserved Windows device (`CON`, `NUL`, `COM1`, and similar), alternate-data-stream, device, `file:` URI, and other host-specific paths are rejected before any filesystem check.
- A local file is resolved relative to the configuration file that declares it, then must be inside the canonical bundle root or a canonical directory explicitly declared by `sync.paths`.
- `../shared/file.py` is valid when `../shared` is a declared `sync.paths` directory. A `../` traversal with no declared containing source is rejected before existence checks.
- Root resources are checked in the root context and again for each target's effective resource overlay. A target's `sync.paths` augments only that target, never another target's local-file authority.
- The validator follows symlinks and Windows reparse points before final containment. A reparse point inside the bundle that escapes an allowed root is rejected. A `sync.paths` directory that is itself a symlink is allowed, because its resolved destination is the explicitly declared source root.
- Pipeline `notebook.path`, `file.path`, and `glob.include` use the same policy. Globs are not expanded by the static check, but their non-wildcard prefix must be contained in the selected local roots; parent-directory components are rejected anywhere in a local glob pattern.

Keep configuration includes inside the bundle tree. The static validators apply only the root `databricks.yml` `include` list, load direct files and bounded flat globs such as `resources/*.yml` in deterministic order, deduplicate repeated matches, and reject dynamic, remote, host-specific, escaping, recursive, oversized, or YAML-alias-based include graphs. Nested fragment include directives are ignored with a warning. Use `sync.paths` for shared code, not parent-directory YAML includes.
