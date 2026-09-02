# Changelog and Release Notes Workflow

Use this guide for `CHANGELOG.md` and release notes.

## Preconditions

Before editing a changelog or release notes, verify all of the following:

- the current branch is not the default branch
- the working tree is clean
- the release or version bump already happened if you are documenting a release

If any precondition fails, stop and report it instead of drafting entries.

## Workflow

1. Decide whether the deliverable is a persistent `CHANGELOG.md`, one-off release notes, or an update to an existing release section.
2. Read the existing `CHANGELOG.md`, release notes, tags, GitHub releases, or package metadata used by the repo.
3. Identify the last release boundary from tags, releases, or the existing file.
4. Collect changes since that boundary from commits, merged PRs, and touched files. Verify anything user-facing against source or docs before writing it.
5. Group changes into user-facing entries. Use Keep a Changelog headings when the repo already uses them or when you are creating a new changelog.
6. Keep an `Unreleased` section only when the repo already uses one or when you are creating a new changelog.
7. Update comparison links only when the file already maintains them or the repo expects them.

## Writing Rules

- Write from the user's perspective, not as a commit log.
- Start entries with a verb and keep them to one line when possible.
- Collapse commit noise into grouped changes.
- Omit empty categories.
- Skip internal churn unless it changes user or developer workflow.
- Preserve the repo's existing changelog voice and heading names before introducing Keep a Changelog categories.
- Do not invent version numbers, release dates, PR numbers, or issue IDs. Use placeholders only if the user explicitly asked for a draft.
- For release notes, include migration or operator actions only when the change actually requires action.

## Useful Categories

Use the repo's categories first. For a new changelog, prefer only categories that have entries:

- `Added`
- `Changed`
- `Fixed`
- `Deprecated`
- `Removed`
- `Security`
- `Migration Notes`

## Review Pass

- Confirm every entry maps to a real change since the chosen boundary.
- Remove entries that are only refactors, formatting, or dependency churn with no user or operator impact.
- Verify links to commits, tags, releases, or PRs before finishing.
