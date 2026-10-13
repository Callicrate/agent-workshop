# AGENTS - Agent Workshop

## Scope

These rules apply to this repository unless a nested `AGENTS.md` gives more
specific guidance. Files under `skills/agents-md/tests/fixtures/` are validator
data, not live repository guidance.

## Context

This repository is the public, artifact-only distribution of installable agent
plugins for Claude Code, Codex, and Copilot. It contains inspectable skill
sources and pre-generated provider payloads. Repository-maintenance projectors
are intentionally not shipped or executed during installation.

## Repository Map

| Path | Purpose |
|------|---------|
| `skills/` | Inspectable Agent Skill sources. |
| `instructions/` | Flat instruction Markdown files with scoped `applyTo` frontmatter. |
| `prompts/` | Flat reusable prompt Markdown files. |
| `plugin-sources.json` | Published snapshot of skill-to-plugin ownership. |
| `marketplaces/claude/`, `marketplaces/codex/`, `marketplaces/copilot/` | Generated provider plugin payloads. |
| `.claude-plugin/`, `.agents/`, `.github/` | Generated provider discovery catalogs. |

Root-level repository-maintenance scripts and local projection commands are
intentionally absent.

## Project Rules

- Treat files under `marketplaces/`, including nested source locks and plugin
  manifests, as generated release artifacts. Do not hand-edit those files.
- Make proposed content changes under `skills/`, `instructions/`, or `prompts/`.
  The maintainer release pipeline regenerates provider payloads.
- Keep `prompts/` and `instructions/` flat.
- Do not add local projector, installer, deployment, or synchronization scripts.
- Keep `README.md` and this file aligned when the public layout changes.

## Tool and Workflow Contracts

- Plugin installation uses the provider CLI commands documented in `README.md`.
- Installation reads the committed marketplace catalogs and plugin payloads; it
  does not execute repository-maintenance scripts.
- If a requested change requires regenerated payloads, make the source change
  and state that maintainer-side projection remains required. Do not simulate
  projection by copying files among provider directories.

## Do / Don't

### Do

- Keep source changes confined to the corresponding source directory.
- Use relative, slash-separated paths in repository docs.
- Keep secrets, tokens, local environment files, caches, and generated
  dependency folders out of tracked files.

### Don't

- Hand-edit generated payloads under `marketplaces/<provider>/`.
- Reintroduce root PowerShell projectors or tell users to bypass execution
  policy.
- Add new prompt subfolders.
- Commit local runtime output such as logs, environment files, virtual
  environments, caches, or build output.

## Related Docs

- [README.md](README.md) - overview and install instructions.
