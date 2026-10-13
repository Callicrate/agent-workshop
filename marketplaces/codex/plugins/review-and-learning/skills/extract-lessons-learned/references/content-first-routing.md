# Content-First Routing

Use this reference when a retrospective packet mixes chat history, reports, prompts, skills, instructions, and generated artifacts.

## Routing Rule

Classify each lesson by what action it should change, not by the folder or packet where it appeared.

Apply this before the general routing guide whenever the packet is a skill review, retrospective review, generated analysis, evidence slice, or mixed artifact bundle.

Examples:

- skill invocation, workflow, or validation issue -> skill update
- repository convention or command surface -> AGENTS.md update
- user-wide preference -> memory candidate
- one-off project fact -> project docs or status note
- unsupported or low-signal observation -> discard or monitor

## Self-Referential Reviews

When the artifact is a review of skills or agent behavior, keep the review as evidence and route each recommendation to the specific skill, instruction, or memory surface it changes.

Do not create a new meta-skill when existing skills can absorb the behavior.

Before proposing a new skill candidate, check existing skill ownership. Mark the idea as `new`, `already covered`, `partial`, or `conflicts` against the closest existing owner. Only `new` ownerless workflows should become new skill candidates.

## Implementation Audit

For skill or instruction changes, audit the current implementation before routing:

- destination checked
- existing coverage found
- gap status: `new`, `already covered`, `partial`, or `conflicts`
- concrete edit only for `new` or `partial`
- no edit for `already covered` unless the existing guidance is misleading
- explicit conflict note when evidence contradicts current guidance

## Evidence Standard

Every routed lesson needs:

- source path or case file
- session key when available
- observed behavior
- reusable recommendation
- destination
- confidence
- counter-evidence or limitation
- implementation audit status when a skill, instruction, AGENTS.md, or memory update is proposed