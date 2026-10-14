# Tool Interface Patterns

Use this reference for MCP tools, CLI commands, install/connect procedures, webhook-like command surfaces, and service access docs.

## Surface Classification

Choose the contract shape by surface type:

| Surface | Required Focus |
|---------|----------------|
| MCP tool | tool name, server, input schema, output schema, server state, errors |
| CLI command | command, working directory, environment, arguments, output, exit codes |
| Install/connect path | prerequisites, package or build artifact, config path, reload step, smoke test |
| Webhook/event | producer, trigger, payload, auth, retries, idempotency, failure evidence |
| Observed access path | what was tested, what was seen, reproduction steps, unknowns, safety boundaries |

Do not force these surfaces into `METHOD /path`. Use HTTP endpoint sections only for real HTTP endpoints.

## MCP Tool Contract

Include:

- server and transport context
- tool name exactly as registered
- purpose and normal use case
- input schema with required and optional fields
- required workspace, challenge, session, or server state
- output schema or durable artifact path
- expected success evidence
- error and degraded-mode behavior
- refresh, reconnect, or reload steps for the client when tool registration changes

Inspect the actual tool registry or source before naming tools. Do not document proposed tools as available unless they are clearly labeled future work.

## CLI Command Contract

Include:

- working directory
- shell or platform assumptions
- executable path or package manager command
- required environment variables, config files, profiles, or services
- arguments and defaults
- examples for normal use and one diagnostic or dry-run path
- expected stdout, files, or exit code
- common failures and what to check next

Run `--help`, `--version`, a dry run, or the smallest harmless command when possible before publishing command docs.

## Install And Connect Docs

Include:

- supported host context, such as WSL, code-server, local VS Code, remote Linux, or container
- prerequisites and how to verify them
- build or package command when install depends on local source
- config file paths and restart or reload steps
- smoke test that proves the client can see or call the interface
- update and uninstall notes only when the user needs to operate the install later

Avoid hidden editor coupling. If the workflow can run outside VS Code, write the command and file contract in editor-neutral terms, then add VS Code or code-server notes as a client-specific subsection.

## Layered Provider Config

When a config routes model aliases through a gateway to backend providers, trace the full chain in one pass and label each name's kind so readers do not mistake an alias for a native provider.

- Distinguish a **model alias** (a name the gateway exposes) from a **backend provider** (the real upstream that serves it).
- Map alias to backend explicitly; do not list a backend model name as if it were a standalone provider.
- State which providers are actually configured versus only reachable as routed backend model names.

Use a compact mapping table:

| Name | Kind | Routes to |
|------|------|-----------|
| `fast-chat` | alias | `gateway` backend |
| `deep-reason` | alias | `gateway` backend |
| `gateway` | backend provider | native upstream |

Then note the negative case plainly, e.g. "`vendor-x` and `vendor-y` appear only as backend model names routed through `gateway`; no native `vendor-x` or `vendor-y` provider is configured."

## Access Reproduction Docs

When documenting observed access, include:

- reachable surfaces
- exact command, URL, client, or tool used
- observed status, fields, screens, files, or errors
- what the evidence proves
- what is not yet proven
- what must not be edited or changed
- trusted adjacent files or notes that should be preserved
- last-updated or status section when the doc is maintained during execution

Use living docs for ongoing work: update only verified new facts, preserve trusted external notes, and resume the main task after the update.