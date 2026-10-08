# Template Selection

Use this helper only when creating a new file, replacing an empty file, or performing an explicit rewrite. For normal evidence sampling and rule quality, use [normal-authoring.md](normal-authoring.md).

## Mode Selection

| Mode | Use When | Required Work |
|------|----------|---------------|
| Patch | Small edit to an existing `AGENTS.md`; no new repo-wide claims | Read the target section, verify touched paths or commands only, run quick validation |
| Create or update | Normal repository `AGENTS.md` work | Analyzer output, sampled repo evidence, standard validation |
| Split/move, review, or exhaustive | Topology, contract-bearing, operational, subagent, domain-specific, or safety-sensitive guidance | Use the matching advanced reference from `SKILL.md` |

## Template Rule

For existing `AGENTS.md` files, preserve valid local guidance and make the smallest structure-preserving edit that satisfies the task.
Use a template only when creating a new file, replacing an empty file, or performing an explicit full rewrite.

Use the smallest applicable starter:

- [../assets/agentsmd-minimal.md](../assets/agentsmd-minimal.md) as the default for normal packages, libraries, apps, tools, or docs repos.
- [../assets/agentsmd-contract-bearing.md](../assets/agentsmd-contract-bearing.md) when the file must preserve CLIs, APIs, MCP servers, generated surfaces, data contracts, or external workflow contracts.
- [../assets/agentsmd-operational.md](../assets/agentsmd-operational.md) when the repo has live operations, handoff files, subagents, CTF/lab workflows, or repository-defined capability checks.
- [../assets/agentsmd-full.md](../assets/agentsmd-full.md) only for explicit full rewrites or unusually broad repositories.

For tiny repositories, acceptable output may be only the H1 title plus Scope, Context, and Project Rules. Local Commands and Testing are optional. There is no minimum section count.

## Section Policy

Required for new files:

- H1 title
- Scope
- Context
- Project Rules

Conditionally required:

- Repository Map only when path roles or edit boundaries matter
- Local Commands only when verified local commands exist
- Testing only when verified testing guidance exists
- Tool and Workflow Contracts only when agent-facing tools, generated surfaces, APIs, data contracts, or workflow contracts exist
- Coordination and Evidence only when durable handoff, status, memory, or coordination files exist
- Do / Don't only when verified local good and bad examples exist

Canonical heading order:

```markdown
# Project Name
## Scope
## Context
## Repository Map
## Local Commands
## Project Rules
## Testing
## Tool and Workflow Contracts
## Coordination and Evidence
## Style Conventions
## Domain Terms
## Do / Don't
## Related Docs
```

Optional sections may be omitted, but remaining known sections should preserve this relative order.

## Trimming Guidance

Keep a section when the repository has any of these traits:

- Production deployment, release, migration, scheduled job, or operational runbook behavior.
- Security-sensitive behavior, authentication, authorization, non-lab secrets, payments, customer data, or private infrastructure.
- Compliance, legal, audit, privacy, safety, regulated data, or model-risk constraints.
- More than one owning team, package, service, app, workflow, or deploy target.
- Monorepo structure, generated code, vendored code, migrations, notebooks, schemas, or read-only boundaries agents must respect.
- Cross-system contracts such as APIs, queues, tables, files, model artifacts, CLI interfaces, or external services.
- Domain vocabulary, data contracts, promotion rules, or recurring AI mistakes that need separate sections.

Delete a section when none of its traits apply and it would contain only generic advice already covered by the agent's base behavior.
Do not add generic rules such as "be concise," "write tests," "use clear names," "follow PEP 8," or "avoid hallucinations" unless the repo has a local exception.

Before finishing, remove every bracket placeholder such as `[path]`, `[framework]`, `[tool/package manager]`, and `[Representative test command]`.
Unknown facts should be omitted, not guessed.
