# Repro And Compile Preflights

Use this reference when a Python failure depends on request shape, generated helper code, shell quoting, or a small reproducible path.

## Small Repro Scripts

Prefer a small script file when the reproduction needs:

- JSON body construction
- multiple imports
- loops or branching
- environment inspection
- quoting-sensitive strings
- repeated execution after edits

Keep the script focused on the failing path. Do not turn it into a broad test harness unless the user requested one.

## Compile Preflight

Before running generated Python helpers, run:

```powershell
python -m py_compile <path>
```

For helpers executed inside WSL or a remote Linux target, run the equivalent interpreter there:

```bash
python3 -m py_compile <path>
```

## Request-Shape Debugging

For API or SDK failures, print a redacted representation of the constructed request before the call.
Verify required fields are present and non-empty before changing endpoints, versions, or authentication.

## Shell Boundary Rule

If the repro crosses PowerShell to WSL, SSH, Bash, or another wrapper, write a helper script and execute it in the target shell instead of nesting multiline code in command strings.

Name the boundary before diagnosing the result:

- which shell parsed the command
- which shell owned prompts
- which interpreter ran Python
- which layer produced the exit code
- whether the output contains a prompt, missing distro, permission error, or terminal transport failure before Python started

Terminal lifecycle messages such as `terminal exited` are transport evidence, not diagnosis. Scan captured output for the first fatal line or prompt before changing Python code.

When the boundary itself is unclear, hand off to `local-project-execution` until the command contract is known. Return to `python-debugging` only when the selected Python interpreter produced the failure.