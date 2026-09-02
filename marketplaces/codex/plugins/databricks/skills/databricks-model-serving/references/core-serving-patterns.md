# Core Serving Patterns

Use this reference for detailed Databricks serving work. The base `SKILL.md` should only route the agent here.

## Configuration Contract

- Start from [assets/serving-config-template.py](../assets/serving-config-template.py).
- Validate the complete JSON contract with `python scripts/validate_endpoint_config.py --config <contract.json>` before any SDK or API operation.
- Keep catalog, schema, model name, endpoint name, config version, creator identity, exact scaling mode and fields, exact routes, target URL, request shape, response cardinality, and telemetry locations explicit.
- Use [assets/endpoint-config-schema.json](../assets/endpoint-config-schema.json) as the stable contract for endpoint configuration.
- Carry the upstream model handoff: resolved UC model version or alias, MLflow run ID, model signature, input example, feature list or lookup keys, label mapping, threshold artifact, and data window.
- Carry the output contract: required response fields, score direction and range, decision labels, fallback labels, thresholds, and semantic smoke fixtures.
- Reconcile the full `{name, logical_type, nullable}` declaration across signature inputs and feature schema, then bind every fixture to those exact fields before constructing a client. Reconcile signature outputs with required fields, nullability, score types, label types, predicates, and thresholds.
- Apply canonical temporal and binary semantics on both request and response values. `date` is a real round-tripping `YYYY-MM-DD`. `timestamp` is bounded RFC3339 with uppercase `T`, explicit `Z` or `±HH:MM`, required seconds, and either no fractional seconds or exactly 3 or 6 digits. `binary` is nonempty standard padded Base64, passes strict alphabet and padding validation, re-encodes identically, and stays within the decoded-byte cap.

## Critical Deployment Rules

- Do not embed large data assets inside the model artifact.
- Do not mix declarative DAB endpoint management with imperative SDK rollout for the same endpoint.
- If an endpoint is DAB-managed, use the bundle as source of truth; use imperative API/SDK changes only for read-only inspection, emergency remediation, or explicitly non-bundle-managed endpoints.
- Do not set `DATABRICKS_HOST` inside Databricks serving containers.
- When calling the SQL Statement Execution API from serving code, handle `PENDING` and `RUNNING` states explicitly.
- Do not treat endpoint `READY` as done. Use [semantic-readiness-patterns.md](semantic-readiness-patterns.md) before completion.
- Do not perform destructive endpoint operations without the target manifest in [prod-target-safety.md](prod-target-safety.md).
- Do not import heavy serving, model, or agent libraries at CLI module import time. Endpoint CLIs must allow help, config rendering, and dry-run checks with only lightweight imports.

## Scaling Contract

Declare exactly one scaling mode per served entity and compare every field in the settled snapshot:

- `workload_size`: `workload_size`, `workload_type`, and `scale_to_zero_enabled`.
- `provisioned_concurrency`: `min_provisioned_concurrency`, `max_provisioned_concurrency`, `workload_type`, and `scale_to_zero_enabled`. Do not also set `workload_size`.
- `provisioned_throughput`: `min_provisioned_throughput`, `max_provisioned_throughput`, `workload_type`, and `scale_to_zero_enabled`.
- `provisioned_model_units`: `provisioned_model_units`, `burst_scaling_enabled`, `workload_type`, and `scale_to_zero_enabled`.

