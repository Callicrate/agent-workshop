# Contract-Bearing AGENTS.md Guidance

Use this only when `AGENTS.md` must preserve tools, MCP servers, CLIs, scripts, APIs, generated surfaces, data contracts, or external workflow contracts. For normal authoring, use [normal-authoring.md](normal-authoring.md).

## Live Contract Inventory

Before editing Tool and Workflow Contracts:

- enumerate the live surface from source, registry output, scripts, task files, or authoritative repo docs
- compare the live inventory to existing `AGENTS.md`, README, scripts, prompts, and examples
- remove stale entries, mark future work as planned, or mark external dependencies as external
- when simplification is claimed, verify before and after counts plus the source of both counts

Classify every named operational surface that `AGENTS.md` instructs agents to use or avoid.
Do not classify incidental folders that are only shown in a repository map unless they define an editing boundary or workflow contract.

Each operational surface should be one of:

- `verified existing`
- `planned`
- `external dependency`

Unverified entries do not belong in operational guidance.

Safe planned wording:

```markdown
- **Planned**: `scripts/export_metrics.py` is referenced in `docs/roadmap.md` but is not present; do not use it until implemented.
```

## Agent-Facing Surface

- Prefer a small, role-oriented public workflow surface.
- Keep maintainer-only escape hatches out of normal agent workflow sections.
- If a lower-level tool remains available, mark it non-default or internal and state the concrete break-glass trigger.
- Collapse overlapping tools into one normal path when the repository already has a clearer abstraction.

## Generic Core, Thin Wrapper

When repositories contain reusable orchestration plus domain workflows, document the split:

- generic service owns reusable mechanics, worker launch, queue handling, or consolidation
- domain wrapper owns domain context, stop conditions, and user-facing commands
- AGENTS.md should point agents at the wrapper for normal work and at the generic core only for maintainer work

## Review Ledger

For repeated review loops, interrupted edits, or contentious contract changes, keep a short ledger in your working notes:

- finding or correction
- fix status
- validation command or evidence path
- residual risk

Do not treat prior assistant text as proof that a contract was updated. Re-read files and record the actual state.
