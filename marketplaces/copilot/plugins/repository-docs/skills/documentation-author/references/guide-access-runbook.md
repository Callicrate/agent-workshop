# Access Runbook Workflow

Use this guide for live access docs, reproduction docs, lab interface notes, workstation/server access notes, and continuously updated capability ledgers such as `*-access.md` files.

## Output Contract

- Lead with scope, target coordinates, execution plane, and last-updated evidence.
- Document each interface separately: URL, host, port, protocol, tool, shell, VPN, browser, API, or CLI.
- State credential or access state without exposing secrets. Use labels such as `available`, `not available`, `requires prompt`, or `read-only`.
- Include exact reproduction commands or click paths that prove access.
- Record observed behavior, banners, pages, response codes, prompts, or screenshots/artifact paths.
- Mark read/write capability for each interface.
- Track peer docs or sidecars, including files that are trusted read-only and must not be edited.
- Keep a `Do Not Repeat` or `Blocked Access` section for dead paths and failed credentials.

## Required Sections

```markdown
# <target> Access Runbook

## Scope
## Execution Plane
## Interfaces
## Credentials And Access State
## Reproduction Commands
## Observed Behavior
## Read/Write Capability
## Peer Docs And Sidecars
## Artifacts
## Do Not Repeat
## Next Checks
```

## Live-Doc Rule

Live runbooks are checkpoints, not terminal deliverables, unless the user asks only for docs. If the user says to write the access doc and then continue, update the doc and resume the main work.

When the user says to keep a runbook updated, treat it as a standing obligation: update new verified access, preserve old verified access, and read trusted sidecars when they change.

## Avoid

- raw secrets or tokens
- unsupported guesses about access level
- mixing host-local, WSL-local, and target-local paths without labels
- editing a peer doc marked do-not-edit
- replacing current access notes with a clean summary that loses reproduction details