# Model Signature Contract

Use this reference before constructing a Spark UDF or passing an inference DataFrame to a model.

## Column-Based Inputs

Read the signature from the immutable model URI. Reject a missing input signature for governed scheduled scoring. Build a feature-only DataFrame before adding keys or trace columns. For every column specification, compare all four properties against that DataFrame:

1. exact name
2. exact position
3. compatible MLflow-to-Spark type
4. required/nullability semantics (`required=True` must be present and non-null in the scoring population)

Reject missing required, extra, reordered, duplicated, or implicitly cast features. Do not sort feature names or infer order from a set. Include an optional field only when it is present, but retain its signature-relative position and declared type. An absent optional field is valid and must not be synthesized as NULL.

For an MLflow Spark UDF, pass one named struct in signature order so MLflow receives names rather than ordinal columns:

```python
from pyspark.sql import functions as F
from mlflow.models import get_model_info

model_info = get_model_info(immutable_model_uri)
input_specs = list(model_info.signature.inputs.inputs)
signature_names = [spec.name for spec in input_specs]
actual_feature_names = feature_input_df.columns

if len(signature_names) != len(set(signature_names)):
    raise RuntimeError("model signature has duplicate input names")
missing_required = [
    spec.name
    for spec in input_specs
    if spec.required and spec.name not in actual_feature_names
]
extra_features = [name for name in actual_feature_names if name not in signature_names]
if missing_required or extra_features:
    raise RuntimeError("feature columns differ from the model signature")

present_input_specs = [
    spec
    for spec in input_specs
    if spec.required or spec.name in actual_feature_names
]
ordered_feature_names = [spec.name for spec in present_input_specs]
if actual_feature_names != ordered_feature_names:
    raise RuntimeError("feature columns are not in signature order")

model_input = F.struct(
    *[F.col(name).alias(name) for name in ordered_feature_names]
)
scored_df = feature_input_df.withColumn("raw_score", score_udf(model_input))
```

After the name/order checks, compare every present field's Spark type with its MLflow type and inspect actual values for required-field NULLs. Spark schema `nullable` metadata alone is not evidence that a required field contains no NULL values.

Do not call `score_udf(*columns)` for a named column signature; MLflow documents that separate UDF arguments are forwarded with ordinal names. Run the limited-row smoke test with this same named struct.

Maintain an explicit reviewed type map. At minimum, distinguish MLflow `boolean`, `integer`, `long`, `float`, `double`, `string`, `binary`, `date`, and `datetime`; do not collapse numeric widths or timestamp/date semantics. Reject MLflow `AnyType`, arrays, objects, or maps unless the scoring implementation has an explicit compatible Spark schema and fixture.

## Tensor Inputs

Tensor signatures are a separate scoring path. Reject them in the default tabular named-struct path. To support one, declare and test:

- tensor name, dtype, and full shape with only documented variable dimensions
- Spark array input type and deterministic reshape order
- null and empty-array behavior
- a limited-row result compared with direct pyfunc prediction

MLflow Spark UDF tensor inputs use array columns and reshape them according to the signature. Never flatten or cast a tensor silently.

## Outputs

Inspect the output signature before selecting `result_type`:

- one scalar output: declare the exact Spark scalar result type
- multiple named column outputs: declare a Spark `struct` with exact names, order, types, and nullability, then select fields explicitly
- tensor output: use an explicitly tested array/struct representation or reject it
- missing, unnamed, `AnyType`, or incompatible output signature: stop before the full run

Do not take the first field from a multi-output prediction or relabel a tensor as a scalar score. Preserve `raw_score` separately from any calibrated score and publish-selection score.

## Offline Contract Tests

Keep fixtures for:

- a valid ordered named-column signature
- an absent optional input and a present optional input in signature order
- a missing required input, reordered input, and extra feature
- a type mismatch
- a required input containing NULL
- a supported optional input
- a rejected tensor input on the tabular path
- a rejected multi-output result on the scalar path
- an explicitly declared multi-output struct path, if the project supports one

Official contracts:

- [MLflow model signatures](https://mlflow.org/docs/latest/ml/model/signatures/)
- [MLflow Spark UDF input and tensor behavior](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.pyfunc.html#mlflow.pyfunc.spark_udf)
