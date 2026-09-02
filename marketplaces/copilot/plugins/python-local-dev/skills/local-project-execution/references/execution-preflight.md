# Execution Preflight

Use this before running repo-local commands.

## Command Contract

Name these before execution:

- Working directory
- Shell and quoting model
- Line-ending policy for repository scripts when `.sh`, `.ps1`, `.bat`, or `.cmd` files are involved
- Interpreter or package manager
- Project or package target
- Required environment variables, profiles, or local services
- Expected success signal
- Timeout or stopping condition
- First fatal line to inspect if the terminal later reports a lifecycle failure
- Cleanup command or job id for long-running helpers

## Discovery Order

1. Read nearby `AGENTS.md`, README, task docs, and `.vscode/tasks.json` when present.
2. Inspect root markers: `.git`, `pyproject.toml`, `package.json`, lockfiles, `Makefile`, `justfile`, `tox.ini`, `pytest.ini`, and CI workflow files.
3. Prefer the package manager implied by lockfiles over global tools.
4. Prefer repo-defined scripts over ad hoc commands.
5. Keep commands scoped to the active subproject unless the user asked for all projects.

## Compact Local Preflight

For unfamiliar projects or command surfaces, gather a compact inventory before running build/test commands:

- command availability with exact executable path and version, for example `Get-Command uv,docker,databricks -ErrorAction SilentlyContinue | Select-Object Name,Source,Version`
- important environment variables as booleans only, never secret values
- config-file presence, such as `.env`, `databricks.yml`, `pyproject.toml`, `package.json`, or `.vscode/tasks.json`
- selected interpreter or package manager and why it was selected
- repo root and subproject root
- for `.sh` scripts from Windows-edited checkouts, whether line endings are LF or CRLF

Keep this preflight short. It should classify the command environment, not become a broad system inventory.

## Git Claim Preflight

Before reporting Git-backed status, diff, branch, tracked-file, remote, or history claims, run:

```powershell
git rev-parse --show-toplevel
```

On success, use the resolved repository root for the Git-backed command or claim.
On failure, classify the location as a non-repository boundary, continue with filesystem and documentation evidence, and do not diagnose product code.
A `.git` marker or an `inspect_project.py` inventory is not proof that Git commands work in the active location.

Do not add this check before every direct Git command when the current repository root is already known.

## Git Commit Outcome And Hook Diagnostics

When `git commit` prints hook or helper errors, keep four signals separate: the
hook diagnostic, the Git command exit code captured immediately, the resulting
commit or ref state, and any hook or configuration repair.

A pre-commit hook may print an error yet allow the commit when it returns zero.
Do not infer commit failure from stderr or rerun the commit until the result is
known. Verify `HEAD` with `git rev-parse --verify HEAD` and
`git show --no-patch --format='%H %s' HEAD`; when needed, compare `HEAD` before
and after the command.

Resolve the active hook directory with `git rev-parse --git-path hooks` and
inspect referenced helper targets during diagnosis. Treat `.git/hooks` and
`core.hooksPath` repairs as repository or machine configuration changes; do not
modify them unless that scope is authorized. Do not bypass hooks with
`--no-verify` as a diagnostic shortcut.

## Service And Daemon Readiness

A present CLI binary does not prove its backing daemon or service is running. When a command depends on a background service, probe readiness first and fail fast with the exact error instead of re-running the real command in a loop.

- Docker: run `docker version` or `docker ps` before `docker compose ...`. A `Cannot connect to the Docker daemon` or `npipe ... system cannot find the file specified` error means the daemon is down, not that the compose file is wrong. Start the daemon, then proceed.
- Managed service or systemd unit: run `systemctl status <unit>` or `journalctl -u <unit> -n 20` and read the first fatal line before re-issuing `systemctl start <unit>`. Fix the reported cause once; do not restart the same unit in a loop expecting a different result.

## CLI Capability Checks

