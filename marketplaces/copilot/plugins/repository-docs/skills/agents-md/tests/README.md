# agents-md Test Fixtures

Fixtures are grouped by expected validator outcome:

- `minimal-python`, `contract-bearing`, and `operational` are valid examples for structural and semantic checks.
- `bad-*` fixtures intentionally violate one validator contract each, such as placeholders, stale paths, broken links, unsafe commands, or nested-scope conflicts.
- `dynamic-paths` captures accepted path annotations and glob-style references that should not be treated as stale.
- `nested-agents` demonstrates a root plus nested `AGENTS.md` topology used by scope-conflict checks.

The pytest suite creates temporary repos for narrow helper-script regressions. Keep fixture directories stable for the subprocess harness in `scripts/run_agentsmd_fixture_checks.py`.
