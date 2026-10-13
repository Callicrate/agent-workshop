# Spark Schema Write Contract

Use this contract before an append, insert, merge source projection, or any change that might alter a Delta target schema.
It validates a source `StructType.jsonValue()` against the target schema before the write.
It does not validate row values, timestamp parsing, SCD2 ranges, or temporal windows.
Keep those checks in the ETL transformation and the validation queries in [core ETL patterns](core-etl-patterns.md).

## Run The Gate

From the `databricks-spark-etl` skill directory, run:

```powershell
python -B scripts/validate_schema.py compare --source-schema source-schema.json --target-schema target-schema.json --resolution by-name --json
```

For a Delta SQL `INSERT ... BY NAME`, select the operation explicitly:

```powershell
python -B scripts/validate_schema.py compare --source-schema source-schema.json --target-schema target-schema.json --write-operation delta-sql-insert-by-name --json
```

Reports for this operation use schema-contract version `3` and record `write_operation`.
The default `generic` operation preserves the existing version `2` report and recursive behavior of the selected resolution mode; it must not be used as evidence for Delta SQL's mixed top-level and nested resolution contract.

The command accepts only the canonical JSON emitted by `StructType.jsonValue()`.
It reads at most 1,048,577 bytes per input, validates a maximum nesting depth of 32 and 10,000 fields, then exits before comparison if either complete schema is malformed.
Invalid JSON or schema input exits `2` without a traceback.

The output always carries one decision:

| Decision | Meaning | Write action |
|---|---|---|
| `exact` | Names, types, and nullability match under the selected resolution contract. | Write only after the normal data and SCD2 checks. |
| `compatible` | The selected policy permits the non-exact mapping. | Keep the selected write mode and any explicit projection visible. |
| `conditional` | Delta widening is potentially valid, but table capability and write-operation conditions still need live verification. | Do not treat as pass. Verify the conditions and rerun or make the DDL change first. |
| `incompatible` | Mapping would lose a required field, nullability guarantee, position, or supported type contract. | Project, cast, or change the table contract explicitly. |
| `invalid` | Input schema or validator options are malformed or bounded limits were exceeded. | Fix the input. Do not compare partially. |

`conditional`, `incompatible`, and `invalid` exit nonzero.
The report includes `casts` and `warnings` so an `exact` default does not silently become an implicit coercion policy.

## Choose Resolution Before Writing

Use generic **by-name** when the writer resolves names recursively.
It matches field names case-insensitively and permits source reordering, including recursively nested structs.
It warns for case-only matches, source-only fields, and omitted nullable target fields because those require an explicit projection.

Use **by-position** only when the writer is known to resolve by ordinal.
It requires the same field count, exact field order, exact names, compatible types, and source-to-target nullability at every struct level.
It catches a swap even when the two swapped fields have the same type.

Use `--write-operation delta-sql-insert-by-name` for `INSERT ... BY NAME` into a Delta table.
This operation uses by-name resolution only for top-level query columns.
It rejects a source-only top-level column because every exposed query column must exist in the target; omitted target columns still follow Databricks default and nullability rules.
For an omitted target, the gate preserves only Spark's `CURRENT_DEFAULT` and `EXISTS_DEFAULT` `StructField.metadata` markers.
It immediately reduces either marker to bounded presence state and never retains, evaluates, or renders the default expression.
`CURRENT_DEFAULT` proves the active insert default; `EXISTS_DEFAULT` alone describes historical backfill and does not.
Only a nonempty, non-whitespace string value establishes `CURRENT_DEFAULT`; false, null, empty, whitespace-only, and other non-string values do not authorize omission.
An omitted target with `CURRENT_DEFAULT` is `compatible` but non-exact and reports `target_default_column_omitted`; an omitted `NOT NULL` target without it remains incompatible.
Every nested struct is positional, including a struct used as an array element, so the source must project the same nested fields in target order.
The validator reports order failures at exact target paths such as `profile.email` and `events[].event_id`.
Do not substitute generic `--resolution by-name`: its recursive name matching models other writers and non-Delta tables, not this Delta SQL operation.

Prefer a name-resolving write:

```python
(
    source_df.select("id", "event_time", "payload")
    .writeTo("catalog_name.schema_name.target_table")
    .append()
)
```

For SQL, use an explicit target column list or `BY NAME` when the active Spark version supports it:

```sql
INSERT INTO catalog_name.schema_name.target_table (id, event_time, payload)
SELECT
    id,
    event_time,
    payload
FROM source_view;
```

```sql
INSERT INTO catalog_name.schema_name.target_table BY NAME
SELECT
    payload,
    id,
    event_time
FROM source_view;
```

Do not use a bare `INSERT INTO ... SELECT`, `INSERT INTO ... VALUES`, or `DataFrameWriter.insertInto(...)` unless the schema gate ran in `by-position` mode for the exact write projection.
Spark documents that `insertInto` ignores column names and resolves by position.
Databricks documents that Delta `INSERT ... BY NAME` reorders top-level fields by name but resolves nested struct fields by position.

## Canonical Schema Requirements

The root must be exactly:

```json
{
  "type": "struct",
  "fields": [
    {
      "name": "id",
      "type": "long",
      "nullable": false,
      "metadata": {}
    }
  ]
}
```

Every field needs a string `name`, a type, and a JSON boolean `nullable`.
Names are bounded, must not contain control characters, and must be unique case-insensitively in every struct, including structs nested in arrays or maps.
The validator accepts canonical primitive names, `decimal(p,s)`, and the following complete object forms:

```json
{"type": "array", "elementType": "string", "containsNull": false}
```

```json
{"type": "map", "keyType": "string", "valueType": "long", "valueContainsNull": false}
```

```json
{"type": "struct", "fields": []}
```

