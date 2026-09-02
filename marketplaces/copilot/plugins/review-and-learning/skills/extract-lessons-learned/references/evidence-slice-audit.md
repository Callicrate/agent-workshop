# Evidence Slice Audit

Use this reference when the input is a prebuilt JSONL slice, selected raw chat sessions, partial export, retrospective evidence packet, or a request to improve a skill from an evidence slice.

## Source Coverage Preflight

Before extracting lessons, record a coverage ledger:

- source families included: VS Code Copilot storage, Codex sessions, exported JSONL, existing retrospective artifacts, git history, project docs, and manually supplied slices
- source families excluded and why
- parse failures and unreadable files
- cutoff assumptions, date windows, project filters, and workspace fragments
- sessions, turns, empty sessions, prompt-only sessions, canceled sessions, rate-limited sessions, length-limit failures, no-response failures, request-failed turns, high-frustration sessions, and duplicate session keys

Use [../scripts/audit_evidence_slice.py](../scripts/audit_evidence_slice.py) for JSON/JSONL slices. Treat [../scripts/collect_project_history.py](../scripts/collect_project_history.py) as a discovery helper, not a required artifact contract. If expected generated artifacts such as `evidence_pack.md`, `signals.json`, or `prior_lessons.json` do not exist, fall back to available raw sessions or selected exports.

If the user says prior work missed history, stop lesson extraction long enough to rerun discovery or state the remaining coverage blocker.

## Streaming Auditor Input Contract

Run the auditor from the skill root. JSONL is the default input and is decoded as strict UTF-8, one record at a time:

```powershell
python -B scripts/audit_evidence_slice.py --json <slice.jsonl>
```

The auditor processes each file transactionally. An explicitly named unsupported file or directory with no eligible files is a discovery failure. A malformed record, invalid UTF-8, I/O failure, refused monolithic file, or caller-supplied size-limit violation discards that file's provisional counters. Every failure emits only a value-free `kind`, `line`, and `column` diagnostic and returns nonzero. Markdown output percent-encodes path bytes that could create formatting; JSON preserves the original path key. Do not synthesize lessons from a partial report after any failure. An eligible but empty JSONL file remains a successful zero-session slice.

Monolithic `.json` is refused by default. Enable compatibility only with both flags and choose a positive limit from the known source contract:

```powershell
python -B scripts/audit_evidence_slice.py --json --allow-monolithic-json --max-input-bytes <bytes> <slice.json>
```

`--max-input-bytes` limits bytes read from each monolithic input. It does not make monolithic parsing streaming and does not bound the decoded object graph. Prefer converting trusted exports to JSONL.

A single JSONL record is the remaining unit of memory. When the producer has a record-size contract, enforce it explicitly with `--max-record-bytes <bytes>`. The auditor has no hidden record-size threshold because valid record sizes vary by producer.

## Post-Extraction Integrity Gate

Run this gate after extraction and before synthesis, indexing, persistence, sharing, or subagent assignment. Fail closed until all four proofs pass:

1. **Raw boundary:** prove the evidence window from raw event or turn timestamps and sequence identifiers, not session-level or file metadata. If raw boundary fields are unavailable for a live or append-only source, exclude that source from the bounded corpus.
2. **Current-run contamination:** identify the current-run project and root, then apply documented deterministic exclude or crop rules. Crop a mixed parent only under a documented deterministic rule that preserves lineage and complete call/output pairs; otherwise exclude the parent or root. Mark every safely cropped parent partial, and record the applied rule and counts.
3. **Denominators and capability:** report projection, session, prompt, root, tool-call, and command-event denominators separately. Record parser capability and missingness for each source; use `unknown`, not zero, when a parser cannot observe an event class, and block only claims that depend on that class.
4. **Credential scan:** scan every emitted artifact and each target sink with a scanner that never prints matched values. Output only counts, categories, locations, or database row keys. A value-bearing hit blocks that sink: isolate it, repair the owning redactor, regenerate the output, replace the affected artifact or database records, and require a zero-result rescan before release. Only explicit raw-retention authorization may allow a named local sink to retain known values. It is not a successful redaction scan and authorizes neither another sink nor sharing.

## Failure Classification

Classify failed and partial records quantitatively before synthesis.

