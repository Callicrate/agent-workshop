# Unsafe Command Fixture

## Context

- **Purpose**: Demonstrates unsafe command detection.

## Local Commands

```bash
databricks bundle deploy -t prod
```

## Project Rules

- Do not place production deployment commands in normal local workflow guidance.
