# Ideal Specification for an agents-md Skill

## 1. Purpose

The skill creates, updates, splits, moves, or reviews repository `AGENTS.md` files.

Analyzer schema 1.3 is bounded evidence, not a completeness claim. Its
`scan_summary.certainty` is `limited` whenever a cap, unreadable candidate,
or excluded alias leaves a material coverage gap; `uncertainty_items` records
the stable reason without source values.

An ideal `AGENTS.md` is a compact behavior guide for future agents working in a repository. It is not a README, architecture document, changelog, evidence log, or general coding standards file.

The skill should optimize for guidance that is:

- repo-specific
- scoped
- currently true
- evidence-backed
- behavior-changing
- safe
- concise
- non-duplicative

## 2. Activation

Use this skill when the user asks to:

- create an `AGENTS.md`
- update an existing `AGENTS.md`
- review an `AGENTS.md`
- split root guidance into nested `AGENTS.md` files
- move or rename `AGENTS.md` guidance with repository topology changes
- add verified project commands, edit boundaries, or agent-facing contracts to `AGENTS.md`

Do not use this skill for:

- README-only work
- architecture-doc-only work
- changelog work
- standalone prompts or skills
- general coding standards not tied to `AGENTS.md`
- non-repository instruction files unless the user explicitly says to convert them into `AGENTS.md`

For mixed documentation requests, use this skill only for the `AGENTS.md` portion.

## 3. Non-goals

The skill must not turn `AGENTS.md` into:

- a full project overview
- a complete directory listing
- a runbook
- a task log
- a memory dump
- a security findings ledger
- a list of generic language rules
- a substitute for tests, CI, or architecture docs

It may point to those documents when agents need them before editing.

## 4. Core principles

### 4.1 Behavior over description

Include a fact only when it changes what a future agent should do.

Good:

```markdown
- Do not hand-edit `internal/generated/`; regenerate it with `make generate` after changing `api/openapi.yaml`.
```

Bad:

```markdown
- This repository contains source code, tests, and documentation.
```

### 4.2 Evidence over inference

Every non-trivial rule must be supported by one of:

1. explicit user instruction for this task
2. current source, config, tests, scripts, or lockfiles
3. authoritative repository docs
4. existing `AGENTS.md` policy that is still valid
5. clearly labeled uncertainty or omission

Do not invent commands, owners, policies, generated boundaries, deployment behavior, or test conventions.

### 4.3 Smallest sufficient scope

Put a rule in the narrowest applicable `AGENTS.md`.

Root guidance applies repo-wide unless a nested `AGENTS.md` narrows or overrides a specific rule. Nested files add local constraints. Root files should not contain implementation-specific guidance for nested-owned surfaces unless the rule truly applies repo-wide.

### 4.4 Preserve before rewriting

For existing files, preserve valid guidance and make the smallest structure-preserving edit that satisfies the user request. Rewrite only when the user asks for a rewrite or the existing file is too stale to patch safely.

### 4.5 Safety by default

Never copy secrets, tokens, credentials, cookies, customer data, private endpoints, or ephemeral sensitive artifacts into `AGENTS.md` by default.

Document variable names, config files, safe retrieval locations, and maintainer-only boundaries instead.

Dangerous commands require more than user wording. Before documenting or running them, classify scope, target, reversibility, credentials, and environment.

## 5. Modes

### 5.1 Patch mode

Use for small edits to an existing `AGENTS.md` where no new repo-wide claims are introduced.

Required work:

- inspect the target section
- inspect current diff if git is available
- verify only touched paths, links, and commands
- preserve structure
- run quick structural validation and targeted semantic validation

### 5.2 Create mode

Use for a new root or nested `AGENTS.md`.

Required work:

- discover existing `AGENTS.md` files
- determine scope
- inspect root docs/config/source/tests enough to support the file
- draft the minimal viable file
- run structural and semantic validation

### 5.3 Update mode

Use for material updates to an existing file.

Required work:

- inspect existing file and current diff
- preserve valid policies
- remove stale factual claims
- verify new and changed claims
- avoid reordering unrelated sections unless required
- run validation

