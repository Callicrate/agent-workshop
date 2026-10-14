---
description: "PowerShell script standards: parameter blocks, error handling, output patterns"
applyTo: '**/*.ps1'
---

# PowerShell Script Standards

## Parameter Blocks

Use `[CmdletBinding()]` and a typed `param()` block for scripts that accept arguments. Annotate every parameter with its type. Use `[Parameter(Mandatory)]` for required inputs and provide sensible defaults for optional ones.

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$Paths,

    [string]$OutputDir = (Join-Path $PSScriptRoot "output")
)
```

Scripts that accept no arguments still declare an empty `param()` to signal intent.

## Error Handling

Set `$ErrorActionPreference = 'Stop'` at the top of every script unless you have a specific reason to use `SilentlyContinue` (e.g., fire-and-forget health checks).

Wrap external calls (HTTP, file I/O, subprocess) in `try/catch`. In catch blocks, return structured output or `throw` - never silently swallow errors.

```powershell
$ErrorActionPreference = "Stop"

try {
    $resp = Invoke-RestMethod -Uri $uri -TimeoutSec 10
}
catch {
    @{ success = $false; error = $_.Exception.Message } |
        ConvertTo-Json -Compress | Write-Output
    exit 1
}
```

## Output

- Use `Write-Output` for data that other scripts or tools consume.
- Use `Write-Host` (with `-ForegroundColor` where helpful) for user-facing status messages.
- Scripts consumed by other tools must return JSON via `ConvertTo-Json -Compress`. Use `-Depth 10` when serializing nested objects.
- Suppress unwanted output with `$null =` or `| Out-Null`, not by ignoring return values.

## HTTP Calls

Use `Invoke-RestMethod` for JSON APIs (it deserializes automatically). Always set `-TimeoutSec` to avoid hanging on unreachable services. Set `-ContentType 'application/json'` on POST/PUT requests.

Handle connection failures gracefully - return a structured error or a clear message, not an unhandled exception.

```powershell
try {
    $null = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 2
}
catch {
    @{ continue = $false; stopReason = 'server is not running' } |
        ConvertTo-Json -Compress | Write-Output
    exit 0
}
```

## File Operations

- Use `Test-Path` before reading or depending on a file's existence.
- Use `Join-Path` for path construction, never string concatenation with `/` or `\`.
- Use `-Encoding utf8` (or `utf8NoBOM`) explicitly on `Get-Content` and `Set-Content` when the file will be consumed cross-platform.
- Use `Split-Path -Parent` to navigate relative to `$PSScriptRoot`, not hardcoded paths.

```powershell
$configFile = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path $configFile)) {
    throw "Config not found: $configFile"
}
$config = Get-Content -Path $configFile -Raw -Encoding utf8 | ConvertFrom-Json
```

## Embedded Commands And Hooks

When writing PowerShell commands embedded in JSON, YAML, VS Code hooks, or Copilot automation wrappers:

- Do not use literal `%USERPROFILE%` paths. Resolve Windows user paths with `$env:USERPROFILE` and `Join-Path`.
- Prefer `powershell -NoProfile -ExecutionPolicy Bypass -Command "& (Join-Path $env:USERPROFILE 'path\to\script.ps1')"` for hook commands.
- Validate the target script path with `Test-Path` before assuming the hook command is correct.
- Validate the containing JSON or YAML after editing. Most hook failures are quoting or escaping errors, not script logic.
- For Nopilot-style `code chat` orchestration, use resume mode (`-r`), set the working directory explicitly, and target the last-opened VS Code window unless the user gives a different window/profile rule.

## Style

- **Naming:** PascalCase for functions (`Get-FrontmatterValue`) and parameters (`$OutputDir`). Use approved verbs (`Get-`, `Set-`, `New-`, `Test-`, `Invoke-`, `Convert-`).
- **One task per script.** If a script does two unrelated things, split it.
- **Comments:** Explain *why*, not *what*. Use `<# .SYNOPSIS #>` comment-based help for scripts with parameters.
- **Pipeline style:** Prefer pipeline chains (`|`) over temporary variables when the chain is short and readable. Break long pipelines with backtick continuation aligned to the pipe.
- **No aliases in scripts.** Write `ForEach-Object`, not `%`. Write `Where-Object`, not `?`. Aliases are for interactive use only.
