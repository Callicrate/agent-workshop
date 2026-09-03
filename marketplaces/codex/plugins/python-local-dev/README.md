# Python Local Development Plugin

This plugin packages canonical workflows for debugging Python and repairing local shell, interpreter, package-manager, build, and test command surfaces.

## Skills

- `python-debugging`
- `local-project-execution`

## Ownership

Edit these skills only in the sibling AGENTS repository.
This directory is a generated deployable projection, and `source-lock.json` records its committed source revision.

## Synchronize

Run from the repository root:

```powershell
.\scripts\Sync-Plugins.ps1 -Plugin python-local-dev
.\scripts\Sync-Plugins.ps1 -Plugin python-local-dev -Check
```

## Install

```powershell
codex plugin add python-local-dev@callicrate
```

Start a new Codex thread after installation or refresh.
