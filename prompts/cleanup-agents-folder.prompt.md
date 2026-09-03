---
name: clean-agents-folder-π
description: Audit and clean up the agents customization folder. Use when reviewing instructions, prompts, skills, and MCP server folders for standards compliance, removing non-compliant structures, fixing frontmatter, and consolidating orphaned content.
agent: agent
model: Claude Opus 4.6
tools: [agent, edit, execute, vscode/memory, read, search, todo]
---

# Clean Up Agents Folder

Audit the shared agents customization folder for standards compliance and clean up non-compliant structures. All paths below are relative to the agents repo root.

## Target Directory Structure

```text
<agents-repo>/
+-- instructions/              # Flat .instructions.md files
+-- prompts/                   # Flat .prompt.md files only, no subfolders
+-- skills/<skill-name>/       # Skill folders with SKILL.md + references/scripts/assets
+-- roles/                     # Canonical roles + deterministic provider packages
+-- mcp/<server-name>/             # MCP client connection docs
+-- mcp/<server-name>/<transport>/ # stdio, HTTP, or streaming MCP server implementation
+-- hooks/                     # Hook JSON files
+-- hooks.json                 # Global hooks config
+-- global_instructions/       # Always-on instructions (legacy)
+-- userdata/                  # Shared source material and config
```

## Standards Reference

### Instructions (`instructions/*.instructions.md`)

