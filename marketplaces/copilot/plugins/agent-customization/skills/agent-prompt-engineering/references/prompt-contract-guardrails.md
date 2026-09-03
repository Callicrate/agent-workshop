# Prompt Contract Guardrails

Use this reference before writing operational prompts, role boundaries, tool contracts, helper-agent specs, MCP contracts, or bulk prompt suites.

## Fact Grounding

- Inspect the current repo tree, tool registry, scripts, command names, and docs that the prompt will reference.
- Ownership tables, handoff graphs, tool lists, folder contracts, and examples may name only verified existing components.
- If a component is planned but not built, put it in a section explicitly labeled proposed future work and include the creation task.
- Prefer executable audits and current registries over stale docs when the two disagree.

Bad pattern: inventing a handoff graph from domain nouns such as `ctf-recon`, `ctf-web`, or `ctf-pwn` when those actors are not registered or implemented.

Good pattern: name the existing wrapper, MCP, script, or prompt file, then state which future component would replace it if the user approves building one.

## Agent-Facing Surface Minimization

- Expose one normal path for each routine capability.
- Hide infrastructure mechanics the agent should not reason about, such as transport folders, tmux internals, queue plumbing, or raw shell escape hatches.
- Label admin paths and break-glass paths separately, with a concrete trigger for each.
- Remove overlapping tools from the prompt even when they remain available internally.
- Translate operator frustration about tool complexity into a stronger abstraction boundary.

Pattern: keep reusable orchestration generic, then adapt domain behavior at the edge. For example, a generic agent-ideas workflow should own parallel ideation and consolidation, while a CTF wrapper supplies CTF-specific context and stop conditions.

## Runtime Call Path

Any helper agent, MCP, queue, worker pool, or workflow contract must specify:

- caller
- trigger
- required inputs
- optional inputs
- durable state location
- sync versus async behavior
- polling or wait behavior
- result retrieval
- failure modes
- retry policy
- caller next action after success or failure

Review the actual invocation path when workers, queues, or async compilation are involved. Trace from caller to wrapper to helper or server and back to the result artifact.

## Model-Visible Output Contract

When runtime code validates or rejects model output, every agent-controllable acceptance constraint must be visible in the prompt or supplied output schema. No agent-controllable rule may remain validator-only.

Before testing, create a constraint ledger that maps each in-scope, agent-controllable validator rule to:

- the visible prompt instruction or output-schema field that communicates it
- a positive regression fixture that the validator accepts
- a negative regression fixture that the validator rejects

Keep exact field names and mappings in the owning project. Structural and runtime audits are useful leads, not proof of semantic parity. Verify the ledger against the actual validator and every boundary that can accept or reject the model output.

## Architecture Invariant Ledger

When a prompt defines directory layout, transport boundaries, naming conventions, or generated file locations, create a short invariant ledger before editing examples.

Include:

- parent docs live at this path
- implementation lives at this path
- transport folders or runtime boundary rules
- generated files and naming convention
- examples that must satisfy the invariant
- audit command or manual check used to verify it

If the user changes the structure mid-session, update the ledger first, then move files and rewrite docs.

## Bulk Prompt Generation

- Do not rely on one large response for many prompts, agents, or specs.
- Generate files incrementally in bounded batches.
- Keep a checklist of expected outputs, completed files, and validation results.
- Validate naming, structure, links, and references after each batch.
- After rate limits, no-response failures, or context compaction, resume from the checklist rather than restarting from memory.

### Transport And Run Artifacts

- Keep generated prompt bodies out of process arguments and launcher script text.
  On Windows, do not embed a large prompt body in PowerShell `-EncodedCommand`; UTF-16LE and Base64 expansion can exceed the process command-line limit.
  A short encoded wrapper that reads a prompt file is acceptable.
- For Codex CLI, write each prompt as UTF-8 and send it on stdin with the explicit prompt `-`.
  Keep argv limited to flags and short paths, and use stdin redirection on POSIX shells.
  In PowerShell, set native-process stdin to UTF-8 for the pipeline and restore the caller's encoding afterward:

  ```powershell
  $priorOutputEncoding = $OutputEncoding
  try {
      $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
      Get-Content -LiteralPath $promptFile -Raw -Encoding utf8 |
          & codex exec @codexArgs -
  }
  finally {
      $OutputEncoding = $priorOutputEncoding
  }
  ```
- For suites, keep each prompt, stdout, stderr, and `--output-last-message` report in one per-run directory.
  Record worker ID, cwd, artifact paths, process ID, status, and exit code in the manifest, then resume from that manifest.
- Treat prompt, log, report, and manifest files as persisted data.
  Apply [Secret Handling](tool-security-patterns.md#secret-handling) before writing them and define retention or cleanup.

## Empty Input And Optional Artifacts

- For extraction, summarization, retrospective, or review prompts, define empty-input behavior explicitly.
- State whether no evidence means no output, an empty report, or a diagnostic artifact.
- Do not require helper artifacts unless the same workflow creates them.
- List helper outputs as optional when another workflow may create them, and define fallback inputs when they are absent.
- Avoid redundant required inputs when one identifier can derive another; state the derivation rule and optional override.

## Interruption Recovery

After an interrupted prompt, role, or skill edit:

1. Re-read the current target files.
2. Compare intended deliverables to files on disk.
3. Check references, mirrors, links, and examples.
4. Run available validators or structure audits.
5. Report only concrete remaining gaps.

Do not assume previous tool calls completed just because earlier assistant text described them.
