# Workflow: Large Batch Skill Work

Use this for multi-skill reviews, large prompt-to-skill conversions, corpus-to-guidance extraction, or any request likely to hit rate limits, no-response failures, or length limits.

## Batch Plan First

Before creating or rewriting large artifacts, produce a bounded plan:

- source inventory: files, folders, evidence slices, or skills to inspect
- output inventory: files expected to be created or changed
- batch size: one skill, one workflow, or one artifact group at a time
- validation after each batch
- checkpoint location or status note
- stop condition and resume instruction

Do not attempt a whole directory, corpus, or prompt suite in one model response.

## Parallel Worker Gate

Before a parallel wave starts, name the coordinator, the immutable input boundary, and one exclusive output path for each worker.

- Use parallel workers for inventory, evidence review, disjoint draft artifacts, or declared disjoint canonical skill folders. Do not let two workers edit the same canonical skill folder or reference file.
- For an external worker that cannot load this skill, give it a compact run contract with the applicable source, ownership, validation, and stop rules. Treat that contract as fixed for the wave.
- Keep draft promotion and edits within one canonical skill folder single-writer. A coordinator must review the completed worker outputs and assign each canonical destination before its designated owner promotes the change; designated owners may then work in parallel on disjoint skill folders.
- Validate the canonical source after promotion. Treat installed copies and generated marketplace payloads as deployment targets, never as worker-edit targets or proof that the canonical source changed.
- Record each worker's input boundary, output path, claim, evidence location, and validation result in the checkpoint. Do not promote an unreviewed draft.

## Resumable Checkpoints

After each batch, record:

- completed files
- validation commands run
- failed or skipped files
- next batch
- assumptions that still need verification

If interrupted, rate-limited, or given no response, reread the changed files and last checkpoint before continuing. Preserve completed work and patch only the missing pieces.

## Output Budget Rules

- Keep `SKILL.md` as a router.
- Move detailed procedure to focused references.
- Generate one bounded artifact group at a time.
- Validate links and scripts before starting the next batch.
- Avoid repeating evidence narratives in operational guidance.

## Post-Interruption Repair

When the user says work may have been interrupted or partially split:

1. Reread every changed skill file and relevant resource file.
2. List expected versus actual files.
3. Run strict validation and script smoke tests.
4. Compare the intended split, merge, or extraction against actual content.
5. Patch verified gaps only.
6. Report what was validated and what remains unverified.

Do not restart from scratch unless the existing partial output is unusable.