The minimum cannot exceed the maximum. Treat every numeric value as exact; a change from 4/8 concurrency to 400/800 is configuration drift, not an equivalent scale class. The current field definitions and mutual exclusion between workload size and custom concurrency are in the [Serving Endpoint API](https://docs.databricks.com/api/model-serving/v1/serving-endpoint).

For AWS custom-LLM serving in Beta, `GPU_XLARGE` is the 1x H100 workload and currently supports neither route optimization nor scale-to-zero. It is limited to `us-west-2` and requires additional enrollment. The offline schema enforces the two static incompatibilities, but it cannot prove current region, enrollment, or capacity eligibility; verify those dynamic conditions separately against the workspace and the [current custom-LLM serving documentation](https://docs.databricks.com/aws/en/machine-learning/model-serving/serve-custom-llms) before deployment.

## Endpoint Lifecycle

- Load models from Unity Catalog using aliases or explicit versions.
- Configure current Unity AI Gateway telemetry with `telemetry_config`; treat legacy `auto_capture_config` only as a migration input.
- Roll out new versions with explicit update calls.
- Use canary traffic or staged aliases for controlled promotion.
- Validate that endpoint input schema and traffic target match the recorded training or promotion handoff before shifting production traffic.
- Validate semantic output behavior with direct sample queries before notebook, client, or app validation.

Example alias pattern. Only run production alias changes after explicit approval or an existing task contract that grants alias movement:

```python
from mlflow import MlflowClient

client = MlflowClient()
client.set_registered_model_alias(
    name=f"{CATALOG}.{SCHEMA}.{MODEL_NAME}",
    alias="Production",
    version=MODEL_VERSION,
)
```

## Batch vs Real-Time

- Start with task-specific AI Functions when one fits. Use `ai_query` for flexible SQL or Python batch inference against supported Databricks-hosted, custom, fine-tuned, or external models. Databricks manages parallelism, retries, and scaling for that batch path; submit the full dataset rather than manually fragmenting it. See [Use ai_query](https://docs.databricks.com/aws/en/large-language-models/ai-query).
- For custom traditional ML or deep-learning models, `ai_query` still requires a custom serving endpoint. For provisioned-throughput models, AI Functions does not use the endpoint's provisioned compute, so capacity and cost assumptions differ from real-time traffic.
- Use direct model scoring, `mlflow.pyfunc`, or Spark UDFs when endpoint-backed batch inference adds cost or governance without a useful serving boundary.
- Use direct endpoint queries when low latency, external invocation, streaming response, or endpoint-specific traffic control matters.
- Design shared helper code for SQL lookups, clients, and schema validation if both paths exist.

## Feature Serving and Monitoring

- Use feature lookups only when the model contract truly depends on live lookup keys.
- Current telemetry and Unity AI Gateway inference tables should feed monitoring and drift checks through their published schemas or a project-owned normalized view.
- Use [monitoring-patterns.md](monitoring-patterns.md) for the detailed monitoring workflow.

## Identity And Route-Optimized Endpoints

- Create production endpoints under a durable service principal. Databricks records the creator identity at creation, uses it to access Unity Catalog resources, and does not allow it to be changed later.
- Preserve the creator's workspace membership plus `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` on the Unity Catalog model. Include `EXECUTE` on transitive functions when the model declares them. See [custom endpoint identity and access](https://docs.databricks.com/aws/en/machine-learning/model-serving/create-manage-serving-endpoints#identity-and-access).
- Give production callers only the endpoint permission they need, normally `CAN_QUERY`.
- Route-optimized endpoints use the dedicated `endpoint_url` returned by the endpoint API and accept OAuth only. Derive the route ID from the first label of that dedicated host. Production applications use a nonsecret service-principal identifier, M2M OAuth, `CAN_QUERY`, and exactly one endpoint-scoped authorization detail: `type=workspace_permission`, `object_type=serving-endpoints`, `object_path=/serving-endpoints/<route-id>`, and `actions=[query_inference_endpoint]`. A plain `all-apis` token or personal access token is not sufficient. A control-plane `WorkspaceClient.serving_endpoints.query` call does not prove the dedicated OAuth transport. See [Query route-optimized serving endpoints](https://docs.databricks.com/aws/en/machine-learning/model-serving/query-route-optimization).

## LLM Endpoint Routers

When Databricks endpoints are LLM backends behind LiteLLM, LibreChat, or an OpenAI-compatible gateway, load [llm-router-serving-patterns.md](llm-router-serving-patterns.md).
The validation surface is logical model routing, provider-specific backend probes, profile isolation, credentials redaction, and client-visible response shape.

## Deterministic Helpers

- [scripts/check_endpoint.py](../scripts/check_endpoint.py) verifies endpoint state and configuration.
- [scripts/check_endpoint.py](../scripts/check_endpoint.py) can run direct sample queries and semantic output assertions.
- [assets/serving-config-template.py](../assets/serving-config-template.py) keeps endpoint configuration consistent.
- [assets/endpoint-config-schema.json](../assets/endpoint-config-schema.json) defines the configuration contract.
