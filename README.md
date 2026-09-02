# callicrate

Agent skills and modular workflows for AI coding assistants, published as
installable plugins from git. One catalog, three providers: Claude Code,
Codex, and Copilot.

Skills are authored once under `skills/<name>/` and projected into per-provider
plugin payloads under `marketplaces/`. Everything ships as plugins installed
from git — there is no global-skill deployer.

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

## Layout

| Path | Role |
|------|------|
| `skills/<name>/` | Canonical Agent Skills — the single source of truth. |
| `instructions/` | Reusable instruction Markdown with scoped `applyTo` frontmatter. |
| `prompts/` | Reusable prompt Markdown. |
| `plugin-sources.json` | Skill-to-plugin mapping and publication policy. |
| `scripts/Sync-Plugins.ps1` | Projects `skills/` into all provider marketplaces. |
| `scripts/Sync-CodexMarketplace.ps1` | Codex projector (reads the local `skills/` tree). |
| `marketplaces/{claude,codex,copilot}/` | Generated per-provider plugin payloads. |
| `.claude-plugin/marketplace.json` | Claude catalog (root discovery path). |
| `.agents/plugins/marketplace.json` | Codex catalog (root discovery path). |
| `.github/plugin/marketplace.json` | Copilot catalog (root discovery path). |

The three catalog manifests stay at their provider-required root paths so
`plugin marketplace add <git-url>` discovers them. Their entries point at the
payloads under `./marketplaces/<provider>/plugins/<name>`.

## Authoring

1. Edit a canonical skill under `skills/<name>/`.
2. Map it to a plugin in `plugin-sources.json`.
3. Regenerate all provider payloads:

   ```powershell
   ./scripts/Sync-Plugins.ps1          # all providers
   ./scripts/Sync-Plugins.ps1 -Check   # verify without writing
   ```

4. Commit the canonical change and the regenerated payloads together.

The projector reads the local working tree, copies each plugin's owning skill
files into its `skills/` directory, derives a content-based `version`
cachebuster, and writes a content-based `source-lock.json`. Git provides
version control and provenance.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
