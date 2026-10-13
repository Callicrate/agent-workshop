# CTF Handoff Documentation

Use this guide when writing or cleaning `status.md`, prompt handoffs, lab status notes, or multi-agent CTF coordination docs.

## Output Contract

- Lead with current scope, active target coordinates, execution plane, credentials or access state, solved answers or flags, and stop condition.
- Separate verified facts from hypotheses and failed paths.
- Include a `Do Not Repeat` or `Dead Paths` section for stale routes, blocked branches, and probes that should not be repeated.
- List artifact paths by location: host workspace, WSL or Kali, and target-local remote path. Mark target-local paths as remote-only until copied.
- End with next actions that a fresh agent can execute without reading chat history.
- When multiple agents are active, update only verified fresh findings, useful failures, artifact locations, and next actions; do not rewrite another agent's ownership notes.

## Avoid

- Browser or tool internals unless they are needed to reproduce access.
- Long terminal transcripts instead of summarized evidence and artifact links.
- Mixing target-local paths with host-local artifact paths.
- Deleting negative evidence just because the route failed.