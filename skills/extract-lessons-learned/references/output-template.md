# Lessons Learned: {Project Name}

Delete any lesson section with no evidence. Use lesson IDs `L1`, `L2`, `L3`,
... and reuse every ID exactly once in `## Routing`. Add implementation-audit
rows only for `apply`, `already-covered`, `supersede`, and `defer`.

This source template intentionally contains unresolved brace placeholders and
must fail validation until every retained placeholder is replaced or removed.

## Executive Summary

- Project: {short project description}
- Review date: {YYYY-MM-DD}
- Review window: {what history was examined}
- Mode: {project-retrospective | evidence-slice-audit | skill-improvement-analysis}
- Outcome: {what shipped, stalled, or changed}
- Top takeaways: {3-5 short takeaways}

## Source Coverage

- Included sources: {chat stores, exported files, git history, docs, manual slices}
- Excluded sources: {locations skipped and why}
- Parse failures: {files or records that could not be read}
- Counts: {sessions, turns, empty sessions, prompt-only sessions, error-bearing turns}
- Error categories: {rate-limited, canceled, length-limit, no-response, request-failed counts}
- Cutoff assumptions: {date windows, workspace filters, project filters}
- Integrity gate: {pass or blocked; raw boundary proof; contamination exclude/crop rule and partial-parent count; separate denominators and parser unknowns; credential scan counts/categories/locations or row keys only; blocked sinks and zero-result rescan status}

Delete this section only when the deliverable explicitly provides its own equivalent coverage ledger.

## What Worked Well

### [L1] {Win or repeatable good decision}

- Observation: {what worked}
- Evidence: {git history, chat history, docs, or source evidence}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {what to repeat}

## Architecture and Design Decisions

### [L2] {Decision or design pattern}

- Observation: {what was decided and what it changed}
- Evidence: {commits, docs, code, or discussion evidence}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {what to repeat, revise, or standardize}

## Pain Points and Time Sinks

Rank by impact.

### [L3] {Pain point}

- Impact: {hours, days, or iteration count}
- Root cause: {underlying issue}
- Evidence: {retries, reverts, repeated fixes, or failure history}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {how to prevent or shorten this next time}

## Repeated Corrections

### [L4] {Repeated mistake or convention violation}

- Observation: {what had to be corrected more than once}
- Evidence: {repeated prompts, edits, or review comments}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {what rule or guidance would stop the repetition}

## Late Discoveries

### [L5] {Important thing learned too late}

- Observation: {what was learned late}
- Evidence: {when it became clear and what it would have changed}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {what to front-load next time}

## Deferred or Abandoned Work

### [L6] {Deferred or abandoned item}

- Observation: {what was not finished}
- Evidence: {why it stopped or was deprioritized}
- Confidence: {high | medium | low, with reason}
- Limitations / Counter-evidence: {what weakens or scopes the lesson}
- Recommendation: {what would need to be true to pick it up again}

## Implementation Audit

Use this section only for `apply`, `already-covered`, `supersede`, and `defer`.
Omit `monitor` and `discard` rows. Each destination must equal the routing
destination. Use exact `none` only for new existing coverage or a no-edit gap.

| Lesson ID | Destination Checked | Status | Existing Coverage | Gap To Patch |
|-----------|---------------------|--------|-------------------|--------------|
| `L1` | `skills/extract-lessons-learned/SKILL.md` | `new` | `none` | add a workflow validation gate |
| `L3` | `instructions/python-general.instructions.md` | `already covered` | current rule already requires the behavior | `none` |
| `L4` | `docs/lessons-learned/current.md` | `conflicts` | prior synthesis contradicts current primary evidence | `none` |
| `L5` | `skills/extract-lessons-learned/references/routing-guide.md` | `partial` | workflow exists but omits stable call identifiers | add call identifier validation after capability ships |

## Recommendations for Future Projects

1. {Specific action with rationale}
2. {Next action}

## Routing

Use [content-first-routing.md](content-first-routing.md), then
[routing-guide.md](routing-guide.md). Every lesson ID above should appear here
exactly once, including discarded items. Allowed dispositions are `apply`,
`monitor`, `already-covered`, `supersede`, `defer`, and `discard`.

