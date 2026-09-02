# AGENTS - callicrate

Canonical source for shared AI-agent skills and workflows, published as
installable plugins across Claude Code, Codex, and Copilot.

## Scope

These rules apply to this repository unless a nested `AGENTS.md` gives more
specific guidance. `skills/AGENTS.md`, when present, owns skill authoring and
validation guidance.

## Repository Map

| Path | Purpose |
|------|---------|
| `skills/` | Agent Skills, each under `skills/<skill-name>/`. Single source of truth. |
| `instructions/` | Flat instruction Markdown files with scoped `applyTo` frontmatter. |
| `prompts/` | Flat reusable prompt Markdown files. |
| `plugin-sources.json` | Skill-to-plugin mapping and publication policy. |
| `scripts/` | `Sync-Plugins.ps1` and `Sync-CodexMarketplace.ps1` marketplace projectors. |
| `marketplaces/{claude,codex,copilot}/` | Generated per-provider plugin payloads (do not hand-edit). |

Do not create alternate top-level folders for these concerns unless the owning
docs are updated in the same change.

## Local Commands

Run from the repository root.

```powershell
# Project canonical skills into all provider marketplaces (-Check previews only).
./scripts/Sync-Plugins.ps1
./scripts/Sync-Plugins.ps1 -Check
```

Assets install as plugins from git, not through a global deployer:

```powershell
codex plugin marketplace add <git-url>
codex plugin add databricks@callicrate
```

This workspace is a multi-surface customization repository, not one deployable
app; do not invent a repository-wide install, lint, or test command.

## Project Rules

- Skill-to-plugin mapping lives in `plugin-sources.json`; generated payloads
  live under `marketplaces/<provider>/`. Edit ownership there, then run
  `scripts/Sync-Plugins.ps1`.
- Keep `prompts/` and `instructions/` flat. If a prompt needs bundled material,
  convert the workflow into a skill.
- The three root catalogs (`.claude-plugin/marketplace.json`,
  `.agents/plugins/marketplace.json`, `.github/plugin/marketplace.json`) are the
  provider discovery manifests. Catalog source paths take the form
  `./marketplaces/<provider>/plugins/<plugin-name>`.
- Keep `README.md` and this file aligned when the top-level folder contract
  changes.

## Do / Don't

### Do

- Edit canonical skills under `skills/<name>/`, then regenerate payloads.
- Use relative, slash-separated paths in repository docs.
- Keep secrets, tokens, local environment files, caches, and generated
  dependency folders out of tracked files.

### Don't

- Hand-edit generated payloads under `marketplaces/<provider>/`.
- Add new prompt subfolders.
- Commit local runtime output such as logs, environment files, virtual
  environments, caches, or build output.

## Related Docs

- [README.md](README.md) - overview and install instructions.
