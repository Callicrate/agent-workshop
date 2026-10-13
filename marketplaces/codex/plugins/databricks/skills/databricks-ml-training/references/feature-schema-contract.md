# Frozen Feature Schema

Use this contract for every model family that consumes the training configuration.
Compare the ordered, selected runtime feature and target schema before fitting.
The offline helper validates supplied declarations; the caller must obtain the observed schema from the actual selected data.

## Format Selection

The legacy format-1 artifact has no `format` key.
Its `version` is the feature-set identifier, and valid scalar fingerprints remain unchanged.
It supports exact scalar types: `boolean`, `integer`, `long`, `float`, `double`, `string`, `date`, `timestamp`, and `binary`.
Format-1 `timestamp` means the supported default Spark `TimestampType`, with an independently confirmed precision contract.

Format 1 rejects `decimal`, `array`, `map`, and `struct`, because a coarse logical token cannot establish their physical shape.
Use format 2 for those supported types.
It adds `format: 2` and a canonical Spark `DataType.jsonValue()`-shaped `data_type` to every declaration.
The logical type is a checked root-family label; contradictory logical and physical tokens fail.

## Physical Declaration And Comparison

```json
{
  "format": 2,
  "version": "transactions-physical-v2",
  "fingerprint": "972997a0517a5f6b7d5a095f841a085f23dac9653287a53b96995ebbde101eb5",
  "features": [
    {
      "name": "amount",
      "logical_type": "decimal",
      "data_type": "decimal(12,2)",
      "nullable": false,
      "null_policy": "forbid"
    }
  ],
  "target": {
    "name": "label",
    "logical_type": "string",
    "data_type": "string",
    "nullable": false,
    "null_policy": "forbid"
  }
}
```

The example fingerprint is verified by `compute_feature_schema_fingerprint`.
For initial construction, that function accepts a closed declaration without `fingerprint` or with an ignored placeholder; it does not mutate the declaration.
Add the returned digest before final artifact or configuration validation, which requires the matching 64-character hexadecimal fingerprint.
Format-2 observed fields contain exactly `name`, `data_type`, and `nullable`; declarations additionally carry the logical root and null policy.

```python
from feature_schema_contract import validate_feature_schema_before_fit

validate_feature_schema_before_fit(
    artifact,
    observed_features=[
        {"name": "amount", "data_type": "decimal(12,2)", "nullable": False}
    ],
    observed_target={"name": "label", "data_type": "string", "nullable": False},
)
```

Obtain each physical type from the selected source's native type representation.
Do not flatten nested types, decimal parameters, or nested nullability into coarse logical labels.
The gate compares feature order, target, top-level nullability, physical types, nested field order and nullability, and the exact artifact fingerprint.

## Spark Full Field Adapter

For a format-2 artifact, when observed types come from PySpark, pass each complete root
`StructField.jsonValue()` object through
`observed_field_from_spark_json` before calling the pre-fit gate. It returns
the existing observed mapping with exactly `name`, `data_type`, and
`nullable`, while checking the complete root object first. The pre-fit gate
then applies its shared whole-schema budget to those mappings.

Manual observed mappings remain supported for callers that already own a trusted
root-field contract, but they cannot prove that root metadata was ordinary.
The adapter requires root `metadata: {}`. This matters because Spark runtimes
with collation support can synthesize nonempty root `__COLLATIONS` metadata
even where the root `dataType.jsonValue()` appears as an ordinary string,
array, or map. Such fields fail before metadata is discarded. Nested struct
metadata is also rejected by the existing physical-type validator.

Adapt the actual selected runtime fields once each, before fitting:

```python
from feature_schema_contract import (
    observed_field_from_spark_json,
    validate_feature_schema_before_fit,
)

# artifact is a validated format-2 feature schema artifact
selected_feature_field_json = [
    selected_training_df.schema[name].jsonValue()
    for name in feature_columns
]
selected_target_field_json = selected_training_df.schema[target_column].jsonValue()

validate_feature_schema_before_fit(
    artifact,
    observed_features=[
        observed_field_from_spark_json(field)
        for field in selected_feature_field_json
    ],
    observed_target=observed_field_from_spark_json(selected_target_field_json),
)
```

Manual format-2 triples remain a supported wire form only when the caller already owns
the root-field proof:

```python
trusted_observed_feature = {
    "name": "amount",
    "data_type": "decimal(12,2)",
    "nullable": False,
}
```

That manual mapping cannot establish that the original root metadata was empty.
The adapter positively guards the supported physical-type subset and empty root
metadata, rejecting unsupported collated-string tokens with the same bounded
physical-type error used for other unsupported type strings. It imports neither
PySpark nor a JVM.

Format 1 continues to use its existing logical-type observed mappings and
independently confirms its default scalar physical contract. Do not route
format-1 fields through this format-2 adapter. Nondefault scalar families or
metadata-bearing fields require an external physical-schema gate.

## Supported Boundary And Migration

Format 2 supports the primitive subset above, canonical `decimal(precision,scale)` with precision 1 through 38 and scale 0 through precision, plus `array`, `map`, and ordered `struct` JSON values.
Array `containsNull`, map `valueContainsNull`, struct-field nullability and order, and empty struct-field `metadata: {}` are part of the physical contract.
The helper bounds total type nodes, nesting depth and struct fields across the full artifact.

UDTs, custom types, nonempty metadata, `timestamp_ntz`, and unsupported precision representations are rejected by these offline helpers.
Types outside this subset require an explicit external physical-schema gate; adding `format: 2` does not make them supported.
A `"timestamp"` string alone cannot establish precision beyond the platform's default.
Use the exact timestamp path in [point-in-time-training.md](point-in-time-training.md) when higher precision matters.

To migrate the wire format, retain the feature-set `version` only when the feature contract itself is unchanged.
Add `format: 2` and `data_type` to every feature and target, recompute the fingerprint, validate the config, and rerun the pre-fit gate.
Version, review, retrain and re-evaluate actual feature-schema changes.
Do not silently coerce a format-1 artifact into format 2.

Use [feature_schema_contract.py](../scripts/feature_schema_contract.py) for the comparison and [training-config-schema.json](../assets/training-config-schema.json) for the closed configuration shape.