| Requirement | Check |
|-------------|-------|
| Location | Must be flat files in `instructions/`, no subfolders |
| Frontmatter | Must have `applyTo` or `description` (ideally both) |
| applyTo scope | Should NOT use `"**"` unless truly applies to all files |
| One concern | Each file covers one topic (don't mix testing + API + styling) |

### Prompts (`prompts/*.prompt.md`)

| Requirement | Check |
|-------------|-------|
| Location | Must be flat `.prompt.md` files in `prompts/`; **NO SUBFOLDERS** |
| Single task | Each prompt does one focused task |
| Frontmatter | Valid YAML with at least `description` |
| No assets | Prompts don't have bundled assets; that's what skills are for |

**CRITICAL**: Remove or relocate any subfolders in `prompts/`. Folders like `assets/`, `references/`, `templates/` belong in skills, not prompts.

### Skills (`skills/<name>/SKILL.md`)

| Requirement | Check |
|-------------|-------|
| Folder name | Must match `name:` field in frontmatter |
| Required file | `SKILL.md` at root of skill folder |
| Frontmatter `metadata` | Should carry `author`, `version`, and `source` fields (see existing skills for the shape) |
| Description | Must have keyword-rich description for discovery |
| Progressive loading | SKILL.md should be under 500 lines; use references/ for details |

### MCP Servers (`mcp/<server-name>/`, `mcp/<server-name>/<transport>/`)

| Requirement | Check |
|-------------|-------|
| Folder name | Must be lowercase kebab-case and describe the server capability |
| Scope | One MCP server or tightly related server family per folder |
| Documentation | Should include `README.md` or `AGENTS.md` with setup, tool contracts, and validation commands |
| Secrets | Must not include tokens, `.env` files, local caches, generated dependency folders, or runtime logs |
| Deterministic workflows | Prefer reusable scripts or MCP tools over repeated ad hoc shell command sequences |

## Audit Workflow

### Phase 1: Discovery

1. Create a TODO list tracking all phases.
2. List all files in `instructions/`, `prompts/`, `skills/`, and `mcp/`.
3. Identify:
   - Files without required frontmatter
   - Files in wrong locations
   - Subfolders where flat files are expected
   - Skills missing required components
   - Orphaned content that doesn't fit any category
   - Duplicate content across multiple files

### Phase 2: Prompts Folder Flat-Structure Check

The prompts folder must stay flat: only `.prompt.md` files, no subfolders. Treat this as a guardrail that runs every audit, not a known violation.

1. List any subfolders or non-`.prompt.md` files in `prompts/`. If none, record "clean" in the report and skip to Phase 3.
2. If subfolders exist, for each item inside:
   - **Reference doc** -> move to the most relevant skill's `references/` folder, or delete if redundant.
   - **Template / asset** -> move to the relevant skill's `templates/` or `assets/` folder.
   - **Prompt-specific context** -> inline into the prompt, or convert the prompt to a skill.
3. Delete empty subfolders after relocating content.
4. Verify `prompts/` contains only `.prompt.md` files and no directories.
5. Also check each prompt's frontmatter carries `name`, `description`, `agent`, `model`, and `tools` (matching existing prompts in the folder as reference).

### Phase 3: Instructions Audit

1. Check each `.instructions.md` file has valid frontmatter.
2. Flag any using `applyTo: "**"`; confirm it truly applies universally or narrow the scope.
3. Check for missing `description` fields (needed for on-demand discovery).
4. Flag instructions that mix multiple concerns.

### Phase 4: Skills Audit

For each skill folder:

1. Verify `SKILL.md` exists and `name:` matches folder name.
2. Verify `description:` is present and keyword-rich.
3. Verify the `metadata:` block carries `author`, `version`, and `source`.
4. Verify `SKILL.md` carries required task context or links to relevant resources.
5. Run validation from the repo root: `python skills/skill-author/scripts/validate_skill.py skills/<skill-name>`.
6. Flag skills over 500 lines; split detail into `references/` files.

### Phase 5: MCP Folder Audit

For each MCP server folder:

1. Verify the folder name is lowercase kebab-case.
2. Verify the folder documents when agents should use the MCP interface instead of shell commands.
3. Verify deterministic workflows are exposed through reusable scripts, MCP tools, or documented commands.
4. Verify setup, environment variables, and validation commands are documented with redacted examples only.
5. Flag secrets, `.env` files, generated dependency folders, caches, virtual environments, logs, and machine-specific runtime state.

### Phase 6: Cross-Cutting Cleanup

1. Search for duplicated paragraphs across `instructions/`, `prompts/`, `skills/`, `roles/`, and `mcp/` using ripgrep on distinctive phrases (e.g. `rg -n "distinctive phrase" instructions/ prompts/ skills/ roles/ mcp/`). Generated role projections are intentional copies and should be checked by manifest rather than deduplicated manually.
2. Identify orphaned files that don't fit any category.
3. Check for stale references; for each internal link `(path)`, confirm the target exists.
4. Verify no junk files are tracked: `.DS_Store`, `Thumbs.db`, `__pycache__/`, `.pyc` files.

### Phase 7: Fixes

Apply fixes in order of severity:

1. **Critical**: Remove/relocate subfolders from prompts/
2. **High**: Add missing frontmatter fields
3. **High**: Fix skill name mismatches
4. **Medium**: Narrow overly broad applyTo patterns
5. **Medium**: Add missing MCP server docs or move misplaced MCP server content under `mcp/` or `mcp/`
6. **Low**: Remove duplicate content

### Phase 8: Report

Write the report to `cleanup-reports/agents-cleanup-<YYYY-MM-DD>.md` (create the folder if missing). Include:

- Files modified
- Files deleted
- Content relocated (from -> to)
- Remaining issues needing manual review
- Recommendations for future organization
- Exit status (see "Done when" below)

### Done when

- `prompts/` contains only flat `.prompt.md` files with valid frontmatter.
- Every `instructions/*.instructions.md` has valid frontmatter with a narrow `applyTo` (no bare `**` unless justified in the file) and a `description`.
- Every skill passes `validate_skill.py`.
- Every MCP server folder has documentation for setup, tool contracts, and validation.
- The role catalog validates, generated provider packages are current, and consumer drift checks pass.
- No stale internal links, no junk files (`.DS_Store`, `Thumbs.db`, `__pycache__/`, `*.pyc`) tracked.
- Report written to the path above.

## Decision Matrix: When to Delete vs Relocate

| Content Type | Current Location | Action |
|--------------|------------------|--------|
| Reference doc | `prompts/references/` | Move to relevant skill's `references/` |
| Template file | `prompts/assets/` | Move to relevant skill's `templates/` or `assets/` |
| Prompt-specific context | `prompts/<subfolder>/` | Inline into the prompt or convert to skill |
| MCP client docs | Anywhere outside `mcp/` | Move to `mcp/<server-name>/README.md` unless it is intentionally project-local |
| Stdio, HTTP, or streaming MCP server code | Anywhere outside `mcp/` | Move to `mcp/<server-name>/<transport>/` unless it is intentionally project-local |
| Orphaned utility | Anywhere | If used by skills or MCP servers, move to `userdata/` or the relevant MCP server folder; otherwise delete |
| Duplicate content | Multiple locations | Keep in most authoritative location, delete copies |

## Safety Rules

- **Never delete** a file without first checking for references. Use ripgrep: `rg -n "<basename-without-extension>" instructions/ prompts/ skills/ mcp/ mcp/ userdata/ global_instructions/`. If anything matches, resolve those callers before deletion.
- **Always create** the destination before moving content.
- **Verify** moves completed successfully before deleting source.
- **Report** any files you couldn't safely relocate.
- **Skip** files that require user decision (flag for manual review instead).
- **Do not touch** the `hooks/` directory or `hooks.json`; they are out of scope for this audit.

## Commands

Run these from the agents repo root:

```powershell
# Find any file in prompts/ whose name isn't *.prompt.md
Get-ChildItem -Path "prompts" -File -Recurse | Where-Object { $_.Name -notmatch '\.prompt\.md$' }

# Find subfolders in prompts (should return nothing)
Get-ChildItem -Path "prompts" -Directory

# Validate every skill (one validator, many targets)
Get-ChildItem -Path "skills" -Directory | ForEach-Object { python "skills/skill-author/scripts/validate_skill.py" $_.FullName }

# List MCP server folders and suspicious local-only files
Get-ChildItem -Path "mcp" -Directory
Get-ChildItem -Path "mcp" -Recurse -Force -Include ".env","*.local.env","*.log","node_modules",".venv","__pycache__" -ErrorAction SilentlyContinue

# Find tracked junk files
Get-ChildItem -Path "." -Recurse -Force -Include ".DS_Store","Thumbs.db","*.pyc" -ErrorAction SilentlyContinue
Get-ChildItem -Path "." -Recurse -Directory -Force -Filter "__pycache__" -ErrorAction SilentlyContinue
```

