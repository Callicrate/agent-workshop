# MLflow Run Readiness Contract

Use the auditor as a read-only readiness gate. It reports whether the evidence
was completely inspected separately from whether the evidence is sufficient.
It does not start training, register or promote models, move aliases, or change
serving.

The CLI requires `--profile <Databricks-profile>` matching
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and configures `databricks://<profile>`
before it creates an MLflow client or reads runs/models. It never falls back to
implicit local MLflow tracking storage.

## Local Policy Scope

The typed requirements below are this skill's **local readiness policy**, not a
claim that MLflow universally requires those fields or filenames. They make a
bounded audit repeatable for this workspace. Teams with a different delivery
contract should change the policy and its tests together.

The local policy accepts unquoted three-part identifiers made from letters,
digits, underscores, and hyphens; timezone-aware ISO-8601 timestamps; IANA
timezone names; nonnegative decimal-integer counts and offsets; and one of
`short_circuit_unscorable`, `fail`, `drop`, or
`impute_with_audited_default` as the null policy. A relative `.py` entrypoint
cannot contain `..`. Training offsets are interpreted from the as-of timestamp,
so the start offset must be at least as far back as the end offset. Explicit
start/end timestamps must be ordered.

## Stage Requirement Matrix

Select the intended stage. Every row inherits the requirements above it; a later
stage is never less strict than an earlier one.

| Stage | Additional required evidence |
| --- | --- |
| `prototype` | Finished run; nonblank source table, dataset version, experiment and workspace paths, three-part UC model name, nonnegative train/validation row counts; fixed point-in-time evidence for table-backed data; actual `MLmodel` artifact(s), each with signature and input example; valid `feature_list.json`, `label_mapping.json` or `label_map.json`, `metric_formulas.json`, and `confusion_matrix.json`. |
| `job-ready-training` | Script entrypoint and runtime parameter contract; valid `job_parameter_contract.json` and `job_smoke.json`; exact `inference_stub.py` or `inference_loader.py` artifact. |
| `promotion-candidate` | Selected-model objective; valid `selected_model.json` and `promotion_handoff.json` with registered-model identity. |
| `serving-candidate` | Inference loader reference; valid `serving_contract.json` with model URI, input schema, output schema, and null policy. |
| `batch-inference-dependency` | Valid `batch_input_contract.json` with source table and input schema, plus `batch_output_contract.json` with output schema. |

For every table-backed run, require fixed `AT_TIMESTAMP` (or equivalent),
timezone, SCD2/table-version semantics, bounded training window, source
freshness, null policy, and nonnegative skipped/unscorable-row evidence.
Reject moving-time values such as `now`, `current_timestamp`, or `datetime.now`.

Metric values must be finite. Custom precision-percent metrics require threshold,
threshold metric, positive class, and averaging evidence. A registered model name
must be a three-part Unity Catalog path.

## Typed JSON Policy

The auditor parses JSON strictly: non-standard `NaN`, `Infinity`, and
`-Infinity` constants are rejected. It does not print any parsed body.

- `feature_list.json`: nonempty, duplicate-free `features` array of trimmed strings.
- `label_mapping.json` or `label_map.json`: nonempty trimmed labels with contiguous
  nonnegative integer IDs starting at zero.
- `metric_formulas.json`: known canonical metric, formula/class/averaging/
  denominator strings, permitted averaging mode, and a finite threshold in `[0, 1]`.
- `confusion_matrix.json`: a nonnegative finite square numeric matrix with at
  least two classes.
- `job_parameter_contract.json`: safe relative Python entrypoint and a nonempty
  parameter map containing fixed ISO `at_timestamp` or `AT_TIMESTAMP`.
- `job_smoke.json`: one single-line command containing `--at-timestamp`.
- `null_policy.json`: allowed policy plus a nonnegative skipped or unscorable-row
  count. `source_freshness.json` contains a timezone-aware fixed ISO `checked_at`.

Promotion, serving, and batch artifacts have a second check beyond shape:
`selected_model.json`, `promotion_handoff.json`, `serving_contract.json`, and
both batch contracts must carry the current `run_id` and
`registered_model_name`. Serving uses a `models:/<registered-model-name>` URI;
batch input carries one of the run's logged source tables. Any mismatch is an
inconsistent-value finding, never a clean handoff.