### 5.4 Split or move mode

Use when repository topology or nested scope changes.

Required work:

- map current and target scopes
- identify which rules move, which stay root-wide, and which are deleted
- avoid duplicate root/nested rules
- search for stale path references in in-scope files
- report out-of-scope stale references instead of editing broadly
- validate each affected `AGENTS.md`

### 5.5 Review mode

Use when the user asks for critique only.

Required work:

- do not edit files
- inspect enough evidence to support findings
- report findings by severity
- distinguish proven defects from risks and suggestions

### 5.6 Exhaustive mode

Use only for monorepos, operational repos, contract-heavy systems, safety-sensitive repos, or explicit user requests.

Required work:

- inventory every mentioned package/service/workflow
- validate nested scope boundaries
- run stale-reference searches
- classify unsafe operations
- report residual uncertainty

## 6. Required workflow

### Step 1: Establish intent and scope

Determine:

- requested mode
- target repository root
- target `AGENTS.md` path
- whether the file is root or nested
- whether existing `AGENTS.md` files apply
- whether the task is create, update, review, split, or move

If git is available, inspect:

```bash
git status --short
```

If the target file exists, inspect its current contents and any current diff before editing.

### Step 2: Gather evidence

Use the smallest evidence set that supports the requested change.

For normal create/update work, inspect:

- existing `AGENTS.md` files
- README or authoritative docs
- package/config/lock files
- task runner files
- CI workflows when local commands are unclear
- representative source files for mentioned areas
- tests and fixtures when test guidance will be included
- generated, vendored, migration, schema, or notebook boundaries when mentioned

For monorepos, inspect each package, app, or service that the `AGENTS.md` will name.

### Step 3: Classify evidence

Classify each candidate item as one of:

- context needed for behavior
- command contract
- edit boundary
- generated or read-only surface
- test/validation rule
- external dependency
- maintainer-only operation
- unsafe/destructive operation
- stale or conflicting claim
- not worth including

Only include items that affect future agent behavior.

### Step 4: Draft or patch

Use the minimal schema that fits the repo. Prefer fewer sections. Delete sections that would contain generic advice.

Do not include bracket placeholders, TODOs, TBDs, or speculative future guidance unless explicitly labeled planned and necessary for agent behavior.

### Step 5: Validate

Run structural validation.

Run semantic validation when the file mentions paths, links, commands, examples, generated files, nested scopes, or related docs.

Validation tools must be described as partial checks. Passing validation does not prove every factual claim.

### Step 6: Final response

For edit requests, final response must include:

- changed files
- major rules added, changed, or removed
- evidence sources sampled
- validation commands and results
- residual risks or unverified claims

For review-only requests, final response must use the review finding schema.

## 7. Target AGENTS.md schema

### Required for new files

```markdown
# Project Name

## Scope

## Context

## Project Rules
```

A new file should also include `Local Commands`, `Repository Map`, and `Testing` when verified facts exist. Do not add those sections when they would be empty or generic.

### Canonical optional sections

Use these section names when needed:

```markdown
## Scope
## Context
## Repository Map
## Local Commands
## Project Rules
## Testing
## Tool and Workflow Contracts
## Coordination
## Sensitive or Unsafe Operations
## Do / Don't
## Related Docs
```

Optional sections may be omitted. Existing valid section names may be preserved during patch mode.

## 8. Section contracts

### Scope

Must state where the file applies and how nested files interact.

Good:

```markdown
This file applies to the entire repository. `packages/api/AGENTS.md` adds API-specific rules for `packages/api/`.
```

### Context

Include only context that changes agent behavior:

- purpose in one sentence
- runtime/package manager from config
- primary entry point
- execution model
- package/app/service identity

Do not include a dependency inventory unless specific dependencies affect edits.

### Repository Map

Include only paths agents need to choose edit locations or avoid unsafe edits.

Good entries identify:

- source roots
- tests
- generated files
- migrations
- schemas
- public contracts
- read-only or owner-controlled surfaces

Do not list every directory.

### Local Commands

Include only commands verified by repo files or authoritative docs.

Each command should say when to use it, and whether it was executed during authoring if relevant.

