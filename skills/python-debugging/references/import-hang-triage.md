# Import Hang Triage

Use this reference when an import hangs, silently exits the process, prompts for input, or terminates the terminal without a clean Python exception.

## Direct Import Probe

Probe imports in the selected interpreter before running broad tests:

```powershell
python -X faulthandler -c "import faulthandler; faulthandler.dump_traceback_later(10, repeat=False); print('before package', flush=True); import package; print('after package', flush=True)"
```

The flushed `before` and `after` markers classify the failure:

- both markers print: import completed
- `before` prints but `after` does not: import hung, crashed, or blocked inside that import
- neither marker prints: command boundary, interpreter startup, or shell issue
- prompt text appears: classify as prompt or environment behavior, not a Python exception

## Bisection Pattern

When a module imports many dependencies, bisect the imports:

```python
print("before click", flush=True)
import click
print("after click", flush=True)

print("before boto3", flush=True)
import boto3
print("after boto3", flush=True)
```

Keep probes small and run them with the same interpreter, working directory, and environment as the failing path.

## Timeout And Stack Dumps

- Use `-X faulthandler` for interpreter-level stack visibility.
- Use `faulthandler.dump_traceback_later(seconds, repeat=False)` around suspect imports.
- Prefer a command timeout or small helper script over waiting indefinitely.
- If the process exits, capture exit code and stderr before assuming a hang.

## Platform And Dependency Checks

Before patching product code, check:

- selected Python interpreter and virtual environment
- package version and install location
- local files shadowing the package name
- optional dependency extras
- native extension or platform-specific wheels
- network, credential, or metadata-service calls triggered at import time

If the failure occurs only through pytest collection, use the pytest collection branch in [error-patterns.md](error-patterns.md) after direct import probes.

## Do Not

- Do not treat `terminal exited` as a Python root cause.
- Do not run broad project tests before direct import probes when the symptom is import-time behavior.
- Do not move imports inside functions unless you have identified a real cycle, optional dependency boundary, or expensive import side effect.
- Do not hide import hangs with broad exception handling; classify the dependency or platform blocker.