# Repository Docs Plugin

This plugin packages canonical workflows for repository documentation, API and CLI documentation, and scoped `AGENTS.md` guidance.

## Skills

- `make-documentation`
- `api-documentation`
- `agents-md`

## Ownership

Edit these skills only in the sibling AGENTS repository.
This directory is a generated deployable projection, and `source-lock.json` records its committed source revision.

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
