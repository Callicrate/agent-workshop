# ML Lifecycle Handoffs

Use this when a training change affects promotion, batch inference, serving, monitoring, or retraining.

## Handoff Record

Produce or verify these fields before downstream work:

- Fully qualified Unity Catalog registered-model name from `outputs.registered_model_name` and its version, only when registration was requested. Keep it distinct from the legacy-safe MLflow logged-model/artifact label in `model.name`; a dotted label never infers registration.
- MLflow experiment path and run ID.
- Source `catalog.schema.table` values with recorded Delta versions and commit IDs.
- Split windows, stable split IDs, feature list, target, class order, positive class, and threshold artifact.
- Input example policy, model signature, ACL group, and retention period.
- Runtime contract, Git commit, job ID, run ID, idempotency key, and dev or full mode.

## Selection Policy

Selection answers only: which candidate is best for this declared validation metric?

- Select from the validation split only.
- Use only these pairs: maximize `pr_auc`, `roc_auc`, `f1`, `precision`, or `recall`; minimize `log_loss`.
- Resolve ties by ascending stable `candidate_id`.
- Log the candidate set, selection metric, direction, split, and tie-break result.

Use `select_candidate` in [scripts/promotion_eligibility.py](../scripts/promotion_eligibility.py). It rejects duplicate candidate IDs, unsupported fields, Boolean/non-finite metrics, and Boolean or invalid support values.

## Eligibility Policy

Eligibility answers a different question: may the selected candidate be registered or promoted now?

Promotion requires all of the following:

- `training.mode` is `full`. Dev mode is explicitly ineligible.
- The configured metric threshold passes in its declared direction.
- The metric beats the recorded baseline by the required directional delta.
- Every required slice passes its minimum support and directional metric gate.
- Calibration error is within the configured maximum.
- Approval status is `approved` and has a durable approval reference.

Use `evaluate_eligibility` in [scripts/promotion_eligibility.py](../scripts/promotion_eligibility.py) after selection. Its policy has a closed shape; `enabled: false` is always ineligible, while `enabled: true` requires the full-mode metric/direction, threshold, baseline, slice, calibration, and approval contract. Record every failed gate, not only the final Boolean.

`register_model`, `write_promotion_candidate`, and `request_promotion` are false by default. They are invalid in dev mode and require full mode, enabled eligibility, and a successful observed eligibility decision before execution. A valid selection or config is not authorization to cause any of those side effects.

## Promotion Candidate Contract

When a downstream workflow consumes promotion candidates, record:

- Candidate ID, selected model URI, MLflow run ID, and requested UC model version.
- Selection policy and observed validation result.
- Eligibility policy, observed baseline, slices, calibration, approval status/reference, and final decision.
- Data provenance, feature/threshold/signature artifact URIs, and training mode.
- `valid_from`, `valid_to`, `is_current`, and removal reason when the candidate table is SCD2.

For SCD2 candidate tables, allow one current row per candidate decision space. A replacement closes the former current row before writing the new current row. Query candidates at a requested timestamp with `valid_from <= timestamp < valid_to` or an open `valid_to`.

## Downstream Rules

- Batch inference loads the recorded model version or alias and persists model version, run ID, threshold, and scored timestamp.
- Serving uses the recorded signature, input-example privacy policy, model version, feature lookup contract, and class mapping.
- Monitoring uses the prediction schema, label availability delay, baseline window, and drift metrics.
- Repair jobs are explicit, idempotent, and dry-run by default. They validate duplicate candidate IDs and SCD2 invariants before writes.

## Completion

Do not call training complete when a downstream consumer would have to infer feature order, threshold, model version, data snapshot, selection rule, or eligibility decision from notebook state.
