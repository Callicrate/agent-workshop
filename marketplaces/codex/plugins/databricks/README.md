# Databricks Plugin

This plugin packages the canonical AGENTS workflows for Databricks, Spark, Delta Lake, MLflow, model training and serving, project health reviews, batch inference, deployment, and runtime diagnosis.

## Skills

- `databricks-api-calls`
- `databricks-asset-bundles`
- `databricks-batch-inference`
- `databricks-deploy-monitor`
- `databricks-ml-training`
- `databricks-model-serving`
- `databricks-project-status`
- `databricks-runtime-doctor`
- `databricks-spark-etl`
- `spark-diagnostics`
- `mlflow-run-auditor`

## Ownership

Edit these skills only in the sibling AGENTS repository.
This directory is a generated deployable projection, and `source-lock.json` records its committed source revision.

## Synchronize

Run from the repository root:

```powershell
.\scripts\Sync-Plugins.ps1 -Plugin databricks
.\scripts\Sync-Plugins.ps1 -Plugin databricks -Check
```

## Install

```powershell
codex plugin add databricks@callicrate
```

Start a new Codex thread after installation or refresh.
