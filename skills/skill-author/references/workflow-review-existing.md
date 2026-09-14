# Workflow: Review an Existing Skill

Use this path for review-only requests or before making larger changes.

## Review Order

### 1. Read the Skill Completely

- Read `SKILL.md` end to end.
- Read linked files in `references/`, `scripts/`, `assets/`, `templates/`, and `agents/openai.yaml` that affect behavior.

### 2. Check Discovery and Routing

- `name` matches the folder name.
- `description` clearly states WHAT the skill does, includes `Use when ...`, and includes explicit exclusions where adjacent skills could false-positive.
- `SKILL.md` routes quickly to the right workflow instead of carrying all detail inline.
- If reviewing all skills or split/merge candidates, run [../scripts/inventory_skills.py](../scripts/inventory_skills.py) and [../scripts/audit_skill_overlap.py](../scripts/audit_skill_overlap.py) to ground the review in current descriptions and resources.
- Treat the inventory's discovery-budget result as a model of listed field values only. It is nonblocking and does not prove that a host omitted a skill.

### 3. Check Separation and Necessity

- `SKILL.md` stays concise and does not duplicate detailed references.
- Distinct tasks have separate `references/workflow-*.md` files.
- `assets/`, `scripts/`, `templates/`, and `agents/` exist only when they are used.
- `agents/openai.yaml`, if present, contains UI/runtime metadata only, not skill instructions or trigger conditions.
- Repeated checks or transformations are handled by deterministic helpers when practical.
- Public tool, MCP, script, or command guidance hides implementation machinery from agents unless the distinction is useful to the caller.
- Claimed simplification is backed by before and after counts for public concepts, not only internal implementation changes.

### 4. Run Deterministic Checks

```powershell
c:/Users/user/collab/agents/.venv/Scripts/python.exe -B skills/skill-author/scripts/validate_skill.py <skill-dir> --strict
```

- Treat validation failures as findings.
- If the skill includes Python helper scripts, require syntax validation to pass.
- If the skill includes shell helpers, check line endings and shell syntax (`bash -n` for Bash scripts).

### 5. Report

- Present findings ordered by severity with concrete file references.
- Do not edit the skill unless the user asked for fixes.
- If the user asked for both review and fixes, apply the smallest change set and then run [workflow-finalize-skill.md](workflow-finalize-skill.md).
