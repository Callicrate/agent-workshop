# Request Body Contracts

Use this reference before calling Databricks APIs that require JSON request bodies, especially statement execution, MLflow, jobs, model registry, and workspace APIs.

## Operation Gate

Classify the request with [operation-classes.md](operation-classes.md) before preparing or sending a request body. If the class is production activation, destructive, active execution control, or an execution trigger not already authorized by the task contract, stop at a prepared payload, manifest, dry-run output, or ready-to-run plan.

Before sending the request, verify:

- endpoint path and API version
- HTTP method
- required top-level fields
- required nested fields such as `content`, `statement`, `warehouse_id`, `name`, `version`, or `run_id`
- field types and empty-string behavior
- whether query parameters differ from JSON body fields

## Empty Content Guardrail

Do not send body fields that are required but empty.
If a generated request body has empty `content`, `statement`, or `messages`, stop and reconstruct the request from the source object before calling the API.

## Helper Pattern

For non-trivial bodies, generate a small Python helper instead of hand-quoting JSON in PowerShell.
The helper should:

- load a sensitive body from a private runtime `--body-file`, not generated source
- allow inline `--body` only for fixed JSON with numeric, boolean, and null leaves; any text-bearing body belongs in the private runtime file
- call the endpoint
- print only redacted structural diagnostics; never print body values, response rows, messages, SQL literals, temporary credentials, external links, raw CLI stdout, or raw CLI stderr
- preserve machine-readable error codes and statement IDs/states when they are safely available

Runtime body files and generated helpers must live in an access-controlled, untracked directory. Do not add them to source control, commit them, or use a path whose name itself carries a token or credential.

Generated helpers pass every POST body to the Databricks CLI as `--json @<private-file>`, never as JSON argv text. They reject every existing symlink or Windows reparse-point component before Git classification and private read/write access, validate runtime body files before materializing a bounded private JSON file (best-effort mode `0600` where supported), pass only that path to the CLI, and attempt cleanup afterward. A read, path-boundary, Git-boundary, output-limit, or cleanup failure is a nonzero structural diagnostic. These checks are point-in-time protection, not proof against filesystem races or ACL changes after the check.

## Operation Intent Label

Before sending a request body for a state-changing endpoint, write down one intent label: `inspect-only`, `local-validation`, `workspace-definition-update`, `execution-trigger`, `active-execution-control`, `production-activation`, or `destructive-operation`. If the label is anything beyond inspect/local validation, verify that the user request or current task contract authorizes that class before execution.

## State-Changing Request Proof Bundle

Use this bundle for delete, disable, tag, alias, promote, repair, run-now, cancel, create, update, traffic shift, schedule activation, endpoint reset, or production target calls.

Before the request:

- prove profile, host, and principal
- fetch current object state from the API, not from a UI screenshot or remembered metric summary
- record the exact object identifier, such as full model name plus version, run ID, job ID, table name, or endpoint name
- state the reason for the change
- retain the request body only in the private runtime file; record field names or a redacted structural manifest when evidence is required
- state the operation class and whether explicit approval or an existing task contract authorizes execution

During and after the request:

- execute once unless the endpoint explicitly documents idempotent retry behavior
- capture only the machine-readable error code, statement ID/state, and an explicitly safe response summary; do not save raw response payloads, external links, temporary credentials, or message text in shared evidence
- fetch post-state using the same profile and object identifier
- report any API error payload exactly enough to diagnose permissions, missing objects, bad methods, or invalid parameters
- note rollback or follow-up actions when the API supports them

For registry changes, fetch the model version by full name and version immediately before mutation and again after mutation. Do not mutate a model version based only on a Databricks UI URL, an old run summary, or search-result metadata.

## Failure Triage

When an API returns `BAD_REQUEST`, `INVALID_PARAMETER_VALUE`, `MALFORMED_REQUEST`, or a 404 from an unexpected method, inspect the method and private request body locally before changing API versions or object IDs. Do not copy raw error messages or request values into shared logs.

Databricks documents `error_code` as the stable machine-readable error field and `message` as informational text whose wording can change; generated helpers therefore retain only an allowlisted error code in diagnostics. See the [official API error contract](https://docs.databricks.com/api/workspace/errors).