- Empty or no-chat session: no lesson unless empty sessions are repeated enough to be the pipeline issue.
- Prompt-only session: no assistant-output lesson; use only as evidence of request intent.
- Rate-limited, canceled, no-response, request-failed, or length-limit turn: partial evidence. Use it for process lessons about batching, retry, checkpointing, or recovery, not as evidence that the requested task was completed.
- Retry chain: keep the final successful content as primary evidence. Cite failed attempts only when they explain a reusable guardrail.
- Mid-artifact assistant output: do not treat truncated content as a finished recommendation. Preserve the partial status in limitations.

## Longitudinal Comparisons

Use this only when comparing behavior across dates, projects, or source systems.

1. Stratify results by source family and source type. Remove source copies with a documented canonical key and source-priority rule.
   Preserve root and parent lineage for independent-family counts. If lineage is unavailable, state that root-family recurrence cannot be inferred.
2. Before calling an aggregate a behavioral change, show a fixed-project or project-balanced cohort result.
   Otherwise report it only as a within-stratum observation and disclose composition changes.
3. For every compared metric, report its denominator and parser capability or missingness by source.
   Do not compare rates when one source does not observe the required field.
4. Treat lexical correction, frustration, and rate-limit counts as triage candidates.
   Promote them only after primary-turn spot checks establish what they represent.

## Evidence Independence

Use these rules whenever counts support recurrence, preference, behavior, or skill-use claims.

1. Annotate prompt origin as `organic`, `orchestrated-or-synthetic`, or `unknown` from provenance, not test-like wording alone.
   Synthetic, injected, smoke-test, and routing-probe sessions may support system-behavior lessons, but do not count them toward user-preference, style, frustration, or autonomy recurrence unless non-test primary sessions corroborate them.
2. Report both session count and normalized prompt-family count.
   For independence claims, identical prompt sequences, or the same template after removing documented volatile fields, count as one family unless independent authorship is evidenced.
   Preserve distinct outputs, tool events, context, and dates, and disclose the normalization rule and removed fields.
3. For skill-use analysis, report catalog presence, direct user request or invocation, assistant narrative, structured tool or path read, quoted or derived mention, and validation or execution as separate event classes.
   Text matches and narrative claims are not proof of a read.
   Keep call-level events keyed by source, session, and call ID; deduplicate use by canonical session, turn, skill, and event class; aggregate recurrence by root evidence family; and report missing call IDs, lineage, paths, and parser capability by source.

For each skill and compared cohort, keep the interpretation ladder explicit:

- **Availability:** the skill appears in the catalog for that cohort.
- **Static exposure:** a structured event proves the skill or its resources were read.
- **Explicit invocation intent:** the user or an attributable orchestrator explicitly requested the skill.
- **Observed application:** behavior or an execution event shows that the skill was applied to the task.
- **Observed adherence:** behavior shows that the applied guidance was followed.
- **Helpfulness or effectiveness outcome:** behavior plus an observed outcome shows whether the applied guidance helped.

Give every rung its own denominator and parser-capability or missingness statement. Mandatory or platform-injected scaffolding is not organic demand. Availability, static exposure, and invocation intent do not by themselves prove application, adherence, or effectiveness. If a source cannot observe a rung, report it as `unknown`, not zero. Infer helpfulness or effectiveness only when both behavior and outcome are observable; do not substitute catalog presence, reads, invocations, or narrative claims for that evidence.

## Skill-Improvement Analysis

When the requested output is a skill-improvement memo or live skill update:

1. Identify the target skill, instruction file, AGENTS.md, or memory surface.
2. Read the current implementation before recommending changes.
3. Compare every evidence-backed recommendation against existing content.
4. Mark each idea as `new`, `already covered`, `partial`, or `conflicts`.
5. Propose or apply concrete edits only for `new` or `partial` gaps.
6. Preserve the user's requested memo path and headings when writing an idea file.

Do not re-add existing recommendations under new wording. Do not create a new meta-skill when an existing skill or reference can absorb the reusable behavior.

## Bounded Batch Workflow

For large evidence sets:

1. Split the corpus into stable batches by source family, session date, or deterministic file order.
2. Give each batch a stable ID and record input paths, record counts, and cutoff assumptions.
3. Produce per-batch findings with stable lesson IDs and evidence paths.
4. Preserve resume metadata after each batch so rate limits, cancellations, or length limits do not erase progress.
5. Run a final synthesis pass that merges duplicate lessons, reconciles conflicts, and keeps counter-evidence.

