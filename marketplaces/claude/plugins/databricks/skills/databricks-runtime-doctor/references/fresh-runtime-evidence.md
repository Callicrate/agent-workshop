# Fresh Runtime Evidence

Use this reference when a Databricks ML runtime recommendation depends on package versions, CUDA, GPU shape, or a cluster image that may have drifted.

## Evidence Priority

Prefer evidence in this order:

1. live failing run snapshot from the same cluster or job
2. fresh one-cell or one-step probe on the target cluster
3. job or cluster spec plus installed library declarations
4. documented DBR defaults as a hypothesis only

Static known-good matrices are starting points, not proof.
Label them as stale or inferred unless a live snapshot confirms the runtime.

## Freshness Metadata

Record these fields before proposing pins or runtime changes:

- workspace or profile
- cluster ID or job ID when available
- DBR version and ML/GPU flavor
- Python version
- `torch`, `transformers`, `accelerate`, `sympy`, `typing_extensions`, and CUDA-related package versions
- optional package probes requested by the traceback, such as `vllm`, `spacy`, `nltk`, `pydantic_ai`, or `tensorflow`
- external data resources requested by the traceback, such as NLTK corpora or tokenizers
- Databricks job ID, run ID, task key, task attempt, and cluster ID when present
- `nvidia-smi` output or a recorded reason why it was unavailable
- collection timestamp and source command
- whether evidence is live, from-spec, or inferred

## GPU And CUDA Checks

For GPU failures, capture:

- `nvidia-smi` output or platform equivalent
- GPU model and count
- CUDA runtime reported by PyTorch
- `torch.cuda.is_available()`
- compute capability when relevant
- DDP or accelerator flags used by the failing code

Do not recommend GPU-specific pins or DDP flags without checking whether the job is single-GPU, multi-GPU, or CPU-only.

## From-Spec Mode

When live cluster access is unavailable, reconstruct the runtime from:

- bundle or job cluster config
- cluster policy
- environment or wheel dependencies
- notebook `%pip` cells if they are part of the job path
- library install order
- prior run logs

Mark the recommendation as `from-spec` and list what remains unverified.

## Repair Output

Every runtime repair plan should include:

- exact code or config diff
- exact dependency pins or relaxations
- DBR or GPU recommendation, if any
- why smaller code changes are insufficient
- validation path: import probe, constructor probe, one training step, or tiny subset rerun