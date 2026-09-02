# Review and Learning Plugin

This plugin packages canonical workflows for skeptical review and evidence-backed lessons learned.

## Skills

- `critically-review`
- `extract-lessons-learned`

## Ownership

Edit these skills only in the sibling AGENTS repository.
This directory is a generated deployable projection, and `source-lock.json` records its committed source revision.

## Synchronize

Run from the repository root:

```powershell
.\scripts\Sync-Plugins.ps1 -Plugin review-and-learning
.\scripts\Sync-Plugins.ps1 -Plugin review-and-learning -Check
```

## Install

```powershell
codex plugin add review-and-learning@callicrate
```

Start a new Codex thread after installation or refresh.
