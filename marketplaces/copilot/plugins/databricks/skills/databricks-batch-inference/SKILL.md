---
name: databricks-batch-inference
description: "Use when scoring Delta tables, loading UC models, writing predictions, or reconciling scoring rows; guides batch writes. Do not trigger for training, serving endpoints, general ETL, or local APIs."
metadata:
  short-description: Run Databricks batch scoring.
---

# Databricks Batch Inference

## When to Use

- Scoring a Delta table and writing predictions back
- Loading a Unity Catalog model by alias or version for Spark batch scoring
- Updating only null-scored rows to avoid clobbering
- Reconciling row counts, null-score coverage, and sample outputs after a scoring job
- Mapping train, score, promote, and ensemble notebook dataflow before edits
- Running bounded next-window scoring after promotion, threshold, or ensemble changes
- Adding a continuous catch-up lookback so late-arriving rows behind a checkpoint still get scored
- Gating predictions from publication and root-causing a spike in one predicted class
- Adding audit traces for model version, run ID, source window, and unscorable rows

## When NOT to Use

- Real-time serving endpoint design or rollout tasks
- Model training, threshold tuning, promotion policy, or MLflow experiment review as the primary task; use databricks-ml-training or mlflow-run-auditor, then return here for scoring writes and reconciliation
- General Spark ETL contract changes unrelated to scoring writes
- Local Databricks REST API scripting

## Workflow

1. Start with references/core-batch-inference-patterns.md to define the scoring contract: target population, key, chunk boundary, output columns, model URI, and reconciliation expectations.
2. When more than one task, notebook, or table participates, draw the scoring dataflow map with references/scoring-dataflow-contract.md before changing code.
3. Run compute, source-snapshot, privilege, and table preflight from references/core-batch-inference-patterns.md before scoring, especially for GPU, serverless, and single-node job clusters.
4. Use references/uc-model-loading.md to resolve an alias exactly once, load only the immutable version URI, verify the loaded run ID, and preserve the requested alias separately.
5. Validate exact input and output signature names, order, types, nullability, and Spark UDF shape with references/model-signature-contract.md. Reject tensor or multi-output models unless their separate path is explicitly declared and tested.
6. For promotion or ensemble date-window work, use references/promote-ensemble-batch-loop.md and hand off to databricks-deploy-monitor when deployment and monitoring are requested.
7. Use references/safe-write-patterns.md to freeze canonical run-contract JSON, lookup strategy, ordered feature pins, and the gated staged-candidate Delta table/version/digest; reread that exact staged snapshot, validate its contract identity, exact keys, fanout, labels, and NULL policy, then serialize a DDL-complete insert-only MERGE on `(business_key, scoring_run_id)` with explicit audit recovery.
8. If a scoring, audit, or unknown-label intent/quarantine table does not exist yet, start from assets/scoring-table-ddl.sql, assets/scoring-run-audit-ddl.sql, or assets/scoring-quarantine-table-ddl.sql and keep DDL in standalone .sql files.
9. Read references/reconciliation-sql-contract.md, then generate review-only checks with scripts/emit_reconciliation_sql.py when row coverage, duplicate keys, unscorable rows, stale sources, mixed versions, or score distribution drift matters. Use only typed table and column arguments by default. Supply raw predicates or stale expressions only through the explicitly acknowledged unsafe flags, then review the emitted SQL before execution.
10. Before the prediction commit, use references/label-share-anomaly-gating.md against the latest exact one-hour closed staged-candidate window; use the same prerequisite assertion for quarantine and publication, persist one immutable count/digest-bound quarantine intent before later writes, require a nonempty digest-bound expected-label artifact plus manifest/reconciliation and complete outcome-count agreement, then pass the full documented query-row JSON to `scripts/derive_publication_decision.py --label-gate-query-results-json`. Only `ALLOW_PUBLISH` admits merge.
11. Keep the upstream training handoff and downstream output contract with the scoring code: ordered signature, source Delta version, observation timestamp, immutable model identity, threshold and label-map versions, and reconciliation checks.
12. Keep one writer per target row set or partition window. If the task becomes model serving, general Delta contract work, or training design, switch to the relevant skill instead of expanding this one.

## Deterministic Tools

| Resource | Use When | Outcome |
|----------|----------|---------|
| references/core-batch-inference-patterns.md | You need the scoring contract, chunking plan, or Spark scoring sequence | Batch-scoring workflow and guardrails |
| references/scoring-dataflow-contract.md | Multiple notebooks, tables, splits, prep tables, or consumers participate | Explicit train, score, promote, ensemble, and table-role map |
| references/promote-ensemble-batch-loop.md | A promotion, threshold, or ensemble change needs bounded scoring runs | Controlled metric review, version safety, no-rerun cleanup, and next-window loop |
| references/uc-model-loading.md | You need the right UC alias or version loading pattern | Reproducible model URI and metadata capture |
| references/model-signature-contract.md | You need exact model inputs, outputs, or Spark UDF construction | Ordered named-struct contract and explicit tensor/multi-output routing |
| references/safe-write-patterns.md | You need a safe Delta write shape | Retry-safe insert-only merge and audit recovery pattern |
| assets/scoring-table-ddl.sql | You need a standalone SQL contract for immutable prediction history | DDL-complete insert-only scoring output contract |
| assets/scoring-run-audit-ddl.sql | Scheduled production scoring needs run-level traceability | Optional Delta audit table for scoring runs |
| assets/scoring-quarantine-table-ddl.sql | Unknown candidate labels must survive review, retry, and upstream drift | DDL-complete insert-only intent and final quarantine contracts |
| references/label-share-anomaly-gating.md | A predicted class spikes, or predictions must be gated before publish | Parameterized z-score publish gate and model-version-correlated drift RCA |
| scripts/emit_reconciliation_sql.py | You need review-only before and after SQL checks | Canonically quoted structural identifiers plus target counts, scoped model checks, source freshness states, unscorable/null outcomes, honest key-ordered samples, and role inventory |
| scripts/derive_publication_decision.py | Label-gate rows must become one merge-admission decision | Exact allow-set mapping with fixed block reasons |
| scripts/business_key_contract.py | Source and staged business-key schemas or values need validation | Shared closed Spark-type set and canonical string grammar |

## References

- references/core-batch-inference-patterns.md - scoring contract, compute preflight, chunking, and Spark scoring flow
- references/scoring-dataflow-contract.md - train, score, promote, ensemble, and table-role contracts
- references/promote-ensemble-batch-loop.md - promotion, ensemble, bad-version, next-window, and no-rerun rules
- references/uc-model-loading.md - UC alias or version loading and metadata capture
- references/model-signature-contract.md - exact signature, named struct, tensor, and multi-output handling
- references/safe-write-patterns.md - safe Delta write shapes and reconciliation checks
- references/label-share-anomaly-gating.md - parameterized publish gating and label-share drift root-cause analysis
- references/reconciliation-sql-contract.md - typed reconciliation SQL inputs, explicit raw-SQL acknowledgement, output limits, and official Databricks semantics