`Primary Root Evidence` uses semicolon-separated
`<root-family-id>@<artifact-reference>` entries. The integer root count must
equal the unique family-ID count. Prior synthesis is never primary evidence.

A one-root `apply` uses `correctness:<kind>:<affected-reference> \|
evidence:<evidence-kind>:<reference>`; an apply with at least two roots uses
exact `none`. Correctness kind is `incorrect-output`, `data-loss`,
`contract-break`, `runtime-failure`, or `workflow-blocker`.

A defer trigger uses `when:<event-kind>:<reference> \|
evidence:<evidence-kind>:<reference>`, where event kind is `recurrence`,
`test-failure`, `incident`, `artifact-change`, or `threshold`. Evidence kind for
both grammars is `test`, `query`, `artifact`, `command`, or `trace`. References
are bounded tokens using letters, digits, `.`, `_`, `/`, `#`, `:`, `@`, `=`, or
`-`, with at least one separator character. Escape the internal pipe in
Markdown tables.

`Action Kind` is `validate`, `test`, `edit`, `create`, or `document` for apply;
`observe` for monitor; `none` for already-covered, supersede, and discard; and
`defer` for defer. Monitor action is exact
`observe:<evidence-kind>:<reference>`. Other actions begin with the same kind,
except `none` is exact `none` and defer is exact `defer until trigger`. Every
gate names an observable pass condition; `done` and `later` are invalid.

| Lesson ID | Disposition | Destination | Primary Root Evidence | Independent Root Families | One-off Correctness Impact | Prior Synthesis Match | Prior Synthesis Decision | Confidence | Limitations / Counter-evidence | Action Kind | Action | Validation Gate | Defer Trigger | Why |
|-----------|-------------|-------------|-----------------------|---------------------------|----------------------------|-----------------------|--------------------------|------------|--------------------------------|-------------|--------|-----------------|---------------|-----|
| `L1` | `apply` | `skills/extract-lessons-learned/SKILL.md` | `root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3` | `2` | `none` | `none` | `none` | high | no contrary successful path observed | validate | validate the completed lessons report | strict validation exits zero and two fixtures pass | `n/a` | repeated workflow gap |
| `L2` | `monitor` | `docs/retrospective-monitor.md` | `root-c@sessions/root-c.jsonl#turn-5` | `1` | `none` | `none` | `none` | low | only one independent root observed | observe | observe:artifact:reports/root-family-count.json | two independent root families are recorded | `n/a` | recurrence is not established |
| `L3` | `already-covered` | `instructions/python-general.instructions.md` | `root-d@sessions/root-d.jsonl#turn-2` | `1` | `none` | `docs/lessons-learned/previous.md#L7` | `dedupe` | medium | one root but implementation already covers it | none | none | existing rule path is recorded | `n/a` | current implementation owns the behavior |
| `L4` | `supersede` | `docs/lessons-learned/current.md` | `root-e@sessions/root-e.jsonl#turn-8; root-f@sessions/root-f.jsonl#turn-4` | `2` | `none` | `docs/lessons-learned/previous.md#L4` | `supersede` | high | older synthesis used incomplete evidence | none | none | superseding lesson citation is recorded | `n/a` | current evidence replaces old synthesis |
| `L5` | `defer` | `skills/extract-lessons-learned/references/routing-guide.md` | `root-g@sessions/root-g.jsonl#turn-6; root-h@sessions/root-h.jsonl#turn-9` | `2` | `none` | `none` | `none` | medium | upstream capability is unavailable | defer | defer until trigger | focused fixture exits zero after trigger | when:artifact-change:schemas/call-ids.json \| evidence:artifact:schemas/call-ids.json | useful gap has an unresolved prerequisite |
| `L6` | `discard` | `discard` | `root-i@sessions/root-i.jsonl#turn-1` | `1` | `none` | `none` | `none` | low | incident-only fact with no reusable guidance | none | none | discard reason and source are recorded | `n/a` | no reusable behavior exists |

From the skill root, run
`python -B scripts/validate_lessons_report.py <report.md>` and resolve every
diagnostic before treating the report as complete.
