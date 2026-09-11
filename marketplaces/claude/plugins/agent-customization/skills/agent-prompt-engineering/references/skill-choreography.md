# Skill Choreography

Use this when a task naturally touches multiple skills.

## Primary Owner

Pick one primary skill that owns the final deliverable and completion loop. Supporting skills may provide focused checks or implementation details, but the primary skill resumes control after each handoff.

Name:

- Primary skill
- Supporting skill, if any
- Why the supporting skill changes the work
- Return condition from the support step
- Final validation owned by the primary skill

## Handoff Rules

- Do not run two skills as equal owners for the same deliverable.
- Use support skills for bounded subtasks: command execution, runtime diagnosis, API call shape, prompt audit, UI QA, or deployment verification.
- Return to the primary skill after the support step produces a result, patch, diagnosis, or blocker.
- If the support skill changes files, the primary skill integrates and validates the overall delivery unit.
- When reviewing this skill or a prompt about choreography, treat this file as routing guidance only. Use independent evidence or an external review artifact for acceptance criteria.

## Common Patterns

- `databricks-deploy-monitor` primary with `databricks-spark-etl`, `databricks-ml-training`, or `databricks-runtime-doctor` as support after a live run failure is localized.
- `frontend-product-ui` primary with `local-project-execution` as support only when app startup or build/test execution is unclear, failing at the command surface, crossing shells, or long-running.
- `extract-lessons-learned` primary with `skill-author` as support when an evidence-backed skill update is approved.
- `agent-prompt-engineering` primary with `critically-review` as support for skeptical review of a finished prompt or coordination contract.

## Anti-Patterns

- Creating a new skill when an existing primary/support pair would cover the task.
- Updating several skills with duplicated wording instead of one shared rule plus local consequences.
- Ending after the support step without returning to the primary completion condition.