Avoid one giant extraction prompt over an unbounded chat corpus.

## Output Requirements

Every lesson from an evidence slice, including a discarded item, needs the same
`L*` ID in its lesson heading and routing row. Each ID must appear exactly once
in both locations.

Add an implementation-audit row only for `apply`, `already-covered`,
`supersede`, and `defer`. Each required row uses the routing row's `L*` ID and
needs:

- the same exact non-discard destination as the routing row
- implementation status: `new`, `already covered`, `partial`, or `conflicts`
- concrete existing coverage; use exact `none` only for `new`
- a concrete gap for `apply` and `defer`; use exact `none` for
  `already-covered` and `supersede`

Do not add implementation-audit rows for `monitor` or `discard` because neither
assesses an edit destination. Status and disposition must agree: `apply` uses
`new` or `partial`; `already-covered` uses `already covered`; `supersede` uses
`conflicts`; `defer` uses `new`, `partial`, or `conflicts`.

The routing row needs:

- `Primary Root Evidence`: semicolon-separated
  `<root-family-id>@<artifact-reference>` entries, or exact `none` for zero roots
- one disposition: `apply`, `monitor`, `already-covered`, `supersede`, `defer`, or `discard`
- exact destination or `discard`
- `Independent Root Families`: a nonnegative integer count of independent
  primary-evidence family IDs; it must equal the unique structured-entry count
- one-off correctness impact, or `none`
- prior-synthesis match: an exact artifact-and-lesson reference, or `none`
- prior-synthesis decision: `none`, `dedupe`, or `supersede`
- confidence
- limitation or counter-evidence
- concrete next action
- observable validation gate
- a concrete `when:` trigger for `defer`, or exact `n/a` for every other disposition

Artifact references use a concrete file path with an optional `#anchor` or
`::test`, or a named `artifact:`, `check:`, `query:`, or `test:` reference.
Family IDs are lowercase stable identifiers. Duplicate family IDs are invalid.
Prior lessons paths and the exact prior-synthesis match artifact are invalid
primary-root evidence.

Prior synthesis is evidence that an idea was already synthesized. Use it only to
deduplicate or supersede a lesson. Never count a prior lessons document, report,
or summary as an independent root family.

An `apply` with two or more verified roots uses exact `none` for correctness
impact. A one-root `apply` requires this exact machine grammar (escape
the pipe as `\|` inside a Markdown table cell):

```text
correctness:<kind>:<affected-reference> | evidence:<evidence-kind>:<reference>
```

Correctness kind is one of `incorrect-output`, `data-loss`, `contract-break`,
`runtime-failure`, or `workflow-blocker`. Evidence kind is one of `test`,
`query`, `artifact`, `command`, or `trace`. References are 1-160 characters,
start with an alphanumeric character, and contain only letters, digits, `.`,
`_`, `/`, `#`, `:`, `@`, `=`, or `-`; each reference contains at least one of
the separator characters `.`, `/`, `#`, `:`, `@`, `=`, or `-`. Free-text
substitutes are invalid.

`Action Kind` and `Action` use this matrix:

- `apply`: `validate`, `test`, `edit`, `create`, or `document`; action begins with the same word
- `monitor`: `observe`; action is exact
  `observe:<evidence-kind>:<reference>` using the closed evidence-kind and
  bounded-reference grammar
- `already-covered`, `supersede`, and `discard`: `none`; action is exact `none`
- `defer`: `defer`; action is exact `defer until trigger`

A defer trigger uses this exact grammar:

```text
when:<event-kind>:<reference> | evidence:<evidence-kind>:<reference>
```

Event kind is one of `recurrence`, `test-failure`, `incident`,
`artifact-change`, or `threshold`. Evidence uses the same closed evidence-kind
enum and bounded reference grammar as correctness impact. Keep the validation
gate separate from trigger evidence.

Every validation gate names an observable pass condition. Generic `done` or
`later` is invalid.

Any unresolved `{placeholder}` outside a fenced example blocks the report.

`discard` is valid when the material is empty, incident-only, unsupported,
already covered without a new gap, or too project-specific for reusable
guidance.

From the skill root, run:

```powershell
python -B scripts/validate_lessons_report.py <report.md>
```

The command emits a stable JSON envelope and returns nonzero for missing or
duplicate IDs, placeholders, incomplete routes, inconsistent prior-synthesis
decisions, invalid defer triggers, or forbidden edit actions.
