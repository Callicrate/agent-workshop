# Metrics Gateway

## Scope

This file applies to the entire fixture repository.

## Context

- **Purpose**: HTTP gateway that validates metric payloads and forwards accepted events to the internal queue.
- **Runtime**: Go 1.22 from `go.mod`.
- **Execution model**: Service binary under `cmd/gateway/` with shared packages under `internal/`.

## Repository Map

- `cmd/gateway/` - service entry point and CLI flags.
- `internal/generated/` - generated queue client; do not hand-edit.
- `api/openapi.yaml` - public request and response contract.
- `migrations/` - append-only database migrations; do not rewrite existing migrations.

## Local Commands

```bash
go test ./...
make generate
```

## Project Rules

- Keep request schema changes in `api/openapi.yaml`, handler validation, and tests in the same change.
- Add new queue fields through the generator; do not patch `internal/generated/` manually.
- Append a new migration under `migrations/` for schema changes; never edit a migration that has already landed.

## Testing

- Handler tests live under `internal/handlers/` and use fixtures from `internal/handlers/testdata/`.

## Tool and Workflow Contracts

- **Inputs**: JSON payloads matching `api/openapi.yaml` and service flags in `cmd/gateway/main.go`.
- **Outputs**: HTTP responses from `internal/handlers/` and queue events from `internal/generated/`.
- **Normal agent-facing path**: edit schema, regenerate client with `make generate`, then run `go test ./...`.
- **Inspected-only path**: production queue replay is documented in `docs/operations.md`; inspect only unless the user explicitly asks to run it.
- **Verified agent-facing surface**: `Makefile`, `go.mod`, and `api/openapi.yaml` prove the local commands and schema path.

## Related Docs

- `docs/operations.md` - read before changing replay, queue, or deployment behavior.
