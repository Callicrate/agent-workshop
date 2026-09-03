---
name: extract-lessons-learned
description: "Use when reviewing history, chats, post-mortems, or completed work for lessons; routes lessons to docs or skills. Do not trigger for live debugging, code review, or non-lessons AGENTS.md work."
metadata:
  short-description: Extract reusable project lessons.
---

# Extract Lessons Learned


## When to Use

- Reviewing a completed or troubled project to capture wins, mistakes, and repeatable fixes
- Writing a post-mortem or retrospective from chat history, git history, and project docs
- Routing project-specific lessons into AGENTS.md, skills, or instruction files
- Auditing a bounded evidence slice and producing targeted skill-improvement ideas
- Comparing retrospective recommendations against existing skills before applying changes

## Modes

- `project-retrospective`: write `docs/lessons-learned/{YYYY-MM-DD}/lessons-learned.md` unless the user gives a different path.
- `evidence-slice-audit`: write the user-requested path and preserve the user-requested headings or schema.
- `skill-improvement-analysis`: compare a target skill, instruction file, AGENTS.md, or memory surface against an evidence slice and write actionable improvement ideas.

Use the mode implied by the user's deliverable. An explicit output path, heading set, or target skill overrides the default project-retrospective path.

## When NOT to Use

- Building a reusable skill from scratch without first extracting project lessons
- Creating a new AGENTS.md file without a lessons-mining step
- General debugging or code review without a retrospective deliverable

## Hard Rules

- In `project-retrospective` mode, save the final document to `./docs/lessons-learned/{YYYY-MM-DD}/lessons-learned.md`. Create the folder if needed.
- In `evidence-slice-audit` or `skill-improvement-analysis` mode, save to the user-requested path and do not force the project-retrospective folder.
- Do not use existing `docs/lessons-learned/` files as evidence for new lessons. Read or update them only when they are the user-requested target artifact, and label their contents as prior synthesis rather than primary evidence.
- Keep only evidence-backed lessons. Discard one-off incidents that do not produce reusable guidance.
- Do not assume generated retrospective artifact names exist. Treat retrospective tooling as discovery and bounding, not as a required artifact contract.
- Empty or no-chat sessions produce no lesson unless repeated emptiness is itself the pipeline issue.

The collector script excludes `docs/lessons-learned/` automatically for evidence collection. Apply the same source-evidence rule to manual review unless the user specifically asked to edit an existing lesson file.

## Workflow

1. Identify the mode and deliverable path. If the user provides an evidence slice, prebuilt JSONL, exported sessions, or a requested memo path, use that contract instead of the default retrospective path.
2. Run a source coverage preflight. Use [scripts/collect_project_history.py](scripts/collect_project_history.py) for project history and [scripts/audit_evidence_slice.py](scripts/audit_evidence_slice.py) for streaming JSONL evidence slices. The auditor refuses monolithic JSON unless the caller explicitly enables and bounds it as described in [the evidence-slice audit reference](references/evidence-slice-audit.md#streaming-auditor-input-contract). If either tool rejects an input or cannot see an expected source family, stop before extracting lessons.
3. Run the [post-extraction integrity gate](references/evidence-slice-audit.md#post-extraction-integrity-gate). Before synthesis, indexing, persistence, sharing, or subagent assignment, fail closed unless raw boundaries, current-run contamination handling, denominator capabilities, and no-value-output credential scans are proven.
4. Record a short coverage ledger: included source families, excluded locations and why, parse failures, cutoff assumptions, session count, turn count, empty sessions, prompt-only sessions, canceled sessions, rate-limited sessions, length-limit failures, no-response failures, high-frustration sessions, and duplicate session keys.
   For comparisons across time, projects, or source systems, apply the [longitudinal comparison guardrail](references/evidence-slice-audit.md#longitudinal-comparisons) before claiming behavioral change.
   When counting recurrence or skill use, apply the [evidence-independence rules](references/evidence-slice-audit.md#evidence-independence).
5. Classify failure-heavy evidence before qualitative synthesis. Treat rate-limited, canceled, no-response, request-failed, and length-limit turns as partial evidence and use them only for process lessons about reliability, batching, or recovery. For retry chains, keep the final successful content and cite failures only when they explain a reusable guardrail.
6. Read the collected evidence, then inspect only the directly relevant source files needed to confirm each lesson. For skill-improvement analysis, audit the destination skill, instruction file, AGENTS.md, or memory first.
7. Route every lesson by content with [references/content-first-routing.md](references/content-first-routing.md), then apply [references/routing-guide.md](references/routing-guide.md). Give every lesson one terminal disposition, exact destination or `discard`, structured primary-root evidence entries, derived integer root-family count, closed-enum correctness or defer evidence when required, prior-synthesis decision, compatible `Action Kind`, matching action, and observable validation gate. Prior synthesis supports only deduplication or supersession; never count it as an independent root family.
8. Run an implementation audit for `apply`, `already-covered`, `supersede`, and `defer`. Use the lesson's `L*` ID, mark the destination `new`, `already covered`, `partial`, or `conflicts`, and record exact existing coverage and gap. Do not add audit rows for `monitor` or `discard`. Only `new` or `partial` `apply` routes propose immediate edits.
9. For large corpora, split evidence into stable bounded batches, produce per-slice findings with stable IDs, preserve resume metadata, and run a final duplicate-detection synthesis pass.
10. Write the retrospective or audit memo with [references/output-template.md](references/output-template.md), unless the user requested a different heading set. Give each lesson a stable ID and keep observation, evidence, recommendation, confidence, and limitation separate.
11. From the skill root, run `python -B scripts/validate_lessons_report.py <report.md>`. Resolve every diagnostic before treating the report as complete.
12. Save the completed document to the mode-specific output path.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/collect_project_history.py](scripts/collect_project_history.py) | You need a deterministic inventory of docs, git history, and chat-session evidence | Stable source list before qualitative review |
| [scripts/audit_evidence_slice.py](scripts/audit_evidence_slice.py) | You have JSONL session slices, raw chat exports, or partial retrospective evidence | Streaming coverage, failure classification, duplicate keys, and phrase counts before synthesis; any rejected input returns nonzero |
| [scripts/validate_lessons_report.py](scripts/validate_lessons_report.py) | You have written a lessons report | Stable JSON validation of IDs, structured recurrence, audit coverage, prior-synthesis decisions, and terminal routing dispositions |
| [references/output-template.md](references/output-template.md) | You are writing the final retrospective | Consistent lessons-learned structure |

## References

- [references/output-template.md](references/output-template.md) — final document structure
- [references/evidence-slice-audit.md](references/evidence-slice-audit.md) — JSONL slice, partial-session, and skill-improvement audit workflow
- [references/routing-guide.md](references/routing-guide.md) — decision tree for where to route each lesson
- [references/content-first-routing.md](references/content-first-routing.md) — classify lessons by content rather than packet family
