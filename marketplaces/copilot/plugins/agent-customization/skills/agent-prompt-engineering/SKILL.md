---
name: agent-prompt-engineering
description: "Use when writing agent prompts/role specs/handoffs/multi-agent rules or .prompt.md/.agent.md/.instructions.md. Do not trigger for skills/AGENTS.md/docs/runtime code."
version: "1.4.0"
metadata:
  short-description: Write or review agent prompts.
---

# Agent Prompt Engineering


## When to Use

- Writing or revising a system prompt, role spec, or operating contract for an agent
- Reviewing prompt text for ambiguity, conflicting directives, or weak guardrails
- Defining multi-agent ownership, handoffs, and conflict resolution
- Designing the agent runtime contract behind a prompt: state transitions, durable artifacts, worker boundaries, and resume behavior
- Turning broad behavioral goals into explicit instructions and evaluation cases
- Writing or revising standalone `.prompt.md`, `.agent.md`, or `.instructions.md` files that define agent behavior but are not AGENTS.md and are not skill artifacts

## When NOT to Use

- Writing repository-wide AGENTS.md guidance
- Creating or updating a skill artifact
- Building runtime code that loads or routes prompts
- Writing user-facing copy, READMEs, or general documentation

## Workflow

1. Identify the deliverable: system prompt, role definition, multi-agent contract, or iteration pass.
2. Before drafting or revising an operational prompt, ground the contract in the current system. Inspect the actual repo tree, tool registry, scripts, command names, and existing docs that the prompt will reference. Ownership tables, tool lists, folder contracts, and handoff rules may name only verified existing components unless a section is explicitly labeled proposed future work.
3. Start from the matching reference and template when one exists:
  - system prompt: [references/system-prompt-patterns.md](references/system-prompt-patterns.md) and [templates/system-prompt-template.md](templates/system-prompt-template.md)
  - role definition: [references/role-design-patterns.md](references/role-design-patterns.md) and [templates/agent-role-template.md](templates/agent-role-template.md)
  - persona overlay that composes over a fixed role: [references/persona-overlay-composition.md](references/persona-overlay-composition.md)
  - multi-agent contract: [references/multi-agent-coordination.md](references/multi-agent-coordination.md) and [templates/multi-agent-contract-template.md](templates/multi-agent-contract-template.md)
  - agent runtime contract: [references/agent-system-runtime.md](references/agent-system-runtime.md) and [templates/runtime-contract-template.md](templates/runtime-contract-template.md)
  - skill choreography or handoff rules: [references/skill-choreography.md](references/skill-choreography.md)
  - issue, PR, or queued work lifecycle: [references/work-lifecycle-contracts.md](references/work-lifecycle-contracts.md) and [templates/work-lifecycle-template.md](templates/work-lifecycle-template.md)
  - required-tool fallback or stop behavior: [references/fallback-path-audit.md](references/fallback-path-audit.md)
  - prompt contract guardrails, surface minimization, architecture invariants, and recovery: [references/prompt-contract-guardrails.md](references/prompt-contract-guardrails.md)
  - agent-facing abstraction boundaries: [references/agent-facing-abstraction-patterns.md](references/agent-facing-abstraction-patterns.md)
  - prompt size, model targets, and versioning: [references/prompt-size-versioning.md](references/prompt-size-versioning.md)
  - tool-access security: [references/tool-security-patterns.md](references/tool-security-patterns.md)
  - iteration pass: [references/prompt-iteration-workflow.md](references/prompt-iteration-workflow.md)
  - first-time worked example: [references/worked-example.md](references/worked-example.md)
