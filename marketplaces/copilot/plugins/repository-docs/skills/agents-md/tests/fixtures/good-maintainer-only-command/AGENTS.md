# Maintainer Command Fixture

## Context

- **Purpose**: Demonstrates safe labeling for unsafe commands.

## Project Rules

- Normal agents inspect the command contract only.

## Tool and Workflow Contracts

- **Maintainer-only path**: do not run this deployment command during AGENTS.md authoring unless the user explicitly asks.

```bash
databricks bundle deploy -t prod
```
