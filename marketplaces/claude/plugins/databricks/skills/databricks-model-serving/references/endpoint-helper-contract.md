# Endpoint Helper Contract

Use this reference when deriving `model_handoff.signature`, interpreting `logical_type`, or deciding what a successful offline validator or endpoint doctor proves.

## Canonical JSON Projection

`model_handoff.signature` is a canonical JSON projection derived from an upstream native model signature.
Within rollout contract v2, the helpers prove that:

- signature inputs exactly equal `feature_schema.features` and `request_contract.input_schema`, including keys, order, `logical_type`, and nullability
- `input_example.columns` exactly equal the projected feature names in order
- signature outputs exactly equal `output_contract.response_schema`
- request fixtures use one request-contract v1 DataFrame shape with exact top-level fields or columns, row widths, nullability, and projected JSON categories
- bounded response values satisfy the declared output schema, label, score, predicate, threshold-action, fallback-rate, and row-cardinality assertions
- contract and fixture JSON stays within the documented byte, depth, node, field, row, and string limits

These are relationships among the JSON declarations and observed JSON values.
The helpers do not retrieve or authenticate an MLflow `MLmodel` artifact, inspect the loaded flavor or environment, or prove that the projection was derived correctly from the native signature.

## Current Logical Domains

The current helper uses these explicit JSON domains:

| `logical_type` | Accepted non-null JSON value |
|---|---|
| `boolean` | JSON boolean |
| `integer`, `long` | Any non-boolean JSON integer accepted by the host parser; both names currently have the same helper domain |
| `float`, `double`, `decimal` | Any non-boolean JSON integer or number that converts to a finite host float; all three names currently have the same helper domain |
| `string` | JSON string |
| `date` | Canonical real `YYYY-MM-DD` date |
| `timestamp` | The bounded RFC3339 form documented in the semantic-readiness reference |
| `binary` | Nonempty canonical padded standard Base64 within the decoded-byte cap |
| `array` | JSON array, represented as a host list |
| `map`, `struct` | JSON object, represented as a host mapping |

`array`, `map`, and `struct` are coarse top-level categories in this contract.
They do not carry element, member, tensor-shape, or recursively nested field declarations.
The helper also does not encode physical decimal precision and scale.

## Upstream Owner Verification

Before the serving owner relies on the projection, the model-training or promotion owner must:

1. Retrieve and authenticate the native signature artifact for the resolved model version or alias and MLflow run.
2. Record the artifact identity or digest, model flavor, MLflow version, serving runtime version, and the version-matched primary evidence used for interpretation.
3. Derive the JSON projection field by field, preserving native input and output order, names, and top-level category, then explicitly choose and validate the projection's null-value policy.
4. Validate every physical constraint omitted by the projection, including integer width, floating-point width and range, decimal precision and scale, tensor shape, and nested array or object structure.
5. Prove any required coercion is value-preserving for the actual flavor and runtime, using bounded boundary cases and without placing private row values in the rollout contract or report.

Every projected request-contract v1 column is required on the wire; `nullable` controls whether its supplied value may be null.
Native MLflow optionality is separate: a native field with `required=false` may be omitted, so do not copy `required=false` to `nullable=true` or claim that omission was preserved losslessly.
If omission semantics are required, the upstream owner must provide an explicit wrapper or materialization mapping and verify its missing-field behavior against the actual model and runtime.
Do not silently replace an omitted field with null or a default.

Concrete hazards include:

- A helper `integer` accepts a host JSON integer outside the native signed 32-bit range, and `long` accepts one outside the native signed 64-bit range. Fixed-width NumPy integer operations can overflow or wrap even though the JSON integer was accepted.
- A JSON number can be finite as a host double while conversion to native `float32` overflows. The helper currently gives `float` and `double` the same JSON domain.
- A helper `decimal` assertion proves only a finite JSON number. It does not prove the native decimal precision, scale, or exact decimal representation.
- A top-level JSON array or object can pass while its nested members, tensor dimensions, or native dtypes disagree with the retrieved signature.

If this evidence is absent or cannot be reconciled, native compatibility remains unverified and rollout is not complete.
Do not infer a native binding from a matching model name, MLflow run ID, HTTP 200 response, or successful doctor report.

## Interpreting A Successful Probe

A successful endpoint query proves that the endpoint accepted one bounded JSON request and returned JSON satisfying the declared assertions.
It does not prove that the runtime consumed the identical mathematical number, used the intended fixed-width dtype, preserved decimal representation, or loaded the native signature artifact that the projection was meant to describe.
Keep upstream artifact and runtime evidence alongside the promotion decision, then use the rollout contract and doctor for their narrower JSON and endpoint-state guarantees.

## Primary Sources

- [MLflow 2.22.0 model signatures](https://mlflow.org/docs/2.22.0/model/signatures) documents signature components, primitive and composite types, signature enforcement behavior, required versus optional input fields, and name-ordering enforcement. Use documentation or source matching the retrieved runtime when it differs.
- [Spark 4.0.2 data types](https://spark.apache.org/docs/4.0.2/sql-ref-datatypes.html) defines signed integer widths, single-precision `float`, double-precision `double`, decimal representation, and nested SQL types.
- [NumPy 2.1 numeric overflow guidance](https://numpy.org/doc/2.1/user/basics.types.html#overflow-errors) shows fixed-width integer overflow and points to dtype limit inspection.
- [NumPy 2.1 `iinfo`](https://numpy.org/doc/2.1/reference/generated/numpy.iinfo.html) and [`finfo`](https://numpy.org/doc/2.1/reference/generated/numpy.finfo.html) define machine limits for integer and floating-point dtypes.
