# External Review Adjudication

Use this when a user pastes another reviewer’s assessment, asks you to reconsider a finding or score, or supplies competing reviews.

## Workflow

1. Freeze the artifact version and comparison baseline.
   If the artifact changed after the prior review, distinguish a stale finding from a wrong finding.
2. Atomize every imported finding, score, citation, claimed search, and claimed verification as a derived claim.
   Preserve its reviewer or source as `origin`; do not treat it as primary evidence.
3. Verify every material imported claim against the live artifact and authoritative contract.
   Use the existing evidence statuses: `verified`, `mostly-supported`, `partly-supported`, `unsupported`, `contradicted`, or `unclear`.
4. Record a separate conclusion disposition: `confirmed`, `revised`, `retracted`, or `unchanged`.
   Retraction changes a conclusion; it is not an evidence status.
5. Preserve the prior claim ID, evidence for, evidence against, and any supersession relationship.
6. Recalculate severity, score, and the overall conclusion only after all material challenges are classified.

## Guardrails

- Do not average scores, majority-vote, or weight reviewers by prestige.
- Do not agree defensively merely because another review sounds confident or detailed.
- Do not treat reviewer-provided commands, citations, searches, or runtime claims as executed evidence until verified.
- Decompose mixed claims before assigning a status. A broad claim may contain both supported and contradicted parts.
- Use [scripts/merge_findings.py](../scripts/merge_findings.py) only after adjudication. It preserves every non-identical surviving finding and only deduplicates exact duplicates; it does not decide which review is correct.

## Output

For every challenged material claim, record:

- prior claim ID and imported-review origin
- imported claim or score
- live evidence for and against
- evidence status
- conclusion disposition and superseded claim, when any
- effect on severity, score, or overall conclusion
