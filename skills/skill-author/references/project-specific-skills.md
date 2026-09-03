# Project-Specific Skills

Use a project-specific skill only after inspecting the target project's local `.agents/skills`, `.claude/skills`, repository guidance, implementation, and tests.

Reject a shared project skill when the behavior already has a current owner. Do not create a second owner as a migration unless explicit authority covers an atomic retirement or redirect of the existing owner. Do not use semantic or prose heuristics to make this decision.

Absent explicit user selection, mark a project-specific skill only for a stable, repeatable workflow after the ownership gate above establishes that there is no current project owner.

Explicit user selection establishes demand without independent recurrence, but never overrides the existing-owner prohibition or its atomic retirement/redirect requirement.

Multiple child sessions from one orchestrated root are one demand signal, not independent recurrence. Do not replace this judgment with a numeric session threshold.

Opaque, redacted, hashed, or bucketed project keys support aggregation only; they are not project identity. Consider project-specific packaging only when trusted evidence contains the observed full absolute project path, that path resolves to the target project, and the ownership inspection at that resolved target has completed. Never infer a project path or name from session labels, workspace fragments, corpus buckets, or opaque keys. Without the trusted resolved path and ownership evidence, keep the behavior shared when it is reusable, or defer or discard the project-specific candidate.

From the `c:/Users/user/collab/agents` repository root, run `python -B skills/skill-author/scripts/scaffold_skill.py --name <skill-slug> --description "<description>" --root skills --project-root "<absolute-project-directory>"`. The scaffolder derives `<project-slug>-<skill-slug>` and writes `project-specific-skill`, which contains only the resolved project path. The marker establishes scope identity only. It grants no authority to read, edit, run, deploy, or otherwise act on the project.

The first nonblank body line immediately after `SKILL.md` YAML frontmatter must be exactly:

```markdown
[project-specific-skill](project-specific-skill)
```

No other placement counts. From the same repository root, validate the completed package with `python -B skills/skill-author/scripts/validate_skill.py skills/<project-slug>-<skill-slug> --strict`.

On platforms without `O_NOFOLLOW`, marker reads compare the entry before opening, the opened descriptor, and the entry after opening before reading bytes. A race can still make the operating-system open call follow a replacement, so the helper never treats this as a safe reader for external targets and fails before reading when identities differ.

Project-root validation lexically rejects ordinary symlink and reparse ancestors before normal descendant probes, but it assumes the path is not concurrently replaced. It is not a hostile concurrent-filesystem or no-network security boundary beyond rejecting direct UNC and Windows device path syntax before ordinary path checks.
