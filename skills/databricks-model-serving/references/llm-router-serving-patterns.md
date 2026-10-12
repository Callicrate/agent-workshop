# LLM Router Serving Patterns

Use this when Databricks endpoints are backends for LiteLLM, LibreChat, OpenAI-compatible APIs, or profile-specific model gateways. It owns LLM invocation and router validation. It does not expand the row/DataFrame v1 endpoint contract or invoke its doctor.

## Classify Before Selecting A Contract

Classify from the invocation task and protocol, source kind, and topology. Do not infer the contract from Unity Catalog registration or an endpoint name.

| Invocation | Source kind | Topology | Validation branch |
|---|---|---|---|
| `predict` with DataFrame records or split columns | Custom model or row-serving endpoint | Direct endpoint | Use the closed v1 row contract and `check_endpoint.py`. |
| Chat, embeddings, audio, completions, Open Responses, or provider-native API | Custom LLM, Foundation Model API, external model, or provider-native service | Direct endpoint or model service | Use this reference and validate the protocol's own request and response shape. |
| Any LLM protocol | One or more LLM backends | LiteLLM, LibreChat, or another router | Validate each selected backend directly, then the router with the same logical model. |

Apply DAB ownership, [operation classes](operation-classes.md), and [production target safety](prod-target-safety.md) before any mutation. This reference prepares or validates invocation only. It does not authorize endpoint creation, traffic movement, profile changes, credential access, or a live query.

## Routing Contract

Do not expose every provider deployment as a separate user-facing model unless the user asks for that surface. Prefer stable logical model names such as:

- `fast-general`
- `deep-reasoning`
- `code-assistant`
- project-specific names such as `tap-default` or `email-default`

Each logical model should map to one or more provider-specific Databricks endpoints with an explicit routing policy: round-robin, weighted, fallback, fail-fast, or profile-isolated.

## Direct LLM Backend Contract

Before any authorized direct probe, record the exact:

- source kind and supported task/protocol
- logical model name and endpoint or model-service target
- explicit profile and workspace host
- URL class: workspace endpoint, model-service gateway, Open Responses path, or provider-native URL
- authentication source and required permission, with credential values redacted

Use one bounded representative **non-streaming** request that matches that protocol, then check both structure and meaning. For chat, validate the expected completion object and a usable assistant result. For embeddings, validate a nonempty embedding result with the expected target identity. For Responses or Open Responses, send `input`, not `messages`, and require the expected response object with an `output` array. For provider-native protocols, use that provider's documented request and response contract. Do not convert a protocol mismatch into a row/DataFrame fixture, a generic success, or a fallback answer.

Record the direct backend result, selected target, logical model, protocol, structural check, semantic check, and a value-free failure class. A router cannot be ready from router-only evidence: prove every backend the routing policy can choose, then prove the router with the same client-visible logical model.

A non-streaming check proves only the non-streaming baseline. When streaming or SSE is part of the client contract, require a separate bounded stream proof with explicit byte, event, and elapsed-time limits; protocol terminal-event validation; reconstructed-content semantic validation; and cancellation plus cleanup behavior. Until that proof passes, streaming remains unverified.

## Source Capability And Route Optimization

Confirm source and capability from current official documentation before choosing URL, authentication, or route-optimization evidence. The following source pages were checked on 2026-10-12; they define protocol availability, not whether a particular model is available in a workspace:

- [Custom LLM serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/serve-custom-llms) supports `llm/v1/chat`, embeddings, and audio tasks, but not `llm/v1/completions`; it also lists route optimization as unsupported for custom LLM serving.
- [Route optimization](https://docs.databricks.com/aws/en/machine-learning/model-serving/route-optimization) applies only to custom model-serving and feature-serving endpoints. It excludes Foundation Model APIs and external models.
- [Foundation Model APIs](https://docs.databricks.com/aws/en/machine-learning/model-serving/foundation-models) and [external models](https://docs.databricks.com/aws/en/machine-learning/model-serving/external-models) define their own source availability and query setup.
- [Chat model queries](https://docs.databricks.com/aws/en/machine-learning/model-serving/query-chat-models) distinguishes Foundation Model APIs and external models, model services and endpoint names, and chat `messages` from Responses `input`.
- [Open Responses queries](https://docs.databricks.com/aws/en/machine-learning/model-serving/query-open-responses-models) uses `/serving-endpoints/open-responses`, a `model` field, and `input`, and returns an `output` array.

Do not carry the row/custom-model dedicated URL, OAuth route proof, or endpoint-scoped authorization details into custom LLM, Foundation Model API, external-model, or provider-native work unless the classified source's current documentation explicitly supports route optimization. An explicitly unsupported combination is a configuration and readiness failure. An unknown capability is unverified. Neither condition authorizes a silent downgrade to a standard workspace URL or another transport probe. Do not invent model availability. Recheck the source capability and workspace target when the source family, task, or documented support changes.

## Profile Isolation

When a project uses multiple access profiles, such as TAP and EMAIL:

- keep profile names explicit in config
- keep credentials redacted from logs and reports
- avoid default Databricks profiles
- validate each profile with direct backend probes
- document which logical model groups each profile can use

For production callers, use a service principal with least-privilege query access when the classified source supports that identity. Keep credential values out of requests, logs, and reports. Apply dedicated URL and OAuth-only route proof only after the source-capability check establishes a supported route-optimized custom-model or feature-serving endpoint. A control-plane Workspace Client query is not evidence that a router used any data-plane transport.

## LiteLLM And LibreChat Rules

- Group provider-specific deployments behind stable logical names.
- Keep fallback order and round-robin behavior explicit.
- Include direct structural and semantic checks for every backend in the group before the router check.
- Keep request and response metadata sufficient to identify the chosen provider without exposing secrets.
- Do not silently mix predictive endpoint validation with LLM router validation. Router health includes logical name resolution, explicit profile and host, target and URL class, authentication source, direct backend proof, and client-visible protocol response shape.

## Manual Classification Fixtures

Use these documentation fixtures when reviewing a routing plan. They do not authorize live calls.

| Fixture | Required route | Required conclusion |
|---|---|---|
| Custom row model with a DataFrame `predict` request | Row/DataFrame v1 contract and doctor | Accept the row branch only after its exact schema and cardinality gates. |
| Unity Catalog-registered chat endpoint | LLM chat branch | Bypass the row contract; registration does not make chat a row request. |
| Responses request with `input` | Responses branch | Reject a `messages` substitution; require an `output` array in the response. |
| Router reply without direct backend evidence | Router branch | Not ready until each selectable backend passes a direct probe. |
| Custom LLM, Foundation Model API, or external model marked route optimized | Source-capability check | Do not apply route-optimized custom-row transport proof without explicit supported-source evidence. |
| Streaming client contract with only a non-streaming success | Streaming proof | Keep streaming unverified until bounded event, terminal, semantic, cancellation, and cleanup checks pass. |

## Lazy Imports

Router and endpoint-management CLIs must support `--help`, config rendering, and dry-run validation without importing heavy agent libraries, ML runtime code, or model clients.

Import heavy dependencies inside the command that needs them.
