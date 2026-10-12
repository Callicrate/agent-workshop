# Persona Overlay Composition

Use this when a persona is injected into an agent's context **together with** a
fixed role, or when generating personas that must compose over a separate set of
job roles without corrupting them. A persona is an overlay, not a standalone agent.

## Roles and Personas Are Different Classes

| Artifact | Owns | Injected |
|----------|------|----------|
| Role | The floor: identity, method, boundaries, inputs/outputs, completion, escalation | Always |
| Persona | A lens over that floor: emphasis, attention, heuristics, values, voice | With a role |

The role is authoritative. The persona shapes *how* the role is carried out; it
never redefines *what* the role is.

## The Safety Contract

A persona may **ADD** and **REPRIORITIZE** within the role's floor. It may never
**SUBTRACT** from that floor, skip its steps, lower its bar, or claim its own agent
identity.

Personas may only vary these additive dimensions:

| Dimension | May do | May not do |
|-----------|--------|------------|
| Lens | Frame the same work with a point of view | Change the deliverable or authority |
| Attention | Raise what to notice first | Remove required checks |
| Heuristics | Add tie-breakers within scope | Override role boundaries |
| Values | Emphasize existing priorities | Introduce conflicting goals |
| Voice | Set register and tone | Restate identity, method, or boundaries |

## Anti-Pattern: Persona Written as a Standalone Agent

```markdown
<!-- WRONG - the persona collides with the role's identity and method -->
You are Atlas, an autonomous engineer.
## Boundaries
...
## Method
...
## System Prompt
...

<!-- CORRECT - the persona is an additive overlay -->
Lens: weigh long-term maintainability over quick wins.
Attention: surface hidden coupling before proposing changes.
Voice: measured, concrete, low-drama.
```

A persona that opens with "You are..." or declares its own Boundaries, Method, or
System Prompt overrides the role floor and breaks composition. Strip those sections
and keep only additive lens/attention/heuristics/values/voice content.

## Pinning Is an Explicit Decision

Whether a persona is pinned (fixed seed or fixed identity) or resampled per run is
a design decision to state, not a silent default.

- Pinned personas give reproducibility and stable behavior across runs.
- Unpinned personas trade reproducibility for diversity.
- Record which one applies and why; do not leave the generation contract implicit.
