# Reconciliation SQL Generator Contract

Use `scripts/emit_reconciliation_sql.py` to generate review-only checks after the scoring population, table roles, and single equality key are already known.
It prints SQL to stdout only.
It neither connects to Databricks nor proves that source rows reconcile to target rows.

## Typed Structural Inputs

- `--table` and `--source_table` accept exactly three Unity Catalog segments.
- Identifier input is bounded to 773 characters and each decoded segment to 255 characters before SQL rendering.
- Every column option accepts exactly one segment, including `--key_col`, score, label, window, source evidence, reason, timestamp, and model metadata columns.
- The parser splits only outside paired backticks, then emits every supplied segment in canonical backticks and escapes an embedded backtick by doubling it.
- It rejects qualified columns, malformed or unpaired backticks, comments, semicolons, controls, newlines, whitespace or punctuation outside delimiters, and UNC or device-style paths.
- `--table_role` accepts only `source=<table>` and `target=<table>` and must agree with the corresponding `--source_table` or `--table` argument.

Databricks supports regular and backtick-delimited identifiers, and documents doubled backticks for an embedded backtick: [Identifiers](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-identifiers).

## Expert SQL Escape Hatch

Predicates and timestamp expressions are not safely parseable as identifiers.
Use them only when a knowledgeable operator has authored and reviewed the SQL:

```text
--unsafe-source-where-sql "..."
--unsafe-target-where-sql "..."
--unsafe-stale-after-sql "..."
--allow-unsafe-sql-fragments
```

The acknowledgement is mandatory whenever any unsafe flag is present.
The generated header says that these fragments are copied verbatim from trusted expert input, must contain no secrets, and require human review.
The script deliberately does not pretend to sanitize arbitrary SQL.
The legacy `--source_where`, `--target_where`, and `--stale_after_expr` aliases are deprecated and require the same acknowledgement.
Each acknowledged fragment is bounded to 4,096 characters. The generator rejects more than 64 repeated source-null or sample-order columns, duplicate repeated columns, more than 32,768 total argument characters, and any request whose conservative pre-render estimate can exceed 131,072 output characters.

`WHERE` accepts a Boolean expression, so a raw predicate has the full power and risk of the SQL dialect: [WHERE clause](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-where).

## Reading The Generated Checks

- Staleness returns `source_row_count`, `nonnull_timestamp_count`, `max_source_timestamp`, and one of `empty_source`, `missing_timestamps`, `stale`, or `fresh`.
  A `NULL` maximum is not reported as fresh.
- Model-version checks restrict both unresolved and version-distribution queries to `score_col IS NOT NULL` and the selected target predicate.
- Score-distribution counts and aggregates use that same `score_col IS NOT NULL` population and compose the selected target predicate identically; NULL/unscorable outcomes remain in the separate outcome accounting check.
- The target outcome check treats `NULL`, empty, and whitespace-only reason values as missing by normalizing with `NULLIF(TRIM(CAST(reason AS STRING)), '')`.
  It reports `unscorable_count` for a NULL score with a normalized non-NULL reason, separately from `unexpected_null_score_count`, defined as a NULL score with no normalized reason, and labels the target population scope.
- The duplicate diagnostic counts NULL keys separately and checks duplicate values only for one non-NULL equality key.
  It is not a composite-key, source-to-target-cardinality, or general `MERGE` proof.
- The generator intentionally emits no source-to-target join or cardinality assertion.
  Add one only when the scoring contract supplies an explicit, reviewed equality condition and the owners confirm the intended cardinality.

Databricks documents that `COUNT(*)` returns zero for an empty input while aggregate values such as `MAX` return NULL, and that predicates are satisfied only when they evaluate TRUE: [NULL semantics](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-null-semantics).
Databricks `MERGE` can fail when more than one source row matches a target row, and the exact matching behavior also depends on the merge condition and runtime version: [MERGE INTO](https://docs.databricks.com/aws/en/sql/language-manual/delta-merge-into).

## Sampling And Scope

`--sample_n` is positive and defaults to 20.
It is capped at 1,000 unless `--allow-large-sample` is present, and at 10,000 even with that acknowledgement.
Without an explicit uniqueness attestation, samples use `ORDER BY <validated key> ASC NULLS LAST` and are labelled a bounded key-ordered sample, nondeterministic among duplicate keys.
Never infer uniqueness from the duplicate diagnostic.
To emit a caller-attested total order, provide `--attest-sample-order-unique` and optionally repeat `--sample_order_col <validated-column>` for each tie-breaker.
Tie-breakers require the attestation and the caller attests that the key plus every listed tie-breaker is unique; the generator does not prove the claim.
Samples never use `RAND()`.
Databricks recommends pairing `LIMIT` with `ORDER BY` for deterministic results: [LIMIT clause](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-limit).

Source-only options require `--source_table`.
This includes source key, source evidence columns, update timestamp, source predicate, and stale-after expression.
Use `--no-model-version-check` only when model metadata is unavailable by contract; the output makes that omission visible and rejects explicitly supplied model-version or model-run-ID columns.
