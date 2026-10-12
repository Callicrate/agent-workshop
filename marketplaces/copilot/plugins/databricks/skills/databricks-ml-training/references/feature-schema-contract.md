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