Commands requiring credentials, containers, cloud state, production targets, destructive actions, migrations, or deployments must be marked `inspected-only` or `maintainer-only` unless the task explicitly requires deeper handling.

### Project Rules

Rules must be actionable and local.

Each rule should answer:

- What should the agent do or avoid?
- Where does the rule apply?
- What repo evidence supports it?
- What mistake does it prevent?

### Testing

Include test location, fixture pattern, and commands only when verified.

If no tests or test command exist, either omit `Testing` or state the verified absence only when it changes behavior.

### Tool and Workflow Contracts

Use only when the repo exposes stable CLIs, APIs, generated clients, data contracts, MCP servers, or agent workflows.

For each contract, include:

- source of truth
- inputs
- outputs
- safe normal path
- generated/read-only boundaries
- maintainer-only paths

### Coordination

Use only when durable status, handoff, memory, notes, or agent-owned sidecar files are part of the repo workflow.

Include:

- trusted inputs
- writable files
- read-only files
- refresh triggers
- conflict precedence

Do not copy the status content itself into `AGENTS.md`.

### Sensitive or Unsafe Operations

Use only when the repo has real sensitive operations.

Include:

- what agents may inspect
- what agents must not run
- what requires maintainer/user authorization
- safe dry-run alternatives
- where operational runbooks live

### Do / Don't

Use only when a small pair of examples prevents a recurring local mistake.

Examples should be copied or adapted from verified local patterns.

### Related Docs

Include only docs agents should read before specific kinds of edits.

Each item must include path and trigger.

Good:

```markdown
- `docs/release.md` - read before changing versioning, packaging, or publish workflows.
```

## 9. Rule quality rubric

A rule belongs in `AGENTS.md` only if it passes all required checks:

| Check | Requirement |
|---|---|
| Specific | Names a real local path, command, contract, or behavior. |
| Current | Matches the current repository state or is clearly labeled planned/external. |
| Scoped | Applies to the target file's scope. |
| Behavioral | Changes what a future agent should do. |
| Evidence-backed | Supported by repo evidence or explicit user instruction. |
| Safe | Does not expose secrets or encourage unsafe execution. |
| Non-duplicative | Not already covered by global instructions or a narrower `AGENTS.md`. |
| Concise | Says the rule once, without tutorial prose. |

## 10. Safety contract

### Secrets and sensitive data

Do not place secret values in `AGENTS.md`.

Forbidden by default:

- tokens
- passwords
- cookies
- private keys
- credentials
- customer data
- private endpoints not already intended for docs
- one-time challenge flags unless the repo explicitly stores them and the user asks

Allowed:

- environment variable names
- config file paths
- profile names when already documented
- local fixture names
- public endpoints already documented for development
- pointers to secure retrieval processes

### Dangerous commands

Classify these as maintainer-only or inspected-only unless explicitly required and safely scoped:

- deploys
- migrations
- destructive file deletes
- production operations
- credential operations
- live target interactions
- data deletion or truncation
- infrastructure mutation
- service resets
- non-dry-run replay against external targets

A user request alone is not enough for blind execution. The agent must know scope, target, environment, reversibility, and expected effect.

## 11. Tooling specification

### analyze_project.py

Purpose: produce an evidence inventory, not drafting suggestions.

Required output:

- schema version
- repo root
- discovered `AGENTS.md` files
- package/workspace map
- config files with paths
- detected languages with evidence
- package managers with evidence
- command inventory with source file and script/target name
- test command candidates with evidence
- generated/read-only candidates with evidence
- CI command hints
- uncertainty list
- skipped directories and sampling limits

Requirements:

- recursively discover common workspace configs
- parse `package.json`, `pyproject.toml`, `Makefile`, `justfile`, `Taskfile`, `tox.ini`, `noxfile.py`, `Cargo.toml`, `go.mod`, Gradle/Maven wrappers, solution files, Docker Compose, Terraform files, and CI workflows where feasible
- distinguish facts from guesses
- avoid generic writing suggestions
- resolve and contain every candidate before reading, exclude outside symlink/junction/reparse and hardlink aliases, and follow directory symlinks only when explicitly requested and still inside the root
- enforce deterministic directory, entry, per-file byte, and cumulative-byte limits, with machine-readable truncation and uncertainty reporting
- read candidate content as bounded strict UTF-8 and report malformed or binary candidates without tracebacks or content disclosure
- output stable JSON
- provide Markdown as a faithful rendering of JSON

