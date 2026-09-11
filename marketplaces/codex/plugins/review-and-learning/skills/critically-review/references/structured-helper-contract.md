# Structured Helper Contract

The `validate_findings.py`, `merge_findings.py`, and `render_report.py` helpers treat JSON reports and findings lists as untrusted data.

## Validation and Limits

- Inputs are strict UTF-8 JSON regular files, capped at 1 MiB.
- The parser rejects JSON deeper than 32 containers, more than 10,000 values, or strings longer than 10,000 characters before schema acceptance.
- Reports contain at most 500 findings; a finding contains at most 50 evidence records. Canonical fields reject unknown properties, use declared enums, and require unique `F-<digits>` finding IDs.
- `merge_findings.py` validates every input report or standalone findings list, then validates its merged report before writing. `render_report.py` validates its report before writing.
- CLI failures are value-free: default `ERROR: <code>` text on stderr, or `--json-errors` for `{ "ok": false, "error": { "code": "<code>" } }`. Neither form includes a traceback or caller-provided values.

## Merge Semantics

- A finding is deduplicated only if its canonical JSON is byte-identical after removing its local `id` and `origins` fields.
- Variants remain separate when any semantic field differs, including severity, confidence, analysis, or evidence.
- Exact duplicates combine all origin records. The helper uses a lexicographic representative only inside that exact-duplicate bucket, then assigns canonical IDs after a stable severity-and-content sort.
- Full source-report metadata is retained in `source_reports`. If input documents or assessments differ, the top-level report uses an explicit merged marker rather than silently promoting one input's conclusion.
- `research_performed` is the logical OR of full input reports. Findings lists carry no research claim and cannot make it true.

## Output Safety and Rendering

- All four writers, including `init_review.py` and `surface_snapshot.py`, use one output primitive. Output files are create-only by default. POSIX publishes from a private temporary file through a directory-handle-relative no-overwrite link; Windows creates the final name through the held directory handle after the payload is prepared. Neither path overwrites an existing name.
- `--force` replaces an existing output only on Windows. It records the regular target's identity, size, last-write, change state, and a transient in-memory SHA-256 of its bounded bytes, then opens that exact leaf relative to the held directory handle without write or delete sharing, acquires an exclusive range lock, and recomputes all of those checks immediately before truncation. The target's existing bytes are capped at 1 MiB; oversize, unreadable, or changed targets fail closed. The digest and target content are never persisted, logged, or emitted. Any growth, shrink, same-size rewrite (including one with restored timestamps), replacement, alias, or link-count change fails closed without publishing. Restoring identical content may proceed. Other platforms return `output-force-unsupported` rather than claim a safe forced replacement.
- An output cannot be an input, hardlink, symlink, Windows reparse point, or path beneath a reparse point. The primitive holds a no-reparse ancestor chain for the full write window, rejects an opened file whose link count is not exactly one, and compares the opened identity with every input immediately before truncation or publication.
- Output is capped at 1 MiB.
- The renderer has a fixed heading vocabulary. Every report value, including evidence, analysis, title, HTML, links, headings, and control characters, is emitted as JSON inside a dynamically sized literal fence. Report content cannot introduce executable Markdown structure or fetch a remote resource.
