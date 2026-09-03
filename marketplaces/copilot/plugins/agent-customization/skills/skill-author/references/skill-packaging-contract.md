# Skill Packaging Contract

Use this when a skill mandates executable scripts, states an operational policy
in more than one place, or is packaged for a plugin surface. These rules prevent
the failure class where a skill reads well but cannot be executed deterministically.

## Mandatory Scripts Must Ship

If `SKILL.md` or a reference tells the agent to **run** a script, that script must
exist in the skill directory and be validated. A skill cannot depend on a script
that is not bundled.

- Every executable named in workflow text must resolve to a real file in the skill, not only appear as a markdown link.
- If a helper is genuinely optional, mark it optional and state the fallback behavior when it is absent.
- Do not describe deterministic tools the skill does not carry.

```markdown
<!-- WRONG - workflow mandates a script that is not in the skill -->
Run `scripts/validate_agentsmd.py --mode standard` before finishing.
<!-- (no scripts/validate_agentsmd.py exists in the skill) -->

<!-- CORRECT - the script exists, or the step is marked optional with a fallback -->
Run [scripts/validate_agentsmd.py](scripts/validate_agentsmd.py) before finishing.
<!-- If no local Python is available, fall back to the manual checklist in references/manual-review.md. -->
```

## Invocation Paths Must Be Explicit and Consistent

Every script command must state the working directory it runs from, and that
directory must be identical everywhere the command appears.

- Pick one execution root: the skill package, the target repository, or the current working directory. Name it.
- Do not drop or add the `scripts/` prefix between the workflow and the deterministic-tools table; inconsistent prefixes leave the agent guessing which directory to run from.

```markdown
<!-- WRONG - same tool, two invocation shapes, no stated cwd -->
Workflow: run `scripts/validate_agentsmd.py`
Tools table: `validate_agentsmd.py --mode standard`

<!-- CORRECT - one shape, explicit root -->
From the target repository root, run:
`python -B <skill-root>/scripts/validate_agentsmd.py --repo-root . --mode standard`
```

## One Policy, One Source of Truth

A single operational decision must be stated once and cross-referenced, never
restated with conflicting terms across `SKILL.md`, references, and assets.

- Example failure: the main workflow says do not persist an evidence sidecar in the target repo, while an asset says standard runs should keep a sidecar, and topology guidance says record results in the sidecar. These are incompatible as written.
- Fix: decide the policy in one place, then link to it. Other sections reference the decision rather than reasserting it.

## Packaged / Plugin Parity

When a skill is packaged for a plugin surface that also ships agents, resolve the
capability gap explicitly instead of leaving it implicit.

- Either give the packaged skill parity with its agent counterpart (model selection, persona/role composition where the surface supports it), or state the intended divergence ("plugin skills are static recipes; model and persona are not selectable here").
- Do not leave reviewers to infer whether a missing capability is a gap or a deliberate choice.

## Exact Named-Agent Dependencies

If advertised behavior requires an exact named agent profile, the installer or package must install that exact profile and verify both its name and selector support before the behavior is advertised. If either check fails, fail closed.

Do not substitute a default agent, worker, explorer, or another profile. Do not prompt-emulate the named profile as a fallback. The structural skill validator cannot enforce this rule because a skill directory has no packaging or selector context.
