# Bulk List Mutation

Use this when a paginated Databricks list or search supplies the target set for multiple state-changing calls.

Apply [operation classes](operation-classes.md) and the [state-changing proof bundle](request-body-contracts.md#state-changing-request-proof-bundle) before any mutation.

## Workflow

1. Prove unfamiliar list and mutation syntax with `databricks <group> <command> --help` before starting an item loop.
   Confirm positional arguments, JSON-body support, continuation fields, and the named `--profile`.
2. Enumerate from page one until the continuation signal is absent, including across empty intermediate pages.
   Reject repeated continuation tokens, deduplicate by stable object ID, and record page number, page count, and cumulative unique total.
3. Freeze the target filter, cutoff, stable IDs, and relevant parent-child relationships in a manifest before mutation.
   Do not let later pages or concurrent arrivals silently change the original target set.
4. Never suppress per-item output or errors.
   Derive `attempted`, `succeeded`, and `failed` from actual return codes or response payloads; a loop iteration counter is not a success count.
   Any item failure makes the batch unsuccessful.
5. Re-enumerate the same query from page one to exhaustion and compare stable IDs with the frozen manifest.
   Report remaining original targets separately from items that appeared concurrently.
   Delete or mutate a parent only after the required child post-state is verified.
6. Claim success only when `failed == 0` and every original target has the intended post-state.

## Failure Checks

- an empty page still has a continuation token
- a continuation token repeats or cycles
- the CLI expects a positional identifier instead of `--json`
- per-item errors are redirected, caught, or discarded while a success counter still increments
- post-state verification checks only the first page
- concurrent new objects are mistaken for failures to mutate the frozen target set

Do not build a generic bulk executor unless supported resource families have stable request, pagination, and rollback contracts plus fixture coverage.
