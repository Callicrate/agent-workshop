# Databricks Operation Classes

Classify Databricks work before execution. This keeps fast inspection fast while preventing accidental live changes.

| Class | Examples | Rule |
|-------|----------|------|
| Inspect-only | list jobs, get run, read endpoint, fetch model version, query schema | Proceed after profile/workspace/resource proof. |
| Local validation | render config, validate bundle, static schema check | Proceed after cwd and target proof. |
| Workspace definition update | deploy bundle, update job config, update endpoint config | Require target/profile/principal proof and expected diff. |
| Execution trigger | `run-now`, bundle run, repair/rerun failed task, SQL execution with side effects | Require explicit user intent or an existing task contract. |
| Active execution control | cancel run, restart endpoint, stop pipeline | Require explicit user intent and run/resource identity proof. |
| Production activation | unpause schedule, move traffic, move model alias, enable trigger, switch prod target | Require explicit approval and production gate evidence. |
| Destructive operation | delete job, endpoint, model version, table, permissions, reset resource | Require explicit approval, pre-state, post-state, and rollback/follow-up notes. |

If operation class is unclear and the action is mutating, stop at a prepared payload, manifest, or ready-to-run plan instead of executing. Never treat a successful CLI return code as sufficient proof for production activation, destructive operations, or semantic output correctness.