### validate_agentsmd.py

Purpose: structural, style, and safety linting.

Required flags:

```bash
--repo-root <path>
--agents-file <path>
--intent create|update|review|split|move
--mode quick|standard|exhaustive
--json
```

Required checks:

- exactly one H1
- no unresolved placeholders, TODO, TBD, FIXME, or template comments
- no skipped heading levels
- known sections in reasonable order for new files
- required sections for create mode
- no generic filler rules
- reasonable length budget by mode
- related docs have path and trigger
- dangerous commands are labeled per command
- no obvious secret values
- no evidence-log sections unless repo-owned and requested
- when `--repo-root` is explicit, contain the target and optional evidence file before either is read

### semantic_check_agentsmd.py

Purpose: partial repository-backed checks.

Required checks:

- `AGENTS.md` path is inside `repo_root`
- Markdown links resolve and do not escape root
- anchors optionally resolve
- image links resolve when local
- inline path references resolve or are explicitly external/planned/pattern per reference
- absolute paths fail unless explicitly external/platform and not used as local edit targets
- symlinks cannot escape root silently
- glob patterns match or are explicitly documented as patterns
- command blocks are checked against command inventory where supported
- all command-chain segments are parsed
- examples in parseable languages are parsed unless they contain recognized placeholders
- root/nested duplicate scope checks cover project rules, commands, contracts, and edit boundaries

### run_fixture_checks.py

Purpose: maintenance smoke test wrapper.

Requirements:

- per-case timeout
- clear start/pass/fail/timeout lines
- JSON summary option
- nonzero exit on failure or timeout
- no dependence on current working directory
- fixture manifest file
- ability to run one fixture by name
- `--help` exits successfully without executing fixtures
- `--json` emits a deterministic, path-free summary with only case IDs, statuses, and counts
- duplicate, missing, and unknown `--fixture` inputs exit 2 with value-free errors

## 12. Test specification

Use a real test runner, preferably `pytest`, for helper scripts.

Required test categories:

- structural validation unit tests
- semantic path/link unit tests
- command parsing unit tests
- unsafe command classification tests
- analyzer inventory tests
- golden Markdown/JSON output tests
- fixture end-to-end tests
- monorepo/workspace tests
- nested `AGENTS.md` tests
- symlink escape tests
- hardlink and file-identity-change tests where the platform supports them
- invalid UTF-8, binary-control, per-file cap, cumulative-byte cap, directory cap, and entry cap tests
- JSON-only exit-2 envelope tests that assert outside absolute paths and input content are not disclosed
- performance/sampling tests
- safety/secret tests

Every shipped example should be either:

1. validated against a fixture, or
2. explicitly labeled illustrative and excluded from validated examples.

## 13. Maintenance contract

Before release, maintainers must run:

```bash
python scripts/check_agentsmd_templates.py .
python scripts/run_agentsmd_fixture_checks.py
pytest tests
```

A release is not acceptable unless:

- all tests pass
- fixture runner has no timeouts
- examples are validated or labeled illustrative
- `SKILL.md` references only existing files
- helper CLI docs match actual flags
- changelog notes behavior changes

## 14. Acceptance criteria for the ideal skill

The ideal version is mature when:

- a simple repo produces a short `AGENTS.md`, not a long project doc
- a monorepo produces correct root/nested scope boundaries
- no new file can pass validation with only a title and context
- stale paths are caught unless precisely labeled planned/external
- absolute local paths cannot pass silently
- common command ecosystems are inventoried or explicitly unsupported
- unsafe operations are classified with low false-positive and low false-negative rates
- helper scripts have unit tests and fixtures
- the fixture runner cannot hang indefinitely
- examples are validated
- the top-level workflow is short enough for agents to follow reliably
