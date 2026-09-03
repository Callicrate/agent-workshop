# Work Lifecycle Contracts

Use this reference when a prompt or agent system works from issues, PRs, labels, queues, or step-based work items.

## Claim And Status Contract

- Define the source of truth for available work before agents start: repository issues, PRs, queue files, or task records.
- Require deterministic helpers for work discovery, claim, status transition, and release. Do not rely on LLM prose scanning when labels, assignees, or step state determine ownership.
- Make helper output compact JSON when another agent consumes it. Avoid colorized progress text, banners, or mixed human commentary in machine-readable paths.
- Include stable fields such as `work_id`, `source_url`, `role`, `status`, `claimed_by`, `next_action`, and `blockers`.
- Define allowed status transitions, including how to mark `in_progress`, `blocked`, `ready_for_review`, and `done`.
- Require a repository-specific working directory for repo commands. Git history, branch, status, and diff commands must run from the target repository root, not from the orchestrator or parent workspace.

## Role Labels

- Assign one active owner for each work item or artifact.
- Make role labels operational: the label should decide which prompt, owner, or helper handles the item.
- If multiple labels apply, define a deterministic tie-breaker before the agent starts work.
- If a work item changes role midstream, require the current owner to update labels/status before handoff.

## Human Question Boundary

- Autonomous agents should ask humans only when progress requires human authority or unavailable facts.
- Do not create human-question issues unless the prompt explicitly instructs the agent to do so.
- Prefer a concrete blocker record over an open-ended question. Include the attempted checks, the exact missing decision, and the next command or file that is blocked.
- Do not stop at "needs review" unless acceptance criteria say that review is the terminal state.

## Completion Rule

- For agent-run tasks, `done` means the validation loop reached terminal success or a concrete blocker.
- A patch, generated artifact, or claimed issue is not complete until its acceptance check passes, is explicitly deferred by the prompt, or is blocked by permissions, ambiguous business logic, or platform failure.

Start from the [work lifecycle template](../templates/work-lifecycle-template.md) when claim and status rules need their own artifact.
