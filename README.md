# Agent Workshop

One set of Agent Skills, authored once and installed as plugins across Claude
Code, Codex, and Copilot from a single git remote.

## What this solves

Every coding assistant wants skills in its own layout and its own marketplace
format, so sharing a skill across tools usually means copy-pasting the same
instructions into three global directories that immediately drift apart. This
repository removes that. A skill is written once under `skills/<name>/`, and a
projector generates the exact plugin payload and marketplace catalog each
provider expects. Add the marketplace once, install `<plugin>@callicrate`, and
the same reviewed skill runs in all three tools. There is no global-skill
deployer to keep in sync and nothing to hand-edit per tool.

## Architecture

```
skills/<name>/                      canonical source of truth
        |
        v   scripts/Sync-Plugins.ps1  (reads the working tree, hashes content)
        |
marketplaces/
  codex/plugins/<name>/             derivation source; others derive from it
  claude/plugins/<name>/            generated payload
  copilot/plugins/<name>/           generated payload
        |
        v
.claude-plugin/marketplace.json     Claude catalog   (root discovery path)
.agents/plugins/marketplace.json    Codex catalog     (root discovery path)
.github/plugin/marketplace.json     Copilot catalog   (root discovery path)
```

The projector copies each plugin's owning skill files into that plugin's
`skills/` directory, derives a content-based `version` cachebuster, and writes a
content-based `source-lock.json`. The Codex payload is the derivation source;
the Claude and Copilot payloads are generated from it, so the three providers
can never present different behavior for the same skill. The three catalog
manifests stay at their provider-required root paths so a plain `plugin
marketplace add <git-url>` discovers them; their entries point at
`./marketplaces/<provider>/plugins/<name>`.

## Design decisions

- **Content-hash projection, not git-object reads.** The projector reads the
  local working tree and identifies a projection by a hash of its *content*, so
  a plugin's version only changes when its content changes and the build is
  reproducible from a fresh clone rather than tied to git history.
- **Plugin-only install.** Everything ships through the provider marketplaces.
  There is no side channel that copies skills into a global directory, so the
  install path and the reviewed artifact are identical.
- **Line endings are normalized (`.gitattributes`).** Canonical and projected
  trees are pinned to LF so the content hashes a fresh clone computes match the
  committed locks on any platform. Without this, a Windows checkout silently
  rewrites line endings and the release check fails on clone but passes locally.
- **Apache-2.0**, for the explicit patent grant.

## Install (from git)

Add the marketplace once, then install any plugin as `<plugin>@callicrate`.

```bash
# Claude Code
claude plugin marketplace add https://github.com/Callicrate/agent-workshop
claude plugin install databricks@callicrate

# Codex
codex plugin marketplace add https://github.com/Callicrate/agent-workshop
codex plugin add databricks@callicrate

# Copilot
copilot plugin marketplace add https://github.com/Callicrate/agent-workshop
copilot plugin install databricks@callicrate
```

Start a new session after changing plugins so the client reloads their skills.

## Plugins

| Plugin | What it does |
|--------|--------------|
| `databricks` | Databricks workflows: asset bundles, model serving, batch inference, Spark ETL, ML training, runtime diagnostics, MLflow auditing. |
| `repository-docs` | Generate and maintain documentation, API docs, and `AGENTS.md` files. |
| `agent-customization` | Author and refine skills and agent prompts. |
| `review-and-learning` | Critically review work and extract durable lessons. |
| `python-local-dev` | Debug Python and run local projects reliably. |
| `frontend-product-ui` | Build and refine frontend product UI. |

## Authoring

1. Edit a canonical skill under `skills/<name>/`.
2. Map it to a plugin in `plugin-sources.json`.
3. Regenerate all provider payloads and verify:

   ```powershell
   ./scripts/Sync-Plugins.ps1          # all providers
   ./scripts/Sync-Plugins.ps1 -Check   # verify without writing
   ```

4. Commit the canonical change and the regenerated payloads together.

## The release check

`Sync-Plugins.ps1 -Check` is the gate. It re-derives every projection from the
canonical tree and fails if any committed payload, lock, or catalog is stale.
The contract is that it must pass **on a fresh clone**, not just in a working
tree, because line-ending normalization only diverges on checkout:

```
> git clone https://github.com/Callicrate/agent-workshop fresh && cd fresh
> ./scripts/Sync-Plugins.ps1 -Check
Plugin projections match local canonical sources: databricks, repository-docs,
  agent-customization, review-and-learning, python-local-dev, frontend-product-ui
```

A clean fresh-clone `-Check` (exit 0) is the definition of a shippable state.

## Layout

| Path | Role |
|------|------|
| `skills/<name>/` | Canonical Agent Skills - the single source of truth. |
| `instructions/` | Reusable instruction Markdown with scoped `applyTo` frontmatter. |
| `prompts/` | Reusable prompt Markdown. |
| `plugin-sources.json` | Skill-to-plugin mapping and publication policy. |
| `scripts/Sync-Plugins.ps1` | Projects `skills/` into all provider marketplaces. |
| `marketplaces/{claude,codex,copilot}/` | Generated per-provider plugin payloads. |
| `.claude-plugin/marketplace.json` | Claude catalog (root discovery path). |
| `.agents/plugins/marketplace.json` | Codex catalog (root discovery path). |
| `.github/plugin/marketplace.json` | Copilot catalog (root discovery path). |

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 Joel Callicrate.
