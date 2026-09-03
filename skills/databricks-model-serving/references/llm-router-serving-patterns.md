# LLM Router Serving Patterns

Use this when Databricks endpoints are backends for LiteLLM, LibreChat, OpenAI-compatible APIs, or profile-specific model gateways.

## Routing Contract

Do not expose every provider deployment as a separate user-facing model unless the user asks for that surface. Prefer stable logical model names such as:

- `fast-general`
- `deep-reasoning`
- `code-assistant`
- project-specific names such as `tap-default` or `email-default`

Each logical model should map to one or more provider-specific Databricks endpoints with an explicit routing policy: round-robin, weighted, fallback, fail-fast, or profile-isolated.

## Databricks Backend Checks

Before blaming the router, probe each Databricks backend directly:

- profile and workspace host
- endpoint name
- model task type: chat, completions, embeddings, or custom predict
- authentication source
- representative request and response shape
- latency and error status

Then validate the router path with the same logical model name the client will use.

## Profile Isolation

When a project uses multiple access profiles, such as TAP and EMAIL:

- keep profile names explicit in config
- keep credentials redacted from logs and reports
- avoid default Databricks profiles
- validate each profile with direct backend probes
- document which logical model groups each profile can use

For production callers, use a service principal with M2M OAuth and grant only `CAN_QUERY` on each endpoint. Route-optimized endpoints require their dedicated `endpoint_url` and OAuth-only authentication. Derive the route ID from the dedicated host and request exactly `{"type":"workspace_permission","object_type":"serving-endpoints","object_path":"/serving-endpoints/<route-id>","actions":["query_inference_endpoint"]}` as `authorization_details`. Do not send personal access tokens, workspace OAuth tokens without that detail, or a workspace invocation URL to a route-optimized endpoint. A control-plane Workspace Client query is not evidence that the router used this transport.

## LiteLLM And LibreChat Rules

- Group provider-specific deployments behind stable logical names.
- Keep fallback order and round-robin behavior explicit.
- Include health checks for every backend in the group.
- Keep request and response metadata sufficient to identify the chosen provider without exposing secrets.
- Do not silently mix predictive endpoint validation with LLM router validation. Router health includes logical name resolution, provider credentials, direct backend probes, and client-visible response shape.

## Lazy Imports

Router and endpoint-management CLIs must support `--help`, config rendering, and dry-run validation without importing heavy agent libraries, ML runtime code, or model clients.

Import heavy dependencies inside the command that needs them.