4. For agent-facing tool or command contracts, run a surface-minimization pass. Identify the one normal path for each routine capability, hide implementation details that the agent should not reason about, and label any escape hatch as break-glass with a concrete trigger. Remove overlapping tools from the prompt even if they remain available internally.
5. Keep sections separated by job: identity, environment, constraints, scope, instructions, state, memory, tools, or handoff rules. Remove prose that does not change model behavior.
6. For large prompt suites or many-agent generation tasks, generate files incrementally, keep a checklist of expected outputs, validate each batch, and resume from the checklist after rate limits, no-response failures, or context compaction. Apply the [prompt transport and run-artifact guardrails](references/prompt-contract-guardrails.md#transport-and-run-artifacts) before launching generated prompts.
7. Run [scripts/audit_prompt_structure.py](scripts/audit_prompt_structure.py) against the draft. Errors are blocking; warnings are advisory. When auto-detection is uncertain or picks the wrong kind, pass `--kind` explicitly. For starter templates, use `--template-baseline` to check headings only, then rerun without it after content is filled. For operational contracts, add `--repo-root`, `--tool-manifest`, and `--check-runtime-contract` when those facts are available.
8. Re-test representative inputs, record [iteration evidence](references/prompt-iteration-workflow.md#6-record-iteration-evidence), and keep only changes that improve the target behavior without creating regressions.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/audit_prompt_structure.py](scripts/audit_prompt_structure.py) | You need a repeatable structure or contract check for a prompt or coordination contract | Required-section audit plus vague-rule warnings; optional checks for unresolved tool names, required artifact claims, duplicate required inputs, missing async lifecycle fields, and examples that reference non-existent repo paths |
| [assets/iteration-evidence.schema.json](assets/iteration-evidence.schema.json) | You need to validate or generate an iteration evidence artifact | Canonical JSON Schema for iteration pass evidence |
| [templates/system-prompt-template.md](templates/system-prompt-template.md) | You need a starter file for a single-agent system prompt | Minimal prompt skeleton |
| [templates/agent-role-template.md](templates/agent-role-template.md) | You need a narrow role contract before writing full prompt text | Role definition starter |
| [templates/multi-agent-contract-template.md](templates/multi-agent-contract-template.md) | You need to formalize agent ownership and handoffs | Coordination contract starter |
| [templates/runtime-contract-template.md](templates/runtime-contract-template.md) | You need to specify helper-agent, worker, queue, or MCP lifecycle behavior | Runtime call-path starter |
| [templates/work-lifecycle-template.md](templates/work-lifecycle-template.md) | You need issue, PR, queue, or work-item claim and status rules | Work lifecycle starter |
| [tests/test_audit_prompt_structure.py](tests/test_audit_prompt_structure.py) | You change the audit script heuristics | Regression coverage for kind detection, vague phrases, template mode, and example stripping |

## Known Limitations

- **Auto kind detection:** `detect_kind()` uses heading-level schema first, then body-text keyword frequency. When the headings are ambiguous (e.g., a role contract mentioning handoffs), prefer `--kind role` or `--kind system` explicitly rather than relying on auto.
- **Warning threshold:** There is no fixed warning count that automatically fails a prompt. Treat errors as blocking and warnings as advisory; investigate high warning counts but do not chase zero warnings at the expense of useful example text.
- **Template baseline:** `--template-baseline` validates starter skeleton headings only. It is not evidence that a filled prompt is ready.
- **Optional fact checks:** `--repo-root`, `--tool-manifest`, and `--check-runtime-contract` are heuristic aids. Treat their findings as prompts for review, not proof that a contract is fully grounded.

## References

- [references/system-prompt-patterns.md](references/system-prompt-patterns.md) - system prompt section contract
- [references/role-design-patterns.md](references/role-design-patterns.md) - role scope and authority design
- [references/persona-overlay-composition.md](references/persona-overlay-composition.md) - persona-as-overlay safety contract, additive dimensions, and pinning decisions
- [references/multi-agent-coordination.md](references/multi-agent-coordination.md) - ownership, handoffs, and conflict resolution
- [references/agent-system-runtime.md](references/agent-system-runtime.md) - state machines, durable artifacts, resume behavior, and execution boundaries
- [references/skill-choreography.md](references/skill-choreography.md) - primary/support skill selection and return conditions
- [references/work-lifecycle-contracts.md](references/work-lifecycle-contracts.md) - work claiming, status transitions, deterministic helpers, and human-question boundaries
- [references/fallback-path-audit.md](references/fallback-path-audit.md) - required-tool fallback, absence behavior, handoff returns, and stop conditions
- [references/prompt-contract-guardrails.md](references/prompt-contract-guardrails.md) - fact grounding, surface minimization, architecture invariants, optional artifacts, and recovery checks
- [references/agent-facing-abstraction-patterns.md](references/agent-facing-abstraction-patterns.md) - hiding infrastructure details behind simple agent-facing operations
- [references/prompt-size-versioning.md](references/prompt-size-versioning.md) - prompt length budgets, model targets, and version records
- [references/tool-security-patterns.md](references/tool-security-patterns.md) - prompt-injection, secret, and tool-authority guardrails
- [references/prompt-iteration-workflow.md](references/prompt-iteration-workflow.md) - test-driven prompt refinement
- [references/worked-example.md](references/worked-example.md) - concrete prompt creation and iteration example
