# Technical Surface Review

Use this reference when reviewing architecture docs, MCP servers, tool contracts, CLI docs, API surfaces, prompts, skills, config files, generated artifacts, or any claim that can be compared with a live repository or runtime surface.

## Live Surface First

Before judging the design or documentation, inventory the current implementation.

Check the surfaces that fit the artifact:

- MCP and tool registrations: registered tool names, schemas, handler files, transport docs, hidden or deprecated tools, and exposed versus internal names.
- CLI commands: entry points, package scripts, `argparse` or command framework declarations, documented flags, default values, and examples.
- APIs and routes: registered routes, request and response schemas, auth requirements, webhooks, and versioned paths.
- Prompts and skills: file names, frontmatter descriptions, linked references, deterministic scripts, generated assets, and public workflow names.
- Repository guidance: `AGENTS.md`, instruction files, README sections, handoff docs, status files, and scripts named as operational sources of truth.
- Config and generated artifacts: package metadata, build files, schemas, lockfiles, generated prompt wrappers, and inventories used by downstream tools.

If a helper is useful, run `scripts/surface_snapshot.py --root <repo> --output <snapshot.json>` and treat the JSON as a lead list. Verify high-impact claims directly in source or runtime output before finalizing the finding.

## Compare Claims To Inventory

For every technical claim, compare the artifact against the observed surface:

- claimed tool count versus registered tool count
- claimed simplification versus before and after counts
- named component versus observed file, package, service, route, or runtime registration
- documented required input versus actual required input
- documented lifecycle versus actual start, poll, result, cleanup, and failure states
- documented ownership boundary versus actual module, server, or repository boundary
- promised automation versus scripts or manual steps that actually exist

Do not count a claimed simplification as complete without a before and after measure. If the public count grew, say so even if the internal implementation improved.

## Fictional-Component Audit

Create a small ledger for named technical nouns:

| Name | Kind | Status | Evidence | Review consequence |
|------|------|--------|----------|--------------------|
| `name` | tool/system/path/input | observed/proposed/absent/ambiguous | file, command, runtime output, or quote | finding, caveat, or no issue |

Status rules:

- `observed`: confirmed in repository, runtime output, package metadata, or source artifact.
- `proposed`: explicitly labeled as future work, candidate, or design option.
- `absent`: searched for and not found, while the artifact presents it as real.
- `ambiguous`: the name may map to another real component, but the artifact does not make the boundary clear.

Treat absent-but-real-sounding components as factuality or architecture findings. Do not preserve fictional handoff targets, tool families, scripts, or paths as harmless placeholders unless the artifact clearly labels them as future work.

## Public Surface And Abstraction Leaks

Review the audience-facing mental model, not only the internal implementation.

Flag these as first-class findings:

- duplicate tools, commands, or concepts that make the caller choose between overlapping verbs
- public names that expose implementation details the caller does not need
- distinctions that are real internally but not useful to an agent, user, partner, or operator
- redundant required inputs that can be derived from context or one canonical identifier
- lifecycle steps hidden behind a single command name without status, wait, result, or cleanup controls
- health, preflight, status, and diagnostics commands whose boundaries are unclear
- file transfer, remote read/write, upload/download, or artifact commands with overlapping responsibilities

Group related redundancy findings by decision impact. For example, report overlapping tool families as one public-surface simplification issue instead of scattering separate low-value complaints.

## Bad And Good Review Patterns

Wrong:

```text
The architecture document looks simpler because it says the public tool surface was reduced.
```

Correct:

```text
The simplification claim does not hold. The registered tool inventory has 18 public tools after the change versus 15 before, and two new names duplicate existing health and network-status concepts.
```

Wrong:

```text
The handoff model is acceptable because the proposed tool family would be useful later.
```

Correct:

```text
The handoff model presents `ctf-network` and `ctf-web` as existing peers, but the repository and runtime inventory only show `ctf-exe` and `ctf-mem`. Label the missing peers as future work or remove the boundary from the current contract.
```

## Follow-Up Corrections

When the user questions a term or boundary, re-check the contract, not just the wording. A question like "what is project?" may reveal that the artifact requires redundant inputs, crosses repository boundaries, or hides the actual source of truth.

Before finalizing a workflow-gap finding, check whether a deterministic helper, linked script, generated artifact, or existing reference already covers the behavior. If it does, revise the finding to target discoverability, routing, or stale docs instead of claiming the capability is absent.