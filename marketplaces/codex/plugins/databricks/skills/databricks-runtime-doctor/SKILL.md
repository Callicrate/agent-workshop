---
name: databricks-runtime-doctor
description: "Use when debugging Databricks runtime: DBR/packages, CUDA/GPU, ML imports, %pip/restart, or cluster driver/capacity. Do not trigger for Python-only failures, model tuning, Spark ETL, serving, or bundles."
metadata:
  short-description: Debug Databricks runtime failures.
---

# Databricks Runtime Doctor

## When to Use

- Diagnose Databricks ML failures caused by DBR, library, CUDA, or accelerator incompatibilities
- HF TrainingArguments.__init__() errors after a DBR or transformers upgrade
- Torch, sympy, or typing_extensions conflicts on DBR 16/17 images
- cuFFT, CUDA capability, or accelerator warnings tied to the cluster image
- Single versus multi-GPU misconfiguration such as ddp_find_unused_parameters or device placement issues
- Import-time failures from optional ML backends such as vllm, spaCy, NLTK, or pydantic_ai
- Notebook runtime-order failures involving %pip, Python restart, imports after use, or missing symbols
- Databricks job or cluster failures such as DRIVER_NOT_RESPONDING, capacity errors, or failed single tasks
- Notebook cells that look like Python hangs while importing Spark tables or emit compute warnings such as needing at least one worker

## When NOT to Use

- General model-quality tuning with no runtime or package failure
- Delta or PySpark pipeline issues unrelated to ML libraries or GPU runtime
- Model serving endpoint rollout, inference latency, or online serving configuration
- Bundle authoring unless the fix is specifically a dependency pin or runtime selection

## Workflow

1. Capture the real environment first. Run scripts/collect_env_snapshot.py, adding --packages and --nltk-data probes when optional dependencies or data resources appear in the traceback. Package probes accept only top-level Python identifiers; `--nvidia-smi-timeout` accepts whole seconds from 1 through 120. The snapshot redacts and bounds probe output, environment values, workspace URLs, subprocess output, and expected probe failures, so treat it as diagnostic evidence rather than a full environment export. Its `nvidia-smi` probe isolates the process tree; if `cleanup_incomplete`, `drain_threads_alive`, or `descendants_alive` is true, do not treat that probe as a clean hardware result. Read the failing stack trace, notebook or script, and job or cluster spec. Prefer live package versions over inferred defaults when both are available. Record freshness metadata: when the evidence was collected, from which cluster or job, and whether it came from a live run or a cluster spec.
2. Classify the failure and load the right reference. Warning-only noise such as local Could not find cuda drivers: record it, then continue to the first fatal exception. Import-time dependency or missing data resource: references/import-time-dependency-triage.md. Notebook install, restart, import, or use-order failure: run scripts/check_notebook_runtime_order.py. Spark/table access hang or compute warning: verify cluster mode, worker count, task compute, and Spark command support before rewriting Python dataflow. Databricks job, cluster, capacity, or task-isolation failure: references/databricks-job-runtime-triage.md. TrainingArguments or HF API drift: references/hf-trainingargs-compat.md. Package, CUDA, DDP, or runtime-image mismatch: references/known-good-matrices.md. Missing or stale live evidence: references/fresh-runtime-evidence.md.
3. Propose the smallest safe fix. Rename or drop unsupported TrainingArguments fields. Pin or relax specific packages in bundle or job environment config. Set required accelerator or DDP flags such as ddp_find_unused_parameters=False. Recommend a different DBR or GPU only when a code or dependency fix is not enough. Treat static known-good matrices as advisory unless they include last-verified metadata; prefer live cluster/runtime evidence, workspace UI options, or current project docs over memory.
4. Validate on a small rerun. Re-run the failing import path, one training step, or a tiny subset.
5. Return an explicit repair plan with exact code diff or replacement block, exact dependency pins, exact DBR or cluster recommendation, and what was verified and what remains unverified.

Warning: Do not recommend GPU pins from Could not find cuda drivers alone. In local or CPU mode, treat it as context and continue to the first fatal exception.

Any static known-good matrix is advisory and must be treated as last-verified guidance. For current jobs, prefer live cluster/runtime evidence, workspace-supported options, and actual job specs over memory.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| scripts/collect_env_snapshot.py | You need live DBR, Python, CUDA, packages, optional dependency probes, NLTK resources, job metadata, or nvidia-smi output | Bounded, redacted environment evidence with structured expected failures |
| scripts/check_notebook_runtime_order.py | A notebook fails from %pip, restart, import order, or symbol-before-import issues | Static notebook runtime-order findings; unreadable or malformed notebooks return a cell-zero structured error |
| references/import-time-dependency-triage.md | The failure is a ModuleNotFoundError, optional backend import, missing resource, GPU warning distraction, or swallowed child error | Lazy import and first-blocker triage |
| references/databricks-job-runtime-triage.md | The failure is DRIVER_NOT_RESPONDING, capacity, task-only failure, or job cluster mismatch | Platform versus code classification and one-task recovery |
| references/hf-trainingargs-compat.md | The failure is an HF constructor or field-compatibility issue | Version-safe TrainingArguments patch |
| references/known-good-matrices.md | The failure looks like a runtime, package, CUDA, or DDP mismatch | Safe pinning and runtime guidance |
| references/fresh-runtime-evidence.md | Live cluster evidence is missing, stale, or must be reconstructed from spec | Freshness, GPU, and from-spec evidence contract |

## References

- references/import-time-dependency-triage.md - optional dependency, lazy import, warning triage, and wrapper failure rules
- references/databricks-job-runtime-triage.md - job run, task attempt, capacity, and cluster health triage
- references/known-good-matrices.md - curated compatibility table and project runtime conventions
- references/hf-trainingargs-compat.md - TrainingArguments field compatibility
- references/fresh-runtime-evidence.md - freshness, GPU, and cluster-spec reconstruction rules
