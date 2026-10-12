# Workflow: Evidence-Backed Skill Update

Use this when a retrospective slice, user correction, failure transcript, model review, or repeated bug report should become a skill rule.

## Steps

### 1. Build A Claim Ledger

Before editing, write a compact ledger with one row per candidate lesson:

| Evidence quote | Repeated failure | Current coverage | Proposed destination | Confidence | Validation check |
|----------------|------------------|------------------|----------------------|------------|------------------|

Use exact quotes or source references. Mark generated summaries, heuristic packs, and truncated logs as partial evidence.

### 2. Audit Current Coverage

Read the target skill's `SKILL.md` and relevant references before writing a rule.

Classify coverage:

- `covered`: current guidance already prevents the failure.
- `covered-but-hidden`: guidance exists but discovery, routing, or validation makes it easy to miss.
- `partial`: guidance covers the principle but not the recurring failure shape.
- `absent`: no current rule, helper, or validation covers it.

Do not add a duplicate rule if current guidance already covers the failure under different wording. Improve routing, examples, or validation instead.

### 3. Route Each Lesson

Choose exactly one primary destination:

- target skill `SKILL.md`
- target skill reference file
- target skill script, schema, asset, or template
- another owning skill
- instruction file
- repository guidance or AGENTS.md
- memory candidate

Route by the behavior being changed, not by the evidence file name. If another skill owns the behavior, hand off there and keep only a concise cross-reference if `skill-author` needs it.

### 4. Patch Small, Enforceable Rules

Prefer these changes, in order:

- deterministic validator or inventory helper
- compact checklist in a focused reference
- bad/good pattern for recurring mistakes
- short routing rule in `SKILL.md`

Avoid long evidence narratives in skill files. The evidence belongs in the working ledger or retrospective report, not in the operational skill unless a quote is needed as a minimal example.

### 5. Validate The Rule

Every promoted lesson needs a validation check:

- strict skill validation
- script syntax or smoke run
- link validation
- inventory or overlap audit
- mirror parity check
- source readback proving the target text exists

## Optional Artifact Fallback

Do not assume helper outputs exist. Evidence packs, `signals.json`, prior lessons, selected-session files, rendered reports, or inventories are preferred aids, not required inputs unless the user says otherwise.

When helper artifacts are missing, proceed from available primary sources and list the limitation. Do not invent evidence files or fail the update solely because optional helpers are absent.
