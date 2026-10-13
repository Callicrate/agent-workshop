# Command Shape Guardrails

Use this reference before running commands that cross shells, embed multiline code, sync files, or have destructive effects.

## Shell Selection

Choose commands for the active host and active shell, not for the user's other machine or usual preference.

- On Windows PowerShell, use PowerShell syntax and avoid Bash heredocs.
- On macOS/Linux Bash or zsh, use POSIX/Bash-compatible syntax and avoid PowerShell backticks or cmdlets.
- In WSL, treat paths, environment variables, Python interpreters, and background processes as belonging to the selected distro.
- For repository scripts, prefer the documented entrypoint even when it differs from the current shell preference.
- Use helper scripts or temporary files when quoting would cross more than one shell boundary.

## Direct Simple Commands

When the user asks for a simple known command and the working directory is clear, run it directly.
Examples include `git status`, `git diff`, `date`, repo-defined test scripts, and validator commands already documented by the project.

Report the output or the relevant lines in the final answer because the user cannot see tool output.

## Multiline Code

Avoid complex `python -c` strings and nested here-documents when the snippet contains JSON, loops, multiline logic, or shell-sensitive quoting.

Preferred shapes:

- checked-in helper script for reusable project logic
- temporary script file for one-off local probes
- PowerShell here-string piped to Python only when the code is short and stays in one shell boundary

For generated Python helpers, run `python -m py_compile <path>` before running behavior-sensitive code.

## Line Endings And Script Portability

Before running a `.sh` file from a Windows-edited checkout, check for CRLF line endings or run `bash -n <script>`.

- If Bash reports `$'\r'`, classify the issue as script portability / line endings, not application logic.
- Normalize shell scripts to LF and PowerShell / batch scripts according to repository `.gitattributes`.
- Do not rewrite application logic to fix a shell script that is only failing because of CRLF.

## Nested Shell Boundaries

Name which shell interprets each layer before running a command that involves PowerShell to WSL, PowerShell to SSH, Bash to Python, or wrappers around terminal tools.

If more than one layer needs quoting rules, write a helper file and execute that file from the innermost environment.

When a command fails with a terminal-exit notification or wrapper output, classify the boundary before diagnosing application code:

- outer shell and working directory
- inner shell or remote host, if any
- selected interpreter or tool executable
- prompt owner, such as `sudo`, SSO, package manager, or credential prompt
- timeout owner
- first fatal stderr or stdout line before the terminal lifecycle message
- exit code owner

Examples such as missing WSL distributions, `sudo` prompts, SSH prompts, or package-manager questions are environment/command-boundary blockers until the target interpreter actually starts and fails.

## Do Not Hide Hangs In Command Chains

Run one uncertain command per terminal call when any step may hang, prompt, or import large dependency graphs.

Prefer separate timed checks for:

- linter or formatter invocation
- direct imports or module-load probes
- CLI `--help` or `--version`
- dry-run or config validation
- real runtime command

For each uncertain command, record:

- command and cwd
- timeout
- expected marker
- exit code
- whether stdout/stderr reached the marker before hang or failure

Do not conclude that a whole validation chain failed when earlier checks passed and only a later help/runtime command hung.

## Long-Running Jobs And Waits

Use fixed sleeps only when the user explicitly asks for a timed wait or when the command itself requires a fixed delay. Do not use `Start-Sleep`, `sleep`, or equivalent as a polling strategy when task notifications, async terminal state, process checks, or log tailing can provide evidence.

For genuine waits or long-running helpers, prefer an agent-managed async/background terminal job with a clear stop condition. Record:

- command
- working directory
- job id or terminal id
- log path, if any
- URL, port, process id, or expected marker
- stop condition
- cleanup command

For dev servers, tunnels, watchers, and browser harnesses, keep the process running only when the user needs it or the validation requires it. Before final response, state whether it remains running and how it should be stopped.

## Manual Reproduction Deliverable

When the user asks to run the current state manually, provide a minimal copy-ready command or script that reaches the observed failure.

Include:

- working directory
- prerequisites, env files, services, or VPN assumptions
- exact command or script path
- expected intermediate success markers
- known stop point, hang marker, or failure line
- cleanup step when the script starts background work

Avoid a broad "try running the tests" answer when the user asked for the exact path to the current hung or failing spot.

## Exit Code And Domain Success Signals

For probes wrapped in `timeout`, `head`, `grep`, pipelines, or interactive banners, classify by expected domain output first and exit code second. Report both when they disagree.

Examples:

- a TCP probe that prints a welcome banner before `timeout` exits nonzero may still prove connectivity
- `grep` returning 1 means no match, which may be expected for a negative probe
- a pipeline may return the wrapper's exit code rather than the domain command's result

## Terminal Cleanup

Killing or replacing a noisy terminal is acceptable when terminal state is poisoned, output is unbounded, or a terminal id can no longer receive commands. Before cleanup, identify whether a long-running process will be intentionally stopped. After cleanup, restart any process that remains part of the user's requested workflow.

## File Sync And Destructive Operations

Before `robocopy /MIR`, recursive deletion, move, cleanup, or sync operations:

- identify source and destination
- decide whether destination-only files should survive
- prefer non-destructive copy modes unless deletion is explicitly requested
- dry-run or list affected files when feasible

Do not use destructive sync as a generic way to make folders match.

## Completion Evidence

For every local command loop, preserve:

- command
- working directory
- expected success signal
- exit code
- relevant output or artifact path
- whether a broader validation remains unrun
