# Metrics Gateway

## Scope

This file applies to the entire repository unless a nested `AGENTS.md` provides more specific guidance.

## Context

- Purpose: HTTP gateway that validates metric payloads and forwards accepted events to the internal queue.
- Primary stack: Go 1.22 from `go.mod`.
- Agent-facing contract: `api/openapi.yaml`, generated queue client code, and handler tests must stay in sync.

## Local Commands

```bash
go test ./...
make generate
```

## Project Rules

- Keep request schema changes in `api/openapi.yaml`, handler validation, and tests in the same change.
- Regenerate `internal/generated/` with `make generate`; do not hand-edit generated queue client files.
- Append new database migrations under `migrations/`; do not rewrite existing migrations.

## Testing

- Handler tests live under `internal/handlers/` and use fixtures from `internal/handlers/testdata/`.
- Run `go test ./...` after handler, generated-client, schema, or migration changes.

## Tool and Workflow Contracts

- **Inputs**: JSON payloads matching `api/openapi.yaml` and service flags in `cmd/gateway/main.go`.
- **Outputs**: HTTP responses from `internal/handlers/` and queue events from `internal/generated/`.
- **Normal agent-facing path**: edit schema, regenerate with `make generate`, then run `go test ./...`.
- **Inspected-only or maintainer-only path**: production queue replay is documented in `docs/operations.md`; inspect only unless the user explicitly asks to run it.

## Related Docs

- `docs/operations.md` - read before changing replay, queue, or deployment behavior.
