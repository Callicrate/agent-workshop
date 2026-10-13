# High-Impact Claim Ledger

Use this ledger for findings or recommendations that would change implementation priority, operational policy, security posture, partner messaging, or skill guidance.

## Required Fields

Every high-impact claim needs:

- exact claim text
- source quote or artifact path
- evidence for
- evidence against or missing evidence
- scope
- severity
- confidence
- destination or owner
- recommended action
- validation or follow-up needed

## Promotion Rules

Promote a claim only when the evidence supports the scope.
If the source proves a narrow incident, do not write a broad rule without separate repeated evidence.

If counter-evidence exists, include it in the finding rather than burying it in caveats.

## Destination Discipline

Choose one destination per claim unless multiple destinations are independently necessary:

- source document edit
- code or test change
- AGENTS.md guidance
- skill update
- user memory candidate
- discard or monitor

Do not create a new skill from a single incident when an existing skill can absorb the guidance.