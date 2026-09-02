# Agent Customization Plugin

This plugin packages canonical workflows for authoring Agent Skills, prompts, role specifications, handoffs, and standalone agent instructions.

## Skills

- `agent-prompt-engineering`
- `skill-author`

`agent-deployment-sync` is intentionally excluded because its deployer and documentation dependencies are not self-contained under its skill directory.

## Ownership

Edit these skills only in the sibling AGENTS repository.
This directory is a generated deployable projection, and `source-lock.json` records its committed source revision.

## Synchronize

Run from the repository root:

```powershell
.\scripts\Sync-Plugins.ps1 -Plugin agent-customization
.\scripts\Sync-Plugins.ps1 -Plugin agent-customization -Check
```

## Install

```powershell
codex plugin add agent-customization@callicrate
```

Start a new Codex thread after installation or refresh.
