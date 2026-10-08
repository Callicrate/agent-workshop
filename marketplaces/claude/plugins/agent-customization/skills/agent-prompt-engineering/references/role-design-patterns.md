# Role Design Patterns

Use this when the deliverable is an agent role or persona contract.

## Required Fields

| Field | Define |
|-------|--------|
| Identity | Name, domain, intended audience |
| Capabilities | Actions the agent may take autonomously |
| Boundaries | What is out of scope or requires confirmation |
| Inputs and Outputs | Accepted inputs, required deliverables, quality bar |
| Escalation | When to ask for help and how to report blockers |

## Design Rules

- Give each role one primary objective.
- Assign one owner per decision area.
- Prefer narrow roles over a general expert persona.
- State approval requirements explicitly.
- Keep communication style separate from authority and workflow.

## Good Boundaries

- May edit files in src/ but must not change CI config without confirmation.
- Owns schema design, but not deployment decisions.
- Produces a patch and validation summary, not a product roadmap.

## Anti-Patterns

- A role that both approves and audits the same output.
- Capabilities that overlap so heavily that two agents can both edit the same artifact.
- Persona language that does not change behavior.

## Provenance and Usage Gates

When roles arrive as an imported pack rather than one at a time, keep them auditable.

- Record where each role came from, when, and why it was added. An unsourced bulk import (for example "21 migrated plus 8 additions" with no source pack) leaves provenance unverifiable.
- Gate retention on demonstrated use: keep roles with real routing or usage, and prune the rest until actual demand returns them.
- Do not hard-wire a large role library into tests or defaults before its dispatch path exists.

## Composing Personas Over Roles

A persona is an additive overlay on a role's floor, not a second agent definition.
See [persona-overlay-composition.md](persona-overlay-composition.md) for the safety
contract, the additive-only dimensions, and the standalone-agent anti-pattern.

## Review Questions

- What can this agent do without asking?
- What must it never do?
- Which artifact proves it finished?
- Which decision belongs to another agent?

Start from the [agent role template](../templates/agent-role-template.md) when the role definition needs its own file.
