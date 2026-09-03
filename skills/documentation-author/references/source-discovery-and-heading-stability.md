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