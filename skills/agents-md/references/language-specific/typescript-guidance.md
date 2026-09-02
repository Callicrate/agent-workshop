# TypeScript AGENTS.md Guidance

Use this to capture TypeScript rules that are specific to the repository.
Do not restate standard TypeScript, React, or Node.js advice unless this project has a concrete local convention.

## Context Facts To Verify

Read these before drafting TypeScript guidance:

- `package.json`, lockfile, `tsconfig.json`, and framework config
- script names for install, dev, build, lint, typecheck, and test
- source layout, path aliases, generated-client directories, and test locations
- representative components, API handlers, services, or packages

## High-Value AGENTS.md Entries

```markdown
## Context
- **Runtime**: Node.js 20 from `.nvmrc`
- **Package manager**: `pnpm` from `pnpm-lock.yaml`
- **Framework**: Next.js App Router; route handlers live under `app/api/`
- **Checks**: `pnpm typecheck`, `pnpm lint`, and `pnpm test`
```

```markdown
## Project Rules
- Use the `@/` path alias configured in `tsconfig.json` for cross-feature imports.
- Keep generated GraphQL types in `src/generated/`; update with `pnpm codegen`.
- Server-only data access belongs in `src/server/`; client components must call typed actions instead.
```

## What To Omit

Skip generic entries unless the repository proves a local exception:

- broad strict-mode recommendations already enforced by `tsconfig.json`
- ordinary React naming rules with no repo-specific structure
- "avoid any" or "prefer type safety" without local examples or tooling
- package-manager commands guessed from habit instead of lockfiles

## Useful Do / Don't Pair

### Do

```typescript
import { getSession } from '@/server/auth';
```

### Don't

```typescript
import { getSession } from '../../server/auth'; // Bypasses the repo alias convention.
```
