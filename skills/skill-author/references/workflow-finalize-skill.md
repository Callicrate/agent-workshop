# Workflow: Finalize a Skill

Use this path after creating or updating a skill, or after a review that resulted in edits.

## Steps

### 1. Remove Unnecessary Content

- Cut provenance, history, rationale, and commentary that do not tell the agent what to do.
- Keep `SKILL.md` as a router; keep detailed procedure in `references/`.
- Remove empty `assets/`, `scripts/`, `templates/`, and `agents/` directories.

### 2. Confirm Standalone Skill Context

- Keep required task context in `SKILL.md` or directly linked references.
- State skill-specific exceptions where they apply instead of relying on hidden shared context.
- Verify every relative link resolves.

### 3. Run Strict Validation

```powershell
c:/Users/user/collab/agents/.venv/Scripts/python.exe -B skills/skill-author/scripts/validate_skill.py <skill-dir> --strict
```

### 4. Conditionally Forward-Test Interpretation-Dependent Behavior

After strict validation, read and apply [behavioral-forward-testing.md](behavioral-forward-testing.md) when acceptance depends on a model interpretation or choice.
Skip behavior-neutral or deterministically enforced edits, and record the reason in the task checkpoint or final report.

### 5. Smoke-Test Changed Scripts

- Strict validation already compiles Python helpers in memory. Do not run `py_compile`; use `python -B <script> --help` when the helper exposes help, or the smallest documented fixture or smoke invocation.
- If you changed a shell helper, verify line endings match repository policy and run `bash -n <script>` for Bash scripts before finishing.

### 6. Report

- State what changed.
- State validation results.
- State anything still unverified.
