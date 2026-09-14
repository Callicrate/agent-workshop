# Multi-Agent Coordination

Use this when more than one agent participates in the workflow.

## Define Per-Agent Contracts

Before naming participants, verify that each agent, script, MCP, or queue already exists or is explicitly proposed future work. Do not invent ownership tables from domain nouns.

| Item | Required Content |
|------|------------------|
| Owner | Which agent is accountable for the decision or artifact |
| Inputs | Files, context, or prior artifacts the agent receives |
| Outputs | Artifact or decision the agent must return |
| Acceptance criteria | How the next agent knows the handoff is complete |
| Escalation | When to stop the loop and raise a blocker |

## Handoff Packet

Every handoff should include:

- current artifact and source-of-truth path
- assumptions already accepted
- open questions that remain unresolved
- validation already performed
- exact next owner
- verified caller or dispatcher that performs the handoff

## Work Queue Lifecycle

- If agents claim GitHub issues, PRs, queue records, or step issues, define the claim helper and status transition rules before the agents start.
- Machine-consumed helper output should be JSON-only and include stable identifiers, role labels, current status, next action, and blocker fields.
- Role labels must decide ownership or routing. If labels conflict, name the deterministic tie-breaker.
- Agents should create human-question items only when the prompt explicitly permits that path or progress is blocked by a human-only decision.

## Shared Artifact Rules

- One writable owner per artifact at a time.
- Name the source of truth before work starts.
- Version or timestamp shared artifacts when multiple agents read them.
- Do not let two agents edit the same file in parallel unless one only comments.

## Idea Worker Contracts

Use this for stuck-review workers, CTF idea sprints, exploit-research pods, or any agent whose output is meant to create new hypotheses rather than edit files.

- Inputs must include the current scope, known evidence, dead paths, artifacts, and stop condition.
- Outputs must be concrete experiments or checks tied to the environment: command to run, file to inspect, endpoint to hit, credential to test, or source to verify.
- Each idea needs an evidence anchor, expected signal, risk or cost, and a clear condition for accepting or rejecting it.
- Generic advice, summaries of the prompt, unsupported exploit claims, and ideas that ignore current coordinates do not satisfy the contract.
- If workers cite external research, they must separate source facts from environment-specific inferences.
- Keep generic orchestration generic and adapt domain behavior at the edge. A generic idea service should own worker launch and consolidation; a domain wrapper should supply scope, evidence, dead paths, and stop conditions.

## Conflict Resolution

- Predefine the tie-breaker agent or authoritative document.
- If two agents disagree twice without progress, escalate on the third cycle.
- Resolve policy conflicts by choosing one source of truth and removing duplicated rules elsewhere.

## Anti-Patterns

- simultaneous edits to one artifact
- handoffs with no acceptance criteria
- shared memory with no provenance
- agents that can both make the same final decision

Start from the [multi-agent contract template](../templates/multi-agent-contract-template.md) when you need a formal handoff document.
