# Common API Patterns

How to document pagination, error schemas, rate limits, and other cross-cutting patterns that appear across multiple endpoints.

Document these in the README (for folder-based APIs) or in a "Common patterns" section near the top (for single-file APIs). Then reference them from individual endpoints rather than repeating.

## Pagination

### Cursor-based pagination

````markdown
### Pagination

This API uses cursor-based pagination. Responses include a `meta` object with pagination info.

| Field | Type | Description |
| ----- | ---- | ----------- |
| meta.next_cursor | string \| null | Pass as `cursor` query param to get the next page. `null` when no more results. |
| meta.has_more | boolean | Whether more results exist beyond this page. |

**Query parameters:**

| Name | Type | Required | Default | Description |
| ---- | ---- | -------- | ------- | ----------- |
| cursor | string | No | — | Cursor from previous response |
| limit | integer | No | `20` | Results per page (max 100) |

**Example — fetching all pages:**

```python
cursor = None
all_users = []
while True:
    params = {"limit": 50}
    if cursor:
        params["cursor"] = cursor
    response = requests.get(
        "https://api.example.com/users",
        headers={"Authorization": "Bearer YOUR_API_KEY"},
        params=params,
    )
    page = response.json()
    all_users.extend(page["data"])
    cursor = page["meta"]["next_cursor"]
    if not cursor:
        break
```
````

### Offset-based pagination

````markdown
### Pagination

This API uses offset-based pagination.

| Name | Type | Required | Default | Description |
| ---- | ---- | -------- | ------- | ----------- |
| offset | integer | No | `0` | Number of results to skip |
| limit | integer | No | `20` | Results per page (max 100) |

Response includes:

| Field | Type | Description |
| ----- | ---- | ----------- |
| total | integer | Total number of matching results |
| offset | integer | Current offset |
| limit | integer | Current limit |

**Example:**

```python
response = requests.get(
    "https://api.example.com/orders",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
    params={"offset": 40, "limit": 20},
)
page = response.json()
orders = page["data"]
print(f"Showing {len(orders)} of {page['total']} results")
```
````

### Which to use

Document whichever the API actually uses. Do not assume. If the API docs don't specify, test it or mark as `TBD`.

## Error Schema

If the API returns a standard error body across all endpoints, document it once and reference it.

### Standard error body

````markdown
### Error response format

All error responses use this format:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The 'email' field must be a valid email address.",
    "details": [
      {
        "field": "email",
        "issue": "invalid_format",
        "message": "Must be a valid email address"
      }
    ]
  }
}
```

| Field | Type | Description |
| ----- | ---- | ----------- |
| error.code | string | Machine-readable error code |
| error.message | string | Human-readable summary |
| error.details | array \| null | Field-level errors (present on validation errors) |
| error.details[].field | string | The field that failed validation |
| error.details[].issue | string | Machine-readable issue code |
| error.details[].message | string | Human-readable field error |

**Parsing errors in Python:**

```python
if not response.ok:
    error = response.json()["error"]
    print(f"{error['code']}: {error['message']}")
    if error.get("details"):
        for d in error["details"]:
            print(f"  {d['field']}: {d['message']}")
```
````

If the API does not use a standard error schema, document the error body shape per-endpoint.

## Rate Limits

### Rate limit headers

````markdown
### Rate limits

The API returns rate limit info in response headers:

| Header | Description | Example |
| ------ | ----------- | ------- |
| X-RateLimit-Limit | Max requests per window | `1000` |
| X-RateLimit-Remaining | Requests remaining in current window | `742` |
| X-RateLimit-Reset | Unix timestamp when the window resets | `1741539600` |
| Retry-After | Seconds to wait (only on 429 responses) | `30` |

When you receive `429 Too Many Requests`, wait for the `Retry-After` duration before retrying.

**Checking rate limits in Python:**

```python
remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
if remaining < 10:
    reset = int(response.headers["X-RateLimit-Reset"])
    wait = reset - time.time()
    print(f"Rate limit nearly exhausted. Resets in {wait:.0f}s")
```
````

### Per-endpoint limits

If specific endpoints have different rate limits, document them in the endpoint's metadata block:

```markdown
- **Rate limits:** 10 requests/minute (stricter than the default 1000/hour)
```

## Retry and Backoff

````markdown
### Retry guidance

For `429` and `5xx` responses, retry with exponential backoff:

```python
import time

def request_with_retry(method, url, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        response = requests.request(method, url, **kwargs)
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return response
    return response  # return last response after exhausting retries
```

Do not retry `4xx` errors other than `429` — they indicate a client problem.
````

## Non-Standard Response Formats

When an API returns non-standard output, include a parsing example in the endpoint doc. Standard JSON needs only `response.json()` — only add these when the format is actually non-standard.

### Binary / file download

```python
with open("export.csv", "wb") as f:
    f.write(response.content)
```

### NDJSON (newline-delimited JSON)

```python
import json
records = [json.loads(line) for line in response.text.strip().splitlines()]
```

### Single-quoted Python-style dicts

Some APIs return Python-style dicts instead of valid JSON.

```python
import ast
data = ast.literal_eval(response.text)
```

### XML responses

```python
import xml.etree.ElementTree as ET
root = ET.fromstring(response.text)
```

### Streaming responses

```python
with requests.get(url, headers=headers, stream=True) as r:
    for line in r.iter_lines():
        if line:
            record = json.loads(line)
            process(record)
```

## Idempotency

````markdown
### Idempotency

For endpoints that accept an `Idempotency-Key` header, include a unique key to safely retry requests without creating duplicates.

```python
import uuid

response = requests.post(
    "https://api.example.com/payments",
    headers={
        "Authorization": "Bearer YOUR_API_KEY",
        "Idempotency-Key": str(uuid.uuid4()),
    },
    json={"amount": 5000, "currency": "usd"},
)
```

The server stores the result for a given key. Repeating the same request with the same key returns the original response without re-processing.
````

## Webhooks

If endpoints trigger webhooks, document them alongside the endpoint:

```markdown
> **Webhook:** Creating an order triggers a `order.created` webhook event. See [Webhooks documentation](../webhooks.md) for payload format and delivery details.
```

## When to Use These Patterns

- **Pagination:** Document when any list endpoint returns paginated results
- **Error schema:** Document once if the schema is shared; per-endpoint if it varies
- **Rate limits:** Document when the API enforces them and exposes headers
- **Retry/backoff:** Document when the API has `429` or transient `5xx` behavior
- **Idempotency:** Document when endpoints accept idempotency keys
- **Webhooks:** Document when an endpoint triggers async events
