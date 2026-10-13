# Other Stack AGENTS.md Guidance

Use this when the target repository is not primarily Python or TypeScript, or when it is polyglot and no language-specific reference exists.
Focus on verified local contracts instead of generic language advice.

## Context Facts To Verify

Read stack-specific root files before drafting guidance:

- Go: `go.mod`, `go.sum`, `Makefile`, `Taskfile.yml`, `magefile.go`
- Rust: `Cargo.toml`, `Cargo.lock`, `.cargo/config.toml`, `rust-toolchain.toml`
- Java or Kotlin: `pom.xml`, `build.gradle`, `build.gradle.kts`, wrapper scripts
- .NET: `*.sln`, `*.csproj`, `global.json`, `Directory.Build.props`
- Ruby: `Gemfile`, `Gemfile.lock`, `Rakefile`
- PHP: `composer.json`, `composer.lock`
- Terraform or infrastructure: `*.tf`, `versions.tf`, backend config, CI plans
- Docker-heavy repos: `Dockerfile`, compose files, entrypoint scripts, health checks

## High-Value AGENTS.md Entries

Prefer entries that identify local boundaries and commands:

```markdown
## Context
- **Runtime**: Go 1.22 from `go.mod`.
- **Package layout**: commands live under `cmd/`; shared packages live under `internal/`.
- **Checks**: `go test ./...` is the repo-defined test command.
```

```markdown
## Project Rules
- Keep generated clients under `internal/generated/`; update them with `make generate`.
- Add new service configuration keys to `config/schema.json` and the matching loader tests.
- Do not edit existing migration files under `migrations/`; append a new migration.
```

## What To Omit

Skip broad language defaults such as formatting style, naming style, or ordinary test advice unless the repository has a local exception, configured tool, or recurring failure.
Do not document commands from memory when the repo does not define or support them.
