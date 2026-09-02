---
name: databricks-asset-bundles
description: "Use when editing databricks.yml, bundle jobs, targets, schedules, or permissions; guides DAB config. Do not trigger for Spark ETL, ML training, serving logic, installs, or deploy monitoring."
metadata:
  short-description: Edit Databricks Asset Bundles.
---

# Databricks Asset Bundles


## When to Use

- Creating or editing `databricks.yml` or bundle configuration
- Configuring jobs, pipelines, variables, permissions, artifacts, workspace sync, or targets
- Splitting a monolithic bundle into modular resource files
- Validating bundle structure before deploy
- Changing job topology, target scoping, schedules, continuous jobs, one-off repair jobs, or backfill resources
- Selecting GPU job cluster topology or verifying live job settings after deploy

## When NOT to Use

- Writing Spark ETL, ML training, or model-serving logic
- Installing Python packages inside notebooks with `%pip`
- Managing the same serving endpoint both declaratively in DAB and imperatively in SDK code

## Workflow

1. Start from [assets/databricks-minimal.yml](assets/databricks-minimal.yml), [assets/databricks-full.yml](assets/databricks-full.yml), or [assets/databricks-split.yml](assets/databricks-split.yml) when you need a new bundle shape instead of drafting from scratch.
2. Read [references/dab-quick-reference.md](references/dab-quick-reference.md) first for every bundle edit. It defines the default contract this skill expects.
3. If validation or deployment is in scope and the profile is not already proven, use [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) before running Databricks CLI commands.
4. Then load only the focused reference the task needs: [references/job-configuration.md](references/job-configuration.md) for jobs and DLT pipelines, [references/job-topology-changes.md](references/job-topology-changes.md) for resource graph edits, [references/gpu-cluster-patterns.md](references/gpu-cluster-patterns.md) for GPU compute, [references/python-dependency-management.md](references/python-dependency-management.md) for Python dependencies and wheel artifacts, [references/resources-splitting.md](references/resources-splitting.md) for include-based bundles and workspace sync, [references/multi-job-patterns.md](references/multi-job-patterns.md) for larger multi-job layouts, [references/ci-cd-patterns.md](references/ci-cd-patterns.md) for headless validation or deployment, or [references/dab-config-schema.md](references/dab-config-schema.md) when you need uncommon top-level keys.
5. Keep the contract explicit: use the project's existing target topology first; if no convention exists, default to `dev` and `prod`. Prefer serverless for non-GPU work only when task APIs, libraries, runtime support, workspace policy, and cost fit. Pin serverless environment versions from the existing bundle or current workspace-supported environment, not memory. Declare dependencies in the bundle rather than notebooks.
6. For notebook or code edits in a bundle-backed project, carry the runtime, bundle, and deployment contract with the edit. Notebook artifacts use `.ipynb`; use `.py` only for Python script or module entrypoints. Do not treat notebook changes as local-only text changes.
7. Finish with the branch that matches the user request:
  - Config-only change: run [scripts/validate_bundle.py](scripts/validate_bundle.py) as the authoritative static gate, then `databricks bundle validate` with the proven profile/target command shape for this bundle.
  - Deployment change: classify it with [references/operation-classes.md](references/operation-classes.md), deploy only when live workspace updates are in scope, then verify live job settings for every touched job.
  - Deploy-run-monitor request: hand off to `databricks-deploy-monitor` after bundle deployment and keep the loop until success, terminal failure with diagnosis, or user-requested stop.


## Live Operation Gates

- Local validation and schema checks can proceed after cwd, bundle root, target, and auth context are proven.
- Workspace definition updates such as deploys require expected diff/resource identity plus target/profile/principal proof.
- Run triggers, repairs, cancels, production activation, schedule unpauses, traffic shifts, alias moves, and destructive operations require explicit user intent or a task contract that already grants that action.
- Do not silently fall back to `DEFAULT`; if profile is omitted because bundle auth owns it, record the resolved host and principal.

## Deterministic Tools

| Resource | Use When | Outcome |
|----------|----------|---------|
| [scripts/validate_bundle.py](scripts/validate_bundle.py) | You need a static structural check before CLI validation | Deterministic bundle linting |
| [scripts/dab_doctor.mjs](scripts/dab_doctor.mjs) | Bun is already available and you want the optional JSON-friendly companion check | Secondary static doctor using the same runtime policy |
| [references/supported-runtimes.yml](references/supported-runtimes.yml) | Runtime or template dependency versions need freshness review | Single maintenance point for version assumptions |
| [assets/databricks-minimal.yml](assets/databricks-minimal.yml) | You need a minimal bundle starter | Small bundle template |
| [assets/databricks-full.yml](assets/databricks-full.yml) | You need a fuller all-in-one bundle starter | Comprehensive bundle template |
| [assets/databricks-split.yml](assets/databricks-split.yml) | You are splitting resources into include files | Modular root template |
| [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) | Validation or deployment targets a live workspace | Correct profile, host, and target before CLI work |
| [references/operation-classes.md](references/operation-classes.md) | A bundle edit may deploy, trigger a run, unpause production, or delete/reset resources | Live-operation gate before execution |
| [references/job-topology-changes.md](references/job-topology-changes.md) | You are removing, moving, splitting, or parallelizing jobs and tasks | Topology checklist and one-off repair job rules |
| [references/gpu-cluster-patterns.md](references/gpu-cluster-patterns.md) | A job uses GPU classic compute or training clusters | Worker, node type, Spark table access, and DDP alignment rules |

For `dab_doctor.mjs`, run `bun install` from `skills/databricks-asset-bundles/scripts/` first. Skip the doctor when Bun is unavailable; `validate_bundle.py` remains the authoritative static validator.

## References

- [references/dab-quick-reference.md](references/dab-quick-reference.md) - everyday bundle editing rules
- [references/job-configuration.md](references/job-configuration.md) - task types, scheduling, compute wiring, live settings, and DLT resources
- [references/job-topology-changes.md](references/job-topology-changes.md) - resource graph edits, target moves, one-off repairs, and parallelism
- [references/gpu-cluster-patterns.md](references/gpu-cluster-patterns.md) - GPU cluster topology and Spark table access rules
- [references/python-dependency-management.md](references/python-dependency-management.md) - classic versus serverless dependencies, wheel artifacts, and version sync
- [references/resources-splitting.md](references/resources-splitting.md) - include-based split bundles and workspace sync
- [references/multi-job-patterns.md](references/multi-job-patterns.md) - larger bundle layout patterns
- [references/dab-config-schema.md](references/dab-config-schema.md) - uncommon top-level sections and override surface
- [references/profile-workspace-preflight.md](references/profile-workspace-preflight.md) - profile, workspace, target, and command-contract checks for validation/deployment
- [references/operation-classes.md](references/operation-classes.md) - operation classes and approval gates for bundle deploy/run/production/destructive actions
- [references/ci-cd-patterns.md](references/ci-cd-patterns.md) - service-principal auth, profile use, and production run identity
- [references/supported-runtimes.yml](references/supported-runtimes.yml) - last-verified runtime and dependency assumptions used by validators
