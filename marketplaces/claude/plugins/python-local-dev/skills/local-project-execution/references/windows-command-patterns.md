# Windows Command Patterns

Use these rules when the active shell is PowerShell.

## Path And Quoting

- Use `-LiteralPath` for filesystem paths that may contain brackets, wildcards, or special characters.
- Prefer PowerShell cmdlets end to end for file operations.
- Do not build a file list in PowerShell and pass it to `cmd /c` for deletion or moving.
- Use backticks only for line continuation in commands you are writing for PowerShell.

## Ripgrep

- Use `rg -g` for recursive file filters. Do not pass a file wildcard as a positional search path.
- Quote regular expressions with single quotes so PowerShell preserves literal regex text, especially `$` and backticks.
- Use repeated `-e` options for patterns that begin with `-` or when separate patterns are clearer.
- Put `--` before real search paths to terminate `rg` options.
- Do not append PowerShell-only parameters such as `-ErrorAction` to `rg`; they are not `rg` options.

```powershell
# WRONG - PowerShell passes `*.sql` to `rg` as a literal positional path argument.
rg 'CREATE\s+TABLE' *.sql

# CORRECT - recursively filter SQL files, keep the pattern literal, and delimit the path.
rg -g '*.sql' -e 'CREATE\s+TABLE' -- root
```

## Python

- Prefer the repo interpreter or virtual environment when one is present.
- Use a temporary script file or checked-in helper for complex Python logic. Avoid `python -c` for JSON, multiline logic, or shell-sensitive quoting.
- For heredoc-style stdin scripts in PowerShell, use:

```powershell
@'
print("hello")
'@ | python -
```

## PowerShell To WSL

Do not choose WSL merely because a Bash-shaped command is convenient. Use WSL when the repo, toolchain, or target runtime requires Linux semantics, and record the Windows path to WSL path translation in the command contract.

- Avoid nesting multiline Python or Bash heredocs inside `wsl.exe`, `bash -lc`, or SSH commands launched from PowerShell.
- For helpers longer than a one-liner, write a helper script or temp file from PowerShell, run it from WSL, and keep quoting inside one shell boundary. Before running `.sh` from a Windows-edited checkout, check line endings or run `bash -n <script>`.
- Syntax-check generated Python helpers with `python3 -m py_compile <path>` before running them against a target.

### PowerShell To WSL Runtime Checklist

Before running a WSL command from PowerShell:

1. Run `wsl -l -v` and choose an existing distro by its exact displayed name.
2. Record the chosen distro in the command contract. Do not assume names such as `kali-linux`, `Ubuntu`, or `Debian` from memory.
3. If a privileged command is planned, run `wsl -d <Distro> --exec sudo -n true` first.
4. If noninteractive sudo fails, classify privileged commands as unavailable and choose an unprivileged alternative when useful. State what capability is lost.
5. Confirm the WSL-side cwd, interpreter, and tool path before diagnosing application code.
6. For tunnels, watchers, dev servers, and other long-lived processes, use a persistent WSL shell, tmux, systemd user service, or agent-managed async terminal. Do not rely on backgrounding a process inside a one-shot `wsl --exec bash -c` invocation.

If `wsl -d <name>` reports `There is no distribution with the supplied name`, stop and rediscover distros. The wrong distro name is an environment/targeting failure, not a product bug.

### Detaching Long WSL Jobs

A job that outlives the agent's command timeout (roughly 30s) must be fully detached from the launching `wsl --exec` call, or it dies when that one-shot call returns.

- Bare `nohup <cmd> &` is not reliable across the WSL one-shot boundary; the process stays tied to the session and can be reaped.
- Detach with `setsid` plus `disown`, redirect all streams to a log, and close stdin so nothing blocks:

```bash
setsid bash -c '"$PY" scripts/long_job.py > run.log 2>&1' < /dev/null & disown
```

- Poll with short recovery commands that finish well under the timeout; never issue a blocking wait:

```bash
tail -n 2 run.log
pgrep -af long_job.py || echo DONE
```

## Bash Scripts From Windows Checkouts

Before invoking a `.sh` script from PowerShell, WSL, Git Bash, or a remote Linux shell, confirm the repository did not store the script with CRLF endings. A Bash error mentioning `$'\r'` is a portability failure. Convert the script to LF or fix `.gitattributes` before diagnosing the program logic.

## Single-Command Environment Overrides

To neutralize a blocking environment variable for one command only (a dead `HTTP_PROXY`/`HTTPS_PROXY`, a stale token, a wrong `PATH` entry), override it in the child scope. Never make a persistent change such as `setx` or a profile edit to work around a transient block.

```powershell
# WRONG - persists for the whole machine/user
setx HTTP_PROXY ''

# CORRECT (PowerShell) - isolate in a child process so the session env is untouched
pwsh -NoProfile -Command "$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; <command>"
```

```bash
# CORRECT (Bash) - assignment prefix scopes the vars to this one command
HTTP_PROXY= HTTPS_PROXY= <command>
```

A bare `$env:HTTP_PROXY=''` in PowerShell persists for the rest of the session; use the child process above when the parent session must stay unchanged.

## Node

- Use the package manager indicated by lockfiles or the supported `packageManager` field, then use `package.json` scripts.
- Run scripts from the package directory that owns `package.json`.
- Prefer `npm run <script>`, `pnpm <script>`, or `yarn <script>` over invoking local binaries directly unless the repo already does that.

## Long-Running Commands

- For dev servers, capture the URL and leave the process running only when the user needs to try the app.
- If a port is taken, inspect the existing process or choose a nearby port according to repo convention.
- In PowerShell, only use `Start-Sleep -Seconds <n>` for literal requested waits or fixed protocol delays.
- Do not use `Start-Sleep` as a polling strategy. Prefer terminal/job notifications, process checks, or log tailing.
- For background helpers launched from PowerShell, define the runnable entrypoint, working directory, environment, log file, and stop condition before starting the process.

## PowerShell Script Files

When answering how to run a `.ps1` manually, include:

- working directory
- `pwsh` versus Windows PowerShell choice
- `-File` invocation with `-LiteralPath` when paths may contain special characters
- required parameters
- env files, services, or profiles the script expects

Example shape:

```powershell
Set-Location -LiteralPath 'C:\path\to\repo'
pwsh -NoProfile -ExecutionPolicy Bypass -File '.\scripts\run_tests.ps1' -Target 'smoke'
```