For Databricks CLI and Databricks Asset Bundle commands, let Databricks skills own workspace/profile/target/live-operation semantics. Use local preflight for cwd, shell, executable discovery, line endings, quoting, timeout, and interpreter behavior.

- Run simple, familiar commands directly when the working directory and package manager are clear.
- Look up or probe command behavior when the CLI is unfamiliar, the flags are complex, the command is likely to fail, or a wrapper script will encode assumptions for repeated use.
- For complex CLIs, inspect the actual command surface with `--help`, `--version`, existing docs, or a harmless dry-run command before adding orchestration.
- To resolve a config key, flag name, or default value, query the installed CLI itself first, such as `<cli> --help`, `<cli> <subcommand> --help`, or its `config` or config-dump subcommand, before reaching for external vendor docs. Local help matches the installed version; web docs may describe a different release.
- For CLIs that may import cloud SDKs, plugins, or generated config on startup, run `--help`, import probes, and runtime commands as separate timed checks. Do not chain them behind one terminal invocation.
- When a command needs specific flags, paths, environment variables, or ordering to work reliably, record the exact pattern in the relevant skill or reference doc instead of rediscovering it each session.
- Preserve the user's requested model, profile, target, or flag unless the CLI proves it is unavailable. Do not silently substitute defaults because a wrapper example used them.
- For agent runners or background workers, identify the CLI path and make the entrypoint directly runnable outside the wrapper first, then add orchestration.
- Record unsupported flags, renamed options, and required environment variables as environment or targeting failures, not product bugs.

## Command Safety Validators

- Validate commands as argv or with a quote-aware parser when enforcing deny/allow rules. Do not scan raw command text in a way that blocks safe quoted strings or misses shell operators.
- Include positive and negative examples for any new guardrail: a command that must run, a command that must be blocked, and a quoted argument that should remain literal.
- Keep shell boundaries explicit. If PowerShell launches Bash, WSL, SSH, or Python, name which shell interprets each layer before adding quoting-heavy validation logic.

## Failure Classification

- Environment: missing dependency, wrong interpreter, wrong package manager, missing local service
- Targeting: wrong working directory, wrong package, wrong test selector
- Product failure: real test, build, lint, or runtime defect
- Platform blocker: permissions, unavailable service, locked file, network failure
- Privilege blocker: `sudo`, UAC, SSH, package manager, SSO, or credential prompt prevents noninteractive execution
- Transport failure: VS Code terminal wrapper, dead terminal id, shell executable launch failure, or terminal lifecycle notice before the command meaningfully ran
- Hang: expected marker appears but the next marker never appears before timeout

### Windows Launcher Recovery

On Windows, `CreateProcessAsUserW ... failed: 5` before command output is a runner transport failure, not evidence about the command or product.
Retry at most once with another launch shape that the current runner explicitly supports.
Keep the active target unchanged, and if the shell changes, translate the command so its semantics and working directory are preserved.
Confirm from the execution record that the selected executable actually changed.
Do not use deprecated, unavailable, or policy-forbidden escalation as a launcher workaround.
If the retry still selects the inaccessible executable, or no safe alternate exists, preserve task state and report the transport blocker; do not loop or diagnose product code.

### First-Fatal-Line Rule

Terminal lifecycle notices such as `terminal exited` are wrappers. After any terminal notification, scan captured output from top to bottom and identify the earliest fatal line owned by a tool, shell, prompt, interpreter, or domain protocol. Classify that first fatal line before using the terminal notification or exit code as the diagnosis.

If output contains both a domain success marker and a wrapper failure, report both and classify by the domain marker first. For example, `Connection ... succeeded!` or a service banner may mean the probe succeeded even when a surrounding `timeout`, `head`, `grep`, or pipe returns nonzero.

Only edit code after the failure is classified as product failure or the environment fix is clearly part of the task.

## Completion Rule

For run/build/test tasks, a patch is not enough. Continue until the validation command succeeds, a broader command succeeds when required, or a concrete blocker is documented with the exact command and failure.