Decimal precision must be 1 through 38 and scale must be from 0 through precision.
Array `containsNull` and map `valueContainsNull` are required because Spark treats those as type-level nullability contracts.
Map keys have no nullable flag because Spark forbids null map keys.

## Nullability Is Source To Target

The validator fails when the source permits nulls and the target forbids them:

- `StructField.nullable`: `source=True`, `target=False`
- `ArrayType.containsNull`: `source=True`, `target=False`
- `MapType.valueContainsNull`: `source=True`, `target=False`

Reports use full paths such as `profile.email`, `tags[]`, and `attributes{value}`.
The reverse direction is safe for a schema contract, but the result is `compatible`, not `exact`, and carries a directional relaxation warning.
It still does not replace data-quality checks for a non-null target.

## Type Policies

`exact` is the default.
It accepts only identical scalar types and recursively compatible complex types.
When a top-level scalar can be repaired, it reports a `cast_to_target_type` plan but still returns `incompatible` until the code casts deliberately.

Use `spark-assignment` only when the write contract intentionally relies on Spark ANSI assignment promotion:

```powershell
python -B scripts/validate_schema.py compare --source-schema source-schema.json --target-schema target-schema.json --type-policy spark-assignment --json
```

The implemented source-to-target rules are the official Spark ANSI precedence direction.
They include `long` to `float` or `double`, decimal to `float` or `double`, and `date` to `timestamp_ntz` or `timestamp` only under this named policy.
They never include `float` or `double` to decimal.
Decimal assignment must preserve both scale and integer-digit capacity.
String and epoch integer values never satisfy a timestamp schema contract.
Parse or convert them in the transformation before writing.

Use `delta-type-widening-v4` only for a Delta Lake 4.0 target that is deliberately allowed to evolve:

```powershell
python -B scripts/validate_schema.py compare --source-schema source-schema.json --target-schema target-schema.json --type-policy delta-type-widening-v4 --delta-type-widening-enabled --delta-table-feature-enabled --automatic-schema-evolution-enabled --json
```

This policy evaluates normal Spark source-to-target assignments first.
For the opposite direction, target-to-source Delta widening, it implements only Delta Lake 4.0's documented matrix: integral widths, integer to decimal or double, `long` to decimal, `float` to double, decimal precision and scale widening, and `date` to `timestamp_ntz`.
It returns `conditional` even with all flags supplied.
The live table must carry `delta.enableTypeWidening=true` and the `typeWidening` feature, and the write must use operation-level schema evolution.
Integer to decimal or double widening is manual-only under Delta's automatic-evolution rules, so declare `--manual-type-change` only after the corresponding `ALTER COLUMN` operation is a separate approved change.

For decimals, Delta widening requires `decimal(p,s)` to become `decimal(p+k1,s+k2)` where `k1 >= k2 >= 0`.
For an integral target widened to decimal, the documented minimum precision applies before the scale: the incoming decimal precision must be at least `minimum_precision + scale`.
For example, a `long` requires at least `decimal(20,0)`, so `decimal(20,1)` is rejected and `decimal(21,1)` is the first scale-one candidate.

## Live Tables And Safe Errors

`compare_live_schema(spark, df, target_table)` uses a narrow structured `TABLE_OR_VIEW_NOT_FOUND` check only for `table_not_found`.
Permissions, catalog outages, malformed remote schemas, and other errors return `target_schema_unavailable` with a bounded class and redacted message.
No raw authorization value, Py4J detail, control character, or absolute input path is emitted.

The same boundary applies to `df.schema` and `jsonValue()` failures: they return `source_schema_unavailable` with a bounded class and redacted message.
`suggest_casts(...)` raises `SourceSchemaUnavailable` instead of returning an empty result when the source schema cannot be read.

`suggest_casts(...)` raises `TargetTableNotFound` or `TargetSchemaUnavailable` for a live target failure.
It never returns `{}` as if an unavailable target were a schema with no casts.

With `--json`, malformed command arguments return one stable, value-free `invalid_cli_arguments` envelope and exit `2`.
Human-mode parser errors retain normal command usage for interactive diagnosis.

## Primary Sources

- [Spark SQL data types](https://spark.apache.org/docs/latest/sql-ref-datatypes.html) defines `StructField.nullable`, array `containsNull`, map `valueContainsNull`, non-null map keys, and unique struct field names.
- [PySpark StructType API](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.types.StructType.html) shows recursive nullability across structs, arrays, and maps.
- [Spark StructField API](https://spark.apache.org/docs/latest/api/java/org/apache/spark/sql/types/StructField.html) defines current and existence default values carried by `StructField` metadata.
- [Spark CatalogV2Util source](https://github.com/apache/spark/blob/master/sql/catalyst/src/main/scala/org/apache/spark/sql/connector/catalog/CatalogV2Util.scala) defines the `CURRENT_DEFAULT` and `EXISTS_DEFAULT` metadata keys and uses the current marker to identify an active default column.
- [Spark ANSI type promotion](https://spark.apache.org/docs/3.5.8/sql-ref-ansi-compliance.html) defines the named assignment-promotion direction, including `date -> timestamp_ntz -> timestamp`.
- [Databricks INSERT](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-dml-insert-into) documents top-level `BY NAME` matching and positional nested structs for Delta tables, including arrays of structs.
- [Spark INSERT TABLE](https://spark.apache.org/docs/4.0.1/sql-ref-syntax-dml-insert-table.html) documents the generic SQL syntax and explicit column lists.
- [PySpark `insertInto`](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.insertInto.html) documents positional resolution.
- [Delta Lake type widening](https://docs.delta.io/delta-type-widening/) defines the Delta 4.0 widening matrix, feature requirement, automatic-evolution conditions, decimal rule, and manual-only integral-to-decimal or double changes.
