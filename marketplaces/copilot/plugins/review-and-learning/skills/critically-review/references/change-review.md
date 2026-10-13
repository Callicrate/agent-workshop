# Change Review Before Commit

Use this when the user asks what changed, whether a change is ready, or how to explain a working tree before commit.

## Inputs

Prefer deterministic local evidence:

- `git status --short`
- `git diff --stat`
- `git diff`
- focused test/build outputs already run for the change
- nearby task notes, issue text, or user instructions

Do not revert or rewrite unrelated user changes while reviewing.

## Review Shape

Lead with findings when there are defects or risks. Otherwise provide a concise change explanation:

- What changed, grouped by behavior or subsystem
- Why the change appears to have been made
- User-visible or operational impact
- Risk areas and assumptions
- Validation already run
- Validation still missing
- Suggested commit split when the tree contains separable work

## Skeptical Checks

- Does the diff actually implement the stated purpose?
- Are tests or generated artifacts consistent with source changes?
- Are unrelated files mixed into the same commit?
- Are secrets, local paths, environment-specific settings, or large artifacts accidentally included?
- Is there a behavior change that deserves docs, migration notes, or release notes?

## Output Rules

- Use exact file paths and line references when discussing concrete risks.
- Separate observed evidence from inference.
- Keep summaries brief; this mode is for decision support before commit, not a full narrative changelog.
