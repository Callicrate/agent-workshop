# Databricks Local Command Surface

Use this reference when a Databricks task fails before domain execution because the local command surface is wrong.

## Own Only Local Mechanics

Let Databricks skills own workspace intent, profile semantics, target selection, deployment safety, job behavior, endpoint behavior, and production gates.

Use this skill for:

- current working directory and bundle root selection
- active shell syntax: PowerShell, Bash, zsh, WSL, SSH, or CI shell
- Python interpreter or package-manager selection
- Databricks CLI executable availability and import-time hangs
- command splitting for `validate`, `deploy`, `run`, `jobs get`, and helper scripts
- line endings in `.sh` scripts edited on Windows
- quoting for JSON, profiles, targets, and heredocs
- subprocess timeouts and first-fatal-line capture

## Preflight

Before diagnosing Databricks logic, prove the local command contract:

```text
cwd:
active host/shell:
interpreter or executable:
profile flag or env source:
target flag or bundle target:
expected success signal:
timeout:
cleanup:
```

If Bash reports `$'\r'`, classify the blocker as CRLF line endings and normalize the script to LF before changing Databricks code.