The local serving URI grammar is structural: `models:/<registered-model-name>`,
`models:/<registered-model-name>@<alias>`, or
`models:/<registered-model-name>/<positive-version>`. The parsed complete model
name must exactly equal the logged `registered_model_name`; prefix lookalikes
such as `models:/catalog.schema.model-evil@champion` do not match. The logged
`inference_loader` parameter is checked by the same grammar and exact comparison.

## Artifact And Model Evidence

The auditor iteratively lists MLflow artifacts in stable path order. It tracks
visited directories, deduplicates repeated responses, and marks the audit
incomplete on a list error or depth, entry, or byte-limit truncation. It does not
use substring matching: only the exact required basename or path satisfies an
artifact requirement. For example, `not_metric_formulas.json` does not satisfy
`metric_formulas.json`.

Every file whose exact basename is `MLmodel` defines one logged model directory.
The auditor derives its URI as `runs:/<run-id>/<model-directory>` and checks every
such model for both a signature and an input example. It never assumes that a
folder merely containing the word `model` is a model artifact.

Saved input-example metadata is usable only when it has a nonblank string
`artifact_path` and one of MLflow's loader-supported types: `dataframe`,
`ndarray`, `sparse_matrix_csc`, `sparse_matrix_csr`, or `json_object`; the
auditor then loads the example and treats a loader failure or `None` as
incomplete evidence, while an empty loaded object or list remains valid.
When the bounded artifact inventory reaches an entry or byte limit, it does not
invoke an input-example loader and instead reports the example as unverified.

Required non-model JSON artifacts are downloaded one file at a time only when
their reported size fits the limit, parsed as UTF-8 JSON, and validated against
the stage-specific schema above. The report names the evidence kind but never
prints artifact content. A failed download, invalid JSON, malformed map, or
over-limit artifact cannot produce a clean result.

Each directory iterator is consumed only through a per-directory limit plus one
lookahead item before sorting. Total directory visits, entries, bytes, JSON bytes,
code files, code-walk entries, and code bytes are capped. A cap is an incomplete
audit, not a partial pass. The code scan collects only a bounded candidate set
before deterministic sorting; it fails when that ordering cannot be bounded.

## Code Scan And Registry Drift

`--code-path` is optional. Its status is always one of:

- `not_requested` when no path was supplied;
- `complete` when the bounded scan completed; or
- `failed` when the supplied path is missing, unreadable, or exceeds a file or
  byte limit.

The scan examines supported text files in deterministic order and records only
relative file paths. A scan failure makes the overall audit incomplete. The
auditor checks stale names in full string parameter values as well as the scan.
It reports an expected-model-name absence only after a `complete` scan; no scan
does not create a false drift finding.

## Output And Exit Status

Both output modes originate from one envelope. With `--json`, stdout is always a
single redacted JSON object, including argument and operational errors:

```json
{
  "schema_version": 2,
  "decision": "clean|findings|no_qualifying_runs|incomplete|operational_error",
  "complete": true,
  "requested_count": 5,
  "found_count": 3,
  "clean_count": 2,
  "code_scan": {"status": "complete"},
  "runs": [
    {
      "run_id": "...",
      "complete": true,
      "is_clean": false,
      "findings": {},
      "warnings": []
    }
  ],
  "errors": []
}
```

`is_clean` is true only when a run is `FINISHED`, the audit is complete, and no
finding remains. Failed, killed, scheduled, or running runs cannot be clean.

- Exit `0`: at least one qualifying run and every run is complete and clean.
- Exit `2`: the audit completed but has findings, or no qualifying runs were
  found.
- Exit `1`: an operational failure or incomplete evidence collection.

`--last` accepts only integers from `1` through `1000`.

## Safe Diagnostics

Exceptions are represented only by type, a safe code, and a bounded redacted
message. The output boundary redacts common Authorization, Bearer, token, API
key, secret, password, and credential-in-URL forms, including `user@host` and
`user:password@host`. Redaction also applies recursively to mapping keys,
artifact paths, and all envelope fields. Do not add raw exception objects,
tracebacks, artifact bodies, environment values, or credential strings to the
report.
