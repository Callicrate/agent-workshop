# Repository Docs Plugin

This plugin packages canonical workflows for repository documentation, API and
CLI documentation, and scoped `AGENTS.md` guidance.

## Skills

- `documentation-author`
- `api-documentation-author`
- `agents-md`

## Ownership

Edit these skills only in this repository's canonical `skills/` directory.
This directory is a generated, deployable projection, and `source-lock.json`
records its committed source revision. Do not edit files under this plugin's
`skills/` -- they are overwritten on the next projection.

## Synchronize

Run from the repository root:

```powershell
.\scripts\Sync-Plugins.ps1 -Plugin repository-docs
.\scripts\Sync-Plugins.ps1 -Plugin repository-docs -Check
```

## Install

```powershell
codex plugin add repository-docs@callicrate
```

Start a new Codex thread after installation or refresh.
