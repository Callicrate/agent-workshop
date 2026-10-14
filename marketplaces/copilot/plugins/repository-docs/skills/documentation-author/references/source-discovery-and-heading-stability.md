# Source Discovery And Heading Stability

Use this reference for large documentation updates, feature inventories, iterative reports, and docs that other tools link to.

## Source Discovery

Before writing, identify the source surface:

- explicit files named by the user
- linked source files
- adjacent docs
- scripts or schemas that define the contract
- validators, CLIs, table inventories, or source-of-truth modules that already define behavior
- generated artifacts used as evidence
- prior reports when consolidation is requested

For broad inventories, split discovery by domain or folder and merge results before drafting.
Do not infer feature coverage from file names alone.

## Evidence-Ledger Tables

For a chronology, claim audit, or consolidation that depends on source records, build a compact ledger before drafting the final table. Keep one row per claimed event or result and include:

- event date and time, with the precision supplied by evidence;
- the smallest exact supporting wording that distinguishes the claim;
- the authoritative source path and revision or retrieval context when history matters;
- source class and claim strength, such as direct statement, derived record, or inference;
- redaction state, such as visible, redacted in source, or derived from redacted material; and
- corroborating sources, conflicts, and duplicate occurrences.

Order rows by the claimed event date, earliest first. Keep partial dates partial and put unknown dates after dated rows. A source timestamp is not an event date unless the source says so. Do not invent a time, date, name, or redacted content to make the table complete.

Treat copied, derived, or repeated wording as one source lineage: cite the earliest authoritative record and list later occurrences as corroboration. Keep independently authored or conflicting sources distinct. Separate direct wording from the conclusion drawn from it, and preserve sensitive redactions rather than reconstructing them.

## Stable Headings

When updating existing docs, preserve headings and anchors unless the user asked for a restructure.
If a heading changes, update internal links in the same edit.

For new long docs:

- use one `#` title
- include a table of contents
- avoid duplicate heading names
- keep implementation queues and evidence links in predictable sections

## Final Pass

Verify:

- links resolve
- tree references still match the actual folder layout after moves or renames
- no placeholder sections remain
- terminology matches source wording
- recommendations are separated from evidence
- planned work, hypotheses, and completed findings are labeled distinctly

## First Usable Workflow

For concept, architecture, or project-framing docs, include a visible first usable workflow before deep architecture. Show the smallest executable slice, inputs, command or action, expected output, and how to inspect the result.

Use architecture depth after the reader can understand how to run or reason about the first slice.
