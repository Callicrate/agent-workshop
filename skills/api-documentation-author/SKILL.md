---
name: api-documentation-author
description: "Use when documenting REST APIs, MCP tools, CLI commands, webhooks, or callable examples; produces interface contracts. Do not trigger for READMEs, project or operator setup, runbooks, changelogs, behavior changes, or debugging."
metadata:
  short-description: Document APIs and developer interfaces.
---

# API Documentation


## When to Use

- Documenting REST API endpoints from source code, OpenAPI, Swagger, or a verified live API
- Documenting machine-consumable interfaces such as MCP tools, CLI commands, SQL query files, webhooks, or callable access paths when the user needs a copyable contract for another engineer or agent
- Auditing existing interface docs for drift, missing surfaces, or broken examples
- Splitting large API or interface docs into a maintainable folder layout
- Generating developer-first request and response examples

## When NOT to Use

- General project documentation such as README.md, AGENTS.md, or changelogs unless the requested deliverable is primarily an interface contract
- Project or operator setup, deployment guidance, and runbooks whose primary purpose is to run or maintain a project; route those to `documentation-author`. Document install or connect steps here only when they are necessary to invoke a named interface contract.
- API design work that changes behavior rather than documenting it
- Runtime debugging that does not produce documentation

## Workflow

1. Read the interface source of truth first. Inventory endpoints, tools, commands, queries, auth model, permission tiers, environment assumptions, and shared patterns before writing.
2. Classify the interface surface: REST endpoint, MCP tool, CLI command, SQL query, webhook or event, install/connect path, or observed access path. Use the REST endpoint template only for HTTP endpoints. For other surfaces, write a contract with inputs, invocation, authentication or environment, expected output, evidence of success, failure modes, and safety boundaries.
3. Use [references/organization-guide.md](references/organization-guide.md) to choose single-file versus folder structure and the default location when the repo has no existing convention.
4. Start from [templates/endpoint-single-file.md](templates/endpoint-single-file.md) or [templates/endpoint-folder-readme.md](templates/endpoint-folder-readme.md), then use [references/endpoint-template.md](references/endpoint-template.md) for endpoint section order.
5. Load only the detailed reference that matches the gap:
  - [references/auth-patterns.md](references/auth-patterns.md) for auth and authorization sections
  - [references/common-patterns.md](references/common-patterns.md) for shared pagination, errors, rate limits, retries, idempotency, or webhook notes
  - [references/response-schema-tables.md](references/response-schema-tables.md) for nested objects, arrays, nullable fields, or unions
  - [references/query-artifact-patterns.md](references/query-artifact-patterns.md) for standalone SQL/query files, parameters, time windows, zero-row diagnostics, and SQLTools-friendly outputs
  - [references/tool-interface-patterns.md](references/tool-interface-patterns.md) for MCP tools, CLI commands, install/connect paths, output schemas, errors, and reload steps
  - [references/agent-readable-docs.md](references/agent-readable-docs.md) for docs intended as starting points or contracts for autonomous agents
  - [references/formatting-rules.md](references/formatting-rules.md) for the final markdown pass
6. If the user asks to keep a doc updated while work continues, treat it as a living evidence document. Update it with only verified new facts, include a last-updated note or status section if the project convention supports it, preserve trusted external notes, and then resume the primary task without waiting for approval.
7. Keep examples copy-paste ready. Use concise raw `requests` examples unless the API requires a client library. Do not include generic import/setup boilerplate that experienced engineers already know.
8. For integration or debugging-oriented API docs, load [references/request-template-checklist.md](references/request-template-checklist.md) and include exact request templates with evidence boundaries.
9. Validate finished REST endpoint docs from the target repository root with `python -B <skill-root>/scripts/validate_api_doc.py <relative-target> --fail-on-warnings`. Add `--json` for the stable `{error, ok, result}` envelope. The helper accepts a regular `.md` file or a bounded documentation tree, reports contained root-relative source paths for directory targets and structural ordinals for direct files, skips reparse points, and reads each file once through a no-follow handle with resource and race checks. Warnings include unresolved built-in starter markers, so resolve or remove every warning before delivery. It rejects local hardlinks because an outside alias cannot be proven safe, and it never emits raw endpoint, query, URL, destination, fragment, secret, or outside-path values. For non-REST docs, run the task-specific check: SQL parser or harmless query when possible, `--help` or dry-run for CLI docs, MCP tool schema inspection for MCP docs, link verification, and path existence checks for every stated local path.

The validator is a bounded audit, not a Markdown renderer. Its fenced-code and heading handling follows the [CommonMark specification](https://spec.commonmark.org/current/); its table checks follow the [GFM tables extension](https://github.github.com/gfm/#tables-extension-). Endpoint sections are isolated at the next endpoint heading at any level or the next same-or-shallower non-endpoint heading, so endpoint content cannot satisfy a neighbor's contract.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/validate_api_doc.py](scripts/validate_api_doc.py) | You need a structural and formatting check for REST endpoint Markdown before finishing | Consistent REST API doc shape |
| [templates/endpoint-single-file.md](templates/endpoint-single-file.md) | The API fits in one document | Stable single-file starter |
| [templates/endpoint-folder-readme.md](templates/endpoint-folder-readme.md) | The API needs a folder layout | Stable multi-file starter |
| [references/endpoint-template.md](references/endpoint-template.md) | You are drafting or fixing endpoint sections | Canonical endpoint section order |

## References

- [references/organization-guide.md](references/organization-guide.md) — file layout and permission grouping
- [references/formatting-rules.md](references/formatting-rules.md) — markdown conventions and table patterns
- [references/common-patterns.md](references/common-patterns.md) — pagination, errors, and non-standard responses
- [references/auth-patterns.md](references/auth-patterns.md) — auth and authorization patterns
- [references/response-schema-tables.md](references/response-schema-tables.md) — nested and typed response tables
- [references/request-template-checklist.md](references/request-template-checklist.md) — request templates, auth, response evidence, and safety boundaries
- [references/query-artifact-patterns.md](references/query-artifact-patterns.md) — SQL/query artifacts, parameters, time windows, result shapes, and diagnostics
- [references/tool-interface-patterns.md](references/tool-interface-patterns.md) — MCP tool, CLI command, install/connect, and reload documentation contracts
- [references/agent-readable-docs.md](references/agent-readable-docs.md) — interface docs written as autonomous-agent starting points
