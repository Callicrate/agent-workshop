# Agent Operations Documentation Workflow

Use this guide for role files, multi-agent operating folders, reviewer/directive loops, peer-status files, and AI worker manuals.

## System Audit

Treat multi-file operating docs as one coherent system. Read every role file, shared status file, README, directive file, and peer handoff file before patching cross-references.

## Output Contract

- Use imperative instructions, stable filenames, and unambiguous start state.
- Make the doc context-independent: a fresh agent should not need chat history.
- State the shared execution plane, shell, WSL distro, tmux/session naming, VPN, and filesystem assumptions.
- Do not tie the workflow to VS Code unless the user explicitly wants VS Code. Use editor-neutral wording when the agent may run elsewhere.
- Name status/directive files, who may edit them, who must only read them, and which files are trusted.
- Include start prompt assumptions, stop conditions, reviewer loop, escalation rules, and peer-file trust boundaries.
- Keep role docs concise and directive, not explanatory essays.

## Checklist

- [ ] Each role has a clear start state.
- [ ] Each role has a stop condition.
- [ ] Shared execution plane is explicit.
- [ ] Per-agent session names or workspace isolation are explicit when needed.
- [ ] Status files and directive files have read/write ownership.
- [ ] Peer docs marked do-not-edit remain untouched.
- [ ] Cross-references match the actual tree.
- [ ] Hidden IDE assumptions were removed.

## Evidence Amendments

When revising strategy or concept docs from an external evidence audit, integrate amendments into the natural existing sections. Do not append a detached evidence appendix unless the user asks for one.