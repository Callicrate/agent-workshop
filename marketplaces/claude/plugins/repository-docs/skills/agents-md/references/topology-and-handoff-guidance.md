# Topology And Handoff Guidance

Use this only when `AGENTS.md` defines repository layout, canonical paths, transport folders, generated file locations, or handoff files. For normal authoring, use [normal-authoring.md](normal-authoring.md).

## Topology Change Checklist

When a task changes repository structure or canonical paths:

- audit the actual tree, not just the files currently open
- update root `AGENTS.md`
- identify adjacent READMEs, architecture docs, skill references, prompt references, scripts, tests, examples, and task docs that mention the moved surface; update only files in scope for the task, otherwise report stale references in the final response
- run a stale-reference search for old folder names, command names, and path examples
- record search patterns and results in working evidence notes and summarize them in the final response

Directory-contract changes are not complete until examples match all cases, including transport-specific implementation folders.
For adjacent README or architecture edits in a mixed documentation task, make only the minimal consistency edits explicitly required by the user or coordinate with the relevant documentation workflow.

## Handoff File Ownership

If user instructions or repo docs name status, architecture, notes, memory, or handoff files, `AGENTS.md` must identify:

- trusted input files agents must read before acting
- read-only files that must not be edited
- agent-owned sidecar or mirror files that may be updated
- shared state files and expected update format
- refresh cadence or change-detection rule
- precedence order when sources disagree

Use this precedence unless the repo has a stronger rule:

1. User message and explicitly named local status files
2. Live platform or implementation state
3. Repository docs and architecture notes
4. Memory store or historical summaries
5. Inferred history

Local repo notes are not less authoritative just because a memory store or tool cache exists.

When `AGENTS.md` itself moves or is split, state the scope of each file.
Do not duplicate a narrower rule in the root file unless it applies repo-wide.

## Sidecar Notes Pattern

When a shared coordination file is read-only for the current agent, document the agent-owned mirror or sidecar path.
The sidecar should state what was copied, what fresh evidence was added, and what still belongs to the read-only owner.
This is separate from authoring evidence notes, which should remain transient unless the user or repo contract asks for a repository evidence file.
