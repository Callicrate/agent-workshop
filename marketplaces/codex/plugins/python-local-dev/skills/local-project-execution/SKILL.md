---
name: local-project-execution
description: "Use when troubleshooting local setup/cwd/shell/runtime/test/build/cross-shell/long processes. Do not trigger for documented commands/Databricks intent/isolated Python tracebacks/docs-only review."
metadata:
  short-description: Choose or troubleshoot local project commands.
---

# Local Project Execution

## When to Use

- The correct repo-local command, working directory, shell, interpreter, package manager, or task entrypoint is unclear
- A command failed because the environment, path, dependency manager, or invocation style was guessed
- Execution crosses PowerShell, WSL, SSH, Bash, Python, Node, or Databricks CLI boundaries
- A CLI, `--help` command, dev server, watcher, tunnel, or other long-running process hangs or prompts unexpectedly
- The user needs an exact local reproduction command, including prerequisites, success markers, and the known failure point

## When NOT to Use

- Owning Databricks domain intent, workspace semantics, deploy/run safety, profiles, targets, or resource changes; use the Databricks skills for those and this skill only for local command mechanics
- A Python traceback after the correct command and environment are already known
- A repo-documented command has a clear working directory and needs no troubleshooting
- Pure code review or documentation work with no local command execution

## Workflow

1. For a simple repo-documented command with a clear working directory, execute it directly.
2. When the command surface is unclear, read [references/execution-preflight.md](references/execution-preflight.md), inspect repository guidance and task metadata, and run [scripts/inspect_project.py](scripts/inspect_project.py) for mixed or unfamiliar projects.
3. Establish the execution contract: working directory, shell, interpreter or package manager, environment assumptions, command, expected success signal, and timeout.
4. Use [references/command-shape-guardrails.md](references/command-shape-guardrails.md) when a command crosses shell, quoting, line-ending, file-sync, or destructive-operation boundaries.
5. For Databricks local-command failures, read [references/databricks-local-command-surface.md](references/databricks-local-command-surface.md); let the relevant Databricks skill own workspace, profile, target, and live-operation semantics.
6. Before running a `.sh` file from a Windows-edited checkout, check CRLF risk or run `bash -n <script>`; classify `$'\r'` failures as script portability, not application logic.
7. For terminal lifecycle notices or nested-shell output, identify the shell, prompt, timeout, and exit-code owner, then find the first fatal line before diagnosing product code.
8. For uncertain CLIs, split lint, import, `--help`, dry-run, and runtime checks into separate timed commands so a later hang cannot obscure earlier results.
9. If a Python or JavaScript command hangs, use [references/python-and-js-commands.md](references/python-and-js-commands.md) for direct import or module probes before handing off to language-level debugging.
10. For WSL commands, use [references/windows-command-patterns.md](references/windows-command-patterns.md) to select the distro, probe noninteractive privileges, and choose a persistent process model.
11. Start with the smallest reliable validation that exercises the change. Expand only when risk or the user request requires it.
12. Classify failures as setup, dependency, target, test, product, prompt, privilege, timeout, hang, or transport failures before editing code. For launcher transport failures, follow the bounded recovery in [references/execution-preflight.md](references/execution-preflight.md); never infer product state from a command that did not start.
13. Treat execution as complete only when the command reaches terminal success or a concrete blocker. For manual reproduction, provide the exact prerequisites, command, markers, stop point, and cleanup.

## Deterministic Tools

| Tool | Use When | Outcome |
|------|----------|---------|
| [scripts/inspect_project.py](scripts/inspect_project.py) | You need a quick inventory of project roots, package managers, Python config, and JS scripts | JSON command-surface summary |
| [references/execution-preflight.md](references/execution-preflight.md) | You need to choose a command safely | Working-directory and command contract |
| [references/command-shape-guardrails.md](references/command-shape-guardrails.md) | A command has nested shells, multiline code, file sync, or destructive semantics | Safer command shape and validation pattern |
| [references/windows-command-patterns.md](references/windows-command-patterns.md) | You are running commands from PowerShell on Windows | Shell-safe invocation patterns |
| [references/python-and-js-commands.md](references/python-and-js-commands.md) | You need Python or JavaScript command selection rules | Test/build/lint command guidance |
| [references/databricks-local-command-surface.md](references/databricks-local-command-surface.md) | A Databricks CLI or bundle command fails because of cwd, shell, profile flag shape, line endings, quoting, timeout, or executable selection | Local command-surface classification before Databricks handoff |
| [tests/test_inspect_project.py](tests/test_inspect_project.py) | You change project-inspection behavior | Run `python -B -m unittest discover -s skills/local-project-execution/tests` for behavioral regression coverage |

## References

The deterministic tools table is the complete reference index. Load only the resources routed by the workflow.
