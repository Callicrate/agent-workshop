# Hugging Face TrainingArguments Constructor Evidence

Use the installed `TrainingArguments.__init__` signature as the compatibility
source for the target runtime. Do not infer accepted keywords from a
`transformers` version, a DBR family, or a package matrix.

One rejected field does not establish a version boundary for every runtime.
Likewise, the verified DBR 17.3 ML package version does not justify removal
claims: the [Transformers 4.51.3 source](https://raw.githubusercontent.com/huggingface/transformers/v4.51.3/src/transformers/training_args.py)
and [current main source](https://github.com/huggingface/transformers/blob/main/src/transformers/training_args.py)
both define `logging_first_step`.

## Probe The Target Runtime

From the target repository root, run the bundled helper with its absolute skill
path:

```powershell
python -B <skill-root>/scripts/probe_training_arguments.py --fields eval_strategy,evaluation_strategy,logging_first_step
```

The helper imports `transformers` only for this explicit probe, calls
`inspect.signature(TrainingArguments.__init__)`, and emits bounded JSON with:

- the loaded package version;
- requested field support as `supported`, `unsupported`, or `unknown`;
- whether the signature is explicit, variadic, opaque, or unavailable.

It never constructs `TrainingArguments`, `Trainer`, or a model, and does not
query CUDA or a network service. An unavailable or failed optional import is
incomplete evidence. A variadic or opaque signature does not prove support for
unlisted keywords. Signature support also does not prove runtime initialization
or field-value validity.

## Build Only After An Explicit Signature Check

Use the probe result to select the evaluation key. Prefer `eval_strategy` when
it is explicitly supported; otherwise use `evaluation_strategy` when explicitly
supported. Select an alias only from a receipt with `complete: true`, an
`explicit` signature, and `accepts_variadic_keywords: false`. If neither alias
is supported, the receipt is incomplete or malformed, or the signature is
variadic or opaque, stop with an actionable unknown/unsupported error instead of
guessing. The receipt must include both aliases and give each exactly
`supported`, `unsupported`, or `unknown`; reserve the neither-supported result
for `unsupported`/`unsupported`. Missing, malformed, or `unknown` alias evidence
is unknown evidence. This conservative selector does not claim that every
variadic callable rejects explicit aliases; it refuses to treat the receipt as
complete evidence.

```python
import inspect
import transformers


def training_argument_fields() -> frozenset[str]:
    signature = inspect.signature(transformers.TrainingArguments.__init__)
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        raise RuntimeError(
            "TrainingArguments signature is variadic; run the signature probe "
            "and resolve the target-runtime constructor contract first"
        )
    return frozenset(
        name
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    )


def build_training_args(output_dir: str) -> "transformers.TrainingArguments":
    supported = training_argument_fields()
    if "eval_strategy" in supported:
        evaluation_key = "eval_strategy"
    elif "evaluation_strategy" in supported:
        evaluation_key = "evaluation_strategy"
    else:
        raise RuntimeError(
            "TrainingArguments supports neither eval_strategy nor "
            "evaluation_strategy in this runtime"
        )

    requested = {
        "output_dir": output_dir,
        evaluation_key: "epoch",
        "save_strategy": "epoch",
        "logging_steps": 100,
        "ddp_find_unused_parameters": False,
    }
    unsupported = sorted(set(requested).difference(supported))
    if unsupported:
        raise RuntimeError(
            "TrainingArguments fields unsupported by the installed constructor: "
            + ", ".join(unsupported)
        )
    return transformers.TrainingArguments(**requested)
```

Put every generated option in `requested` before the comparison. Do not filter
unknown options out and continue; report their names without logging values.
Choose mixed precision and hardware only from separate runtime/GPU evidence, not
from this constructor check.
