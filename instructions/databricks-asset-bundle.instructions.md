---
description: "Databricks Asset Bundle configuration guardrails for databricks.yml and bundle.yml"
applyTo: '**/databricks.yml,**/bundle.yml'
---

# Databricks Asset Bundle Guardrails

These rules apply automatically when editing `databricks.yml` or `bundle.yml`. For detailed configuration patterns, templates, and workflows, load the **databricks-asset-bundles** skill.

## CLI Profile Requirement

**All `databricks bundle` commands MUST include a `--profile` flag.**

```bash
# ✅ CORRECT
databricks bundle deploy --profile my-project-profile
databricks bundle validate --profile my-project-profile

# ❌ WRONG - risks deploying to wrong workspace
databricks bundle deploy
```

## Required Sections

Every `databricks.yml` must include: **bundle**, **permissions**, **variables**, **workspace**, **resources**, and **targets**.

## Mandatory Rules

- **Only `dev` and `prod` targets.** Never create staging, qa, or uat environments.
- **Prefer serverless compute** for non-GPU jobs. Always set `environment_version: "4"`.
- **Never use `%pip install` in notebooks.** Declare all dependencies in `databricks.yml`.
- **Pin exact versions** with `==` (e.g., `elasticsearch==8.17.2`).
- **Always run `databricks bundle validate --profile <profile>`** after any edit. For offline validation without CLI auth, use `python scripts/validate_bundle.py` from the **databricks-asset-bundles** skill.
- **Always define explicit permissions** with `CAN_MANAGE` for owners.
- **All resources must include required tags.** Check `variables.tags` or the project's AGENTS.md.
- **Expose a minimal operator parameter surface.** Decide the manual-run contract first (e.g., `end_date`, `lookback_hours`) and expose only those through `job.parameters`. Keep source/output table names, modes (including dry-run), and wiring as fixed internals of the bundle and code.
- **Prefer an explicit workspace owner variable over `${workspace.current_user.userName}`.** For reliable headless validate/deploy (CI, service principals), define `variables.user_name` and use `${var.user_name}` in paths and permissions. Avoid `${workspace.current_user.userName}` when validation must not depend on an interactive user context.

## Required Tags

| Tag | Description |
|-----|-------------|
| `Team` | Owning team name |
| `Project` | Project identifier |
| `Owner` | Primary contact email |
| `DataClassification` | Data sensitivity level |
| `Environment` | `${bundle.target}` |
| `ApplicationName` | `${bundle.name}` |
| `ResourceOwner` | Resource owner org |
| `CiscoMailAlias` | Mailing list for alerts |
| `DataTaxonomy` | Data taxonomy description |
| `IntendedPublic` | Whether data is intended for public use |

## Table Management

Tables should be created via SQL files, not Python code. Organize under `src/sql/bronze/`, `src/sql/silver/`, `src/sql/gold/`.