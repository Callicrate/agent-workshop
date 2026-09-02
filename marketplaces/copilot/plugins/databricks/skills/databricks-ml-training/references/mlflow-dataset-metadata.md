# MLflow Dataset Metadata

Use this checklist for Databricks training runs that need reproducibility across training, promotion, batch inference, serving, or audit.

## Required Run Metadata

Log or record:

- Full source table names, Delta versions, and Delta commit IDs.
- Example timestamp column, SCD2 predicate and availability rule, split windows, split IDs, and row counts.
- Feature list and feature-set version, target column, class order, positive class, and label source version.
- Random seed, model family, hyperparameters, runtime, Git commit, job ID, run ID, and idempotency key.
- Selection metric, direction, tie-break, threshold objective, validation support, and calibration result.
- Registration request, promotion request, eligibility policy, approval status, and final side-effect decisions.

## Dataset Logging

Prefer MLflow dataset inputs. For table-backed training, use `mlflow.log_input` or an equivalent project helper that records table name, immutable version, feature subset, and filters.

If dataset logging is unavailable, log equivalent params and write a dataset-contract artifact. A source table name without its Delta version and commit ID is insufficient for a reproducible rerun.

## Private Input Examples And Signatures

MLflow input examples are artifacts, not harmless debugging output.

- Use only synthetic or redacted examples, with at most the configured number of rows.
- Exclude direct person, record, account, and device IDs; DOB; IP and MAC addresses; names, emails, phones, addresses, and SSNs; and authentication, credential, password, API-key, secret, token, cookie, and session forms. The scanner first validates the configured column grammar and compares the case-normalized raw name with the exact documented safe controls or exact `<sensitive-root>_(count|rate)` aggregates. Only then does it normalize separators, camel/acronym boundaries, IPv4/IPv6 spellings, plurals, and `authn`/`authz` aliases. It denies any sensitive root found directly or by bounded trie/DP segmentation of a compact identifier; tokenization never creates safety. The documented exceptions are `valid`, `grid`, `hybrid_score`, `candidate_id`, `feature_id`, `model_id`, `token_count`, `cookie_rate`, `address_count`, `address_rate`, `ip_count`, `mac_rate`, `authentication_rate`, `authorization_count`, `api_count`, `bearer_rate`, `session_count`, `cookie_count`, `password_rate`, `secret_count`, `credential_rate`, `contact_count`, `contact_rate`, `feature_count`, `feature_rate`, `model_count`, and `model_rate`.
- Scan the configured columns before logging:

  ```python
  from mlflow_example_privacy import validate_input_example_columns

  validate_input_example_columns(input_example.columns)
  ```

- Set a least-privilege experiment or artifact ACL for the configured access-control group.
- Apply and document the configured retention period. Do not use an indefinitely retained shared experiment as a privacy substitute.
- Do not log raw source records, feature values that remain identifying after combination, access tokens, or credentials in params, tags, examples, error messages, or reports.

The name-based scanner is a guardrail, not a data-classification system. Before logging, a named human data owner must review the actual synthetic or redacted values, combinations of apparently benign fields, and whether redaction remains effective in the target experiment's access context. Record that review with the artifact decision; do not treat a clean scanner result as approval.

## Experiment And Model Provenance

Before comparing runs, resolve the MLflow experiment by exact path and distinguish the legacy-safe `model.name` logged-model artifact label from the separately configured three-part `outputs.registered_model_name` Unity Catalog destination. A dotted or FQN-shaped artifact label remains only a label and never infers the destination. The destination is the raw MLflow API value, not SQL syntax: pass unquoted components even when they contain a hyphen. This config boundary rejects SQL backticks, including literal-backtick UC object names, to prevent SQL/API ambiguity. Report a UC model version and its fully qualified registered-model name with the run ID only when registration was requested; never infer the destination from the logged-model name.

Downstream inference must recover the model version, run ID, feature list, threshold, label mapping, source snapshots, signature, and dataset window without notebook state.

## Cleanup Checks

Before deleting models or artifacts:

- List experiment runs with pagination and logged model artifacts separately from registered versions.
- Confirm the project, experiment path, catalog, schema, and registered model full name.
- Preserve run IDs, artifact paths, version IDs, ACL evidence, and retention decision in the cleanup report.
