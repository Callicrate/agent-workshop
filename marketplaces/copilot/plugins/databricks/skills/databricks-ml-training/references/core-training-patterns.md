# Core Training Patterns

Use this reference for rules that apply to every Databricks training job. Load a model-family reference only after this one.

## Closed Training Contract

Start with [assets/training-config-schema.json](../assets/training-config-schema.json), then validate the concrete config before a data read or model fit.

```powershell
python skills/databricks-ml-training/scripts/validate_training_config.py --config training-config.json
```

The contract requires model, a frozen feature-schema artifact, target/class contract, seeded train/validation/test windows, runtime, output root, metrics, threshold, signature, promotion policy, and source provenance. It rejects unknown keys at every configuration-object boundary. Do not add unvalidated notebook-only parameters.

The feature-schema artifact records a version, SHA-256 fingerprint, every selected feature's logical type, nullability, and null policy, plus the target's matching contract. Before fitting, compare the selected runtime schema with [scripts/feature_schema_contract.py](../scripts/feature_schema_contract.py). A type, nullability, order, target, or fingerprint change is schema evolution: version and review it, then retrain and re-evaluate; never silently coerce it inside the model fit.

Use [assets/parameter-block-template.py](../assets/parameter-block-template.py) for a side-effect-free run context. Inject one UTC `started_at`, job ID, run ID, and idempotency key in the entry point. The template constructs paths but does not create folders, call MLflow, or write data at import time.

## Immutable Sources And Example-Time Features

Reproducibility has two independent requirements:

1. **Physical source snapshot.** Record every input as `catalog.schema.table`, Delta version, and Delta commit ID. Reruns read those immutable versions, not whatever is latest.
2. **Per-example feature validity.** Join SCD2 features at each example's prediction timestamp, using `valid_from <= example_at < valid_to` and an availability timestamp when one exists. A valid future row or a late-arriving row must not be used for an earlier prediction.

Load [point-in-time-training.md](point-in-time-training.md) before table-backed training. The train, validation, and test windows are pairwise disjoint, and their stable example IDs must also be pairwise disjoint. Run [scripts/point_in_time_contract.py](../scripts/point_in_time_contract.py) checks in local tests or pipeline validation.

## Runtime And Development Mode

- State the DBR family, Python version, cluster mode, worker count, GPU count, distributed mode, and Spark-table import requirement. Every run declares `max_tuning_trials`; dev runs also declare `sample_cap`. Transformer configurations declare the selected `foundation_model`.
- Run [training-compute-preflight.md](training-compute-preflight.md) before expensive GPU or Spark-backed training.
- Start with `dev` mode, capped data, and bounded tuning. A dev run is ineligible for promotion by contract.
- Use a normalized Unity Catalog Volume path exactly shaped as `/Volumes/<catalog>/<schema>/<volume>/...` for Databricks artifacts. A local artifact root is allowed only when it is an absolute normalized child of a project-owned root. Reject `.`/`..`, backslashes, NULs, drive/UNC forms, and unsafe job/run/idempotency path segments. Never use `/dbfs/Workspace/Shared` for training artifacts.

## MLflow And Private Artifacts

Always set Databricks tracking and Unity Catalog registry URIs, set the experiment explicitly, and log the run IDs, source snapshots, ordered feature-schema artifact and exact fingerprint, feature list, class order, positive class, threshold, metrics, and runtime contract. `model.name` is an MLflow logged-model/artifact label, not a Unity Catalog name. It keeps the legacy safe identifier grammar, including dots, but is opaque: even an FQN-shaped value never authorizes or derives registration. When `outputs.register_model` is `true`, `outputs.registered_model_name` is separately required as the explicit three-part Unity Catalog destination `<catalog>.<schema>.<model>`. It is the raw MLflow API object name: each component is 1–255 Unicode code points and cannot contain a period, U+0020 space, `/`, an ASCII control character, or U+007F; hyphens, leading digits, and non-ASCII characters are allowed. Do not add SQL backtick quoting merely because a component has a hyphen or another special character; this config boundary rejects backticks and does not support literal-backtick UC object names. When registration is `false`, the destination is optional but, if supplied, is still validated as a planned destination and does not authorize registration. The signature input-example columns must exactly equal the ordered feature list; a partial, extra, or reordered input example is invalid.

Log only bounded synthetic or redacted input examples. Do not log direct person, record, account, device, DOB, IP/MAC, authentication, credential, key, token, or session identifiers. The signature contract requires an ACL group and retention period. Scan example column names with [scripts/mlflow_example_privacy.py](../scripts/mlflow_example_privacy.py), then have a human reviewer inspect the actual synthetic/redacted values and combined quasi-identifiers before applying the workspace ACL and retention controls. The scanner is a name guardrail, not a substitute for value review.

## Imbalance And Thresholds

For skewed classification, load [imbalanced-classification.md](imbalanced-classification.md). `scale_pos_weight` is binary-only; class weights are structured label/weight records and must cover configured multiclass labels. Validate class support in every binary split, resolve the positive class from `model.classes_`, and tune a binary threshold on validation data only. Test is reserved for final reporting. The threshold helper supports F1, precision, and recall; it rejects single-class data, booleans, and non-finite probabilities, and breaks exact ties at the lower threshold.

## Selection Is Not Promotion

Selection chooses one candidate from validation metrics. Promotion eligibility is a separate full-mode policy with an allowed metric/direction pair, directional metric threshold and baseline delta, directional slice gates, calibration, and an explicit approved status. PR-AUC, ROC-AUC, F1, precision, and recall must be finite values in `[0, 1]`; log loss must be finite and non-negative. Apply those domains to candidate, baseline, slice, and policy-threshold observations, rejecting booleans. Load [ml-lifecycle-handoffs.md](ml-lifecycle-handoffs.md) before changing a handoff or promotion flow.

`register_model`, `write_promotion_candidate`, and `request_promotion` default to `false`. Dev mode rejects every one of them. In full mode, require an enabled policy and a successful `evaluate_eligibility` decision before performing any of those side effects; configuration validity alone is not a completed gate.

## Completion Gate

Before calling training complete, verify all of the following:

- Exactly one candidate was selected by the declared metric, direction, validation split, and deterministic tie-break.
- The model object, signature, feature list, class order, positive class, threshold artifact, and required metrics are present.
- The run records every Delta snapshot, source commit ID, split identity, seed, job/run IDs, and idempotency key.
- Input examples passed the privacy scan and have the required ACL and retention plan.
- Registration, candidate writes, and promotion were skipped unless explicitly requested and their respective gates passed.
