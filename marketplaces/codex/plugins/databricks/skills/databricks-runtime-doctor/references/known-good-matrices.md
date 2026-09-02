# Databricks Runtime Compatibility Evidence

Static package rows are diagnostic context, not an installation recipe. Before
recommending a runtime or package change, capture the target runtime with
[`../scripts/collect_env_snapshot.py`](../scripts/collect_env_snapshot.py) and
label the result as live, from-spec, or inferred.

## Verified Reference Row

| Exact runtime flavor and version | Verified | PyTorch | transformers | Scope |
|---|---:|---:|---:|---|
| Databricks Runtime **17.3 LTS for Machine Learning** | 2026-08-29 | 2.7.0 | 4.51.3 | Databricks-published ML image defaults; not a pin recommendation |

Source: [Databricks Runtime 17.3 LTS for Machine Learning release notes](https://docs.databricks.com/aws/en/release-notes/runtime/17.3lts-ml).
The page also identifies the GPU image CUDA libraries. Treat those image
libraries as cluster facts to verify, not a reason to install a hardware- or
CUDA-suffixed wheel independently.

## How To Use This Row

1. Confirm the exact runtime flavor and version in the failing run. `17.3 LTS`
   and `17.3 LTS for Machine Learning` are different images.
2. Capture installed `torch`, `transformers`, `accelerate`, `sympy`,
   `typing_extensions`, CUDA availability, device count, and `nvidia-smi`
   evidence from the target environment.
3. Compare a specific failure against those facts. Do not infer a package
   mismatch from a GPU model, a local CUDA warning, or a broad runtime family.
4. Make the narrowest change that addresses the first fatal exception, then
   rerun its import or one-step reproduction.

If the live image differs from the reference row, the live image wins. A pin
is justified only by a reproduced package constraint or documented project
contract, and must be validated against that exact runtime image.

## Current Runtime Families Require A Live Check

Do not extend the 17.3 LTS ML row to newer runtime families. Databricks lists
both [18 LTS ML](https://docs.databricks.com/aws/en/release-notes/runtime/18ml)
and [19 ML](https://docs.databricks.com/aws/en/release-notes/runtime/19ml) as
separate images with their own package sets and behavior changes. Check the
current release notes and a live snapshot before suggesting any dependency or
CUDA guidance for those runtimes.

The [runtime compatibility index](https://docs.databricks.com/aws/en/release-notes/runtime/)
is the source for supported runtime variants and lifecycle status. It does not
replace runtime-local package evidence.

## CUDA And Distributed Training

- A local `Could not find cuda drivers` warning is context, not a GPU repair
  instruction. Continue to the first fatal exception.
- For a GPU failure, verify the ML runtime flavor, whether CUDA is available,
  the bounded device list, and the relevant `nvidia-smi` result before changing
  any torch or CUDA dependency.
- Recommend `ddp_find_unused_parameters=False` only after confirming that the
  workload actually runs under DDP and that its model path supports that flag.
- Treat CPU-only, single-GPU, multi-GPU, and unavailable-GPU states as
  distinct. Do not infer them from an instance-family name.
