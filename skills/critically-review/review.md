# Report rendering fix review

## Evidence and intended correction

| Evidence | Failure / current coverage | Fix destination | Confidence | Validation |
|---|---|---|---|---|
| RubyHack `report.md` contains document objects and finding objects in `text` fences. `render_report.py` calls `json.dumps` for every report value. | Renderer produces a serialized data dump, not a reader-facing report. | Renderer | High | Readability regression and rerender actual findings |
| `test_render_uses_only_fixed_headings_and_inert_literals` requires literal fences. | Safety test enforces the presentation bug. | Existing test suite | High | Replace with readable-output and hostile-input regressions |
| Workflow step 8 prescribes `overall_assessment.*` fields for every review. | Prose guidance exists but JSON rules are not scoped to structured output. | Reporting reference, workflow, template | High | Readback and strict validation |

Keep schemas, CLI arguments, dependencies, input validation, and safe file-writing behavior unchanged. Escape untrusted Markdown/HTML rather than serializing prose. Keep JSON as optional machine-readable working data.

## Validation

- Replaced JSON-fence rendering with Markdown headings, prose, lists, claim blockquotes, and readable labels. Verdict is first; empty optional sections are omitted. All schema fields, including source-report metadata and finding provenance, remain represented.
- Updated the workflow, reporting reference, helper contract, and template. JSON field constraints now apply only to structured output. No description, schema, runtime dependency, CLI flag, merge, or file-write primitive changes.
- Strict skill validation and renderer `--help`: passed.
- Full unittest suite: 24 tests, 23 passed, 1 skipped because this Windows environment cannot create the test symlink. Existing junction and hardlink checks passed.
- New regressions cover readable output, optional metadata, empty sections, multiline prose, Unicode, hostile Markdown/HTML and control characters. The optional installed CommonMark parser test ran and passed; it is not a runtime dependency.
- Regenerated the reported RubyHack `report.md` from its existing seven findings with the canonical renderer. Readback matches renderer output, includes all seven finding headings, and parses without code fences, code blocks, or raw HTML blocks. This was a formatting repair, not a new factual review.
- `git diff --check`: passed. Source edits stay under `skills/critically-review/`.

## Limits

Fresh-agent interpretation testing and independent review were attempted but blocked by Codex refresh-token and Copilot authentication failures. Deterministic rendering is tested; broader model compliance is not claimed.

Compared canonical renderer with the generated Codex marketplace copy: the marketplace copy is stale. No installed or generated plugin copies were edited, committed, or published. The canonical source named by the user is fixed; plugin consumers still need the normal sync/publish/refresh workflow.
