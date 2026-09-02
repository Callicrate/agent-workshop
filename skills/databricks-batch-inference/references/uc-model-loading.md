# Unity Catalog Model Loading Patterns

Use this reference to resolve a requested Unity Catalog alias once and score one immutable registered-model version.

## Resolve Once, Then Freeze

Aliases are mutable deployment pointers. Record the requested alias separately, resolve it once, and load only `models:/<name>/<version>` for the rest of the run.

```python
import mlflow

client = mlflow.MlflowClient()
registered_model_name = "catalog.schema.model_name"
requested_alias = "Champion"

resolved = client.get_model_version_by_alias(registered_model_name, requested_alias)
resolved_version = str(resolved.version)
resolved_run_id = resolved.run_id
immutable_model_uri = f"models:/{registered_model_name}/{resolved_version}"

# Preflight the exact artifact that every worker will use.
loaded = mlflow.pyfunc.load_model(immutable_model_uri)
if loaded.metadata.run_id != resolved_run_id:
    raise RuntimeError("loaded model run ID differs from the resolved registry version")

# Re-read the concrete version, not the alias, and verify the registry identity.
verified = client.get_model_version(registered_model_name, resolved_version)
if verified.run_id != resolved_run_id or str(verified.version) != resolved_version:
    raise RuntimeError("resolved Unity Catalog model identity changed during preflight")

score_udf = mlflow.pyfunc.spark_udf(
    spark,
    immutable_model_uri,
    result_type="double",
)
```

Record these as distinct fields:

- `requested_model_alias`: the mutable name requested by the job
- `resolved_model_uri`: the immutable `models:/name/version` URI actually loaded
- `model_version`: the concrete registered version
- `model_run_id`: the run ID returned for that version and verified on the loaded model

Never pass `models:/name@alias` to `load_model`, `spark_udf`, `FeatureEngineeringClient.score_batch`, or a retry after resolution. Otherwise executors or retries can score different versions under one run ID.

## Direct Version Request

If the caller supplied a concrete version, normalize it to a non-empty decimal string, fetch that version from the registry, and perform the same run-ID verification. Set `requested_model_alias` to NULL; do not fabricate an alias.

## Signature And Output Preflight

Before the full source read:

1. inspect the immutable model's signature
2. validate exact input names, order, types, and required/nullability semantics
3. validate the output shape and declared Spark result type
4. run a limited-row invocation using the same immutable URI and input construction as the full run
5. capture the signature digest in the scoring-run audit

Use [model-signature-contract.md](model-signature-contract.md) for the exact checks.

## Permissions And Read-Only Inspection

Preflight the execution identity without granting ownership or mutation rights:

| Object | Required privileges |
|---|---|
| Source table | `USE CATALOG`, `USE SCHEMA`, `SELECT` |
| Existing prediction target for insert-only `MERGE` | `USE CATALOG`, `USE SCHEMA`, `SELECT`, `MODIFY` |
| Registered model version or alias resolution | `USE CATALOG`, `USE SCHEMA`, `EXECUTE` |
| Every packaged feature table or view | `USE CATALOG`, `USE SCHEMA`, `SELECT` |
| Every first-class Feature or Feature View entity | `USE CATALOG`, `USE SCHEMA`, `READ FEATURE` |
| Every packaged on-demand feature function | `USE CATALOG`, `USE SCHEMA`, `EXECUTE` |
| Lineage system tables, when queried | access to the enabled system schema plus the object visibility required by the workspace policy |

Before `FeatureEngineeringClient.score_batch`, read the immutable model's packaged feature specification and lineage, enumerate every table/view, first-class Feature entity, and on-demand function, and persist that ordered inventory with its snapshot pins in the run contract. Preflight the execution principal against each dependency and both parents. A model-level `EXECUTE` grant does not imply feature-data access.

Inspect grants with `SHOW GRANTS ON <object>` or Catalog Explorer using a read-only operator path. Do not add grants as part of a scoring notebook. Feature entities use `READ FEATURE`; backing tables and views use `SELECT`. Both require `USE CATALOG` and `USE SCHEMA` on their parents.

After a successful test run, verify source and target table lineage in Catalog Explorer or `system.access.table_lineage`. Lineage system tables are a subset of observable read/write events, so absence is a review finding, not proof that no access occurred. Preserve the scoring-run metadata as the primary reproducibility record.

Official contracts:

- [Unity Catalog model aliases and version loading](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/)
- [Unity Catalog table privileges](https://docs.databricks.com/aws/en/tables/tables-concepts)
- [Unity Catalog `READ FEATURE` and object privileges](https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/privileges-reference)
- [Feature View batch scoring](https://docs.databricks.com/aws/en/machine-learning/feature-store/feature-views-api-reference)
- [Unity Catalog lineage](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage)
