# AGENTS.md Writing Tips

Use these checks after drafting an AGENTS.md file.
The goal is local, evidence-backed guidance, not a second copy of global coding standards.

## Evidence Test

Every rule should answer two questions:

- Where did this come from in the repository?
- What mistake would an agent make if this rule were absent?

Prefer rules tied to concrete files, commands, directories, schemas, config, or recurring failures.
For standard or exhaustive runs, keep traceable sources in transient working notes by default, not in the delivered `AGENTS.md`.
Create a repository evidence sidecar only when the user asks for one or the repository already defines an agent-owned evidence file.
Delete rules that only say what a competent engineer already knows.
Generic filler includes phrases such as "write clean code," "follow best practices," "add tests when appropriate," "handle errors properly," and "use meaningful names" when they are not tied to concrete local files, commands, configs, or recurring failures.

## Before / After: Context

**Before:**

```markdown
## Context
This is a Python project using modern tooling.
```

**After:**

```markdown
## Context
- **Runtime**: Python 3.12 from `.python-version`
- **Package manager**: `uv` from `uv.lock`
- **Primary entry point**: `src/acme/cli.py`, exposed as `acme` in `pyproject.toml`
- **Tests**: `pytest` with fixtures in `tests/conftest.py`
```

## Before / After: Project Rules

**Before:**

```markdown
## Code Style
Use type hints and keep code clean.
```

**After:**

```markdown
## Project Rules
- Keep generated API clients under `src/acme/generated/`; do not hand-edit them.
- Add new CLI subcommands in `src/acme/commands/` and register them in `src/acme/cli.py`.
- Integration tests need `ACME_TEST_PROJECT`; otherwise run only `pytest tests/unit`.
```

## Before / After: Do / Don't

**Before:**

```markdown
## Do / Don't
- Do use dependency injection.
- Don't use global state.
```

**After:**

````markdown
## Do / Don't

### Do

```python
service = BillingService(repo=BillingRepository(session), clock=FrozenClock(now))
```

### Don't

```python
service = BillingService()  # Opens its own database connection and reads wall-clock time.
```
````

## Deduplication Checklist

- [ ] The rule is specific to this repository, not a general language default.
- [ ] The rule is not already covered by workspace instructions or a loaded skill.
- [ ] The rule cites or implies a concrete source such as config, code, tests, or docs.
- [ ] Working evidence notes record sampled paths and sources for non-trivial rules.
- [ ] Commands match the repo's lockfile, package manager, and task runner.
- [ ] Examples are small, paired, and directly reusable.
- [ ] Examples are copied from or adapted from verified local code. If no local example exists, omit the example.
- [ ] The file is short enough for agents to load every session. Prefer 100-200 lines for normal repos, and exceed that only for monorepos, operational repos, or contract-heavy systems.
