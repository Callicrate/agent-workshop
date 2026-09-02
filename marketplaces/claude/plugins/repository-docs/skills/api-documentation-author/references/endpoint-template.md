# Endpoint Template

Copy this template for each endpoint. Include only sections that have meaningful content — omit any section that would just say "None" or "No special headers required."

---

## METHOD /path

Short summary of what the endpoint does and when to use it.

- **Purpose:** ...
- **Authentication:** required auth mechanism or `None`
- **Authorization:** required role/scope/permission or `None`
- **Rate limits:** specific limit or `Not documented`
- **Idempotency:** yes/no if relevant
- **API version:** version if applicable
- **Stability:** `Stable`, `Beta`, `Deprecated`

### Method and path

```txt
METHOD /path
```

### Headers

| Header | Required | Value | Notes |
| ------ | -------- | ----- | ----- |
| Authorization | Yes | Bearer `<token>` | Required for authenticated calls |
| Content-Type | Yes | application/json | Required when sending JSON |

_Include this section only when there are headers worth documenting. Omit entirely for endpoints with no special header requirements._

### Path parameters

_Include only when the endpoint has path parameters._

| Name | Type | Required | Description | Example |
| ---- | ---- | -------- | ----------- | ------- |
| userId | string | Yes | Unique user identifier | `usr_123` |

### Query parameters

_Include only when the endpoint has query parameters._

| Name | Type | Required | Default | Description | Example |
| ---- | ---- | -------- | ------- | ----------- | ------- |
| page | integer | No | `1` | Page number | `2` |

### Request body

_Include only when the endpoint accepts a request body._

Short description of what the body represents, followed by the field table and a complete example.

| Field | Type | Required | Description | Constraints | Example |
| ----- | ---- | -------- | ----------- | ----------- | ------- |
| name | string | Yes | User display name | 1-100 chars | `"Jane Doe"` |
| email | string | Yes | User email address | valid email | `"jane@example.com"` |

For nested objects, arrays, or unions, use the patterns in [response-schema-tables.md](response-schema-tables.md) instead of inventing a one-off layout here.

### Example request

Use raw `requests` calls without generic boilerplate imports. Assume the reader knows how to install and import `requests`; focus on endpoint-specific URL, headers, params, and body.

```python
response = requests.post(
    "https://api.example.com/users",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
    json={"name": "Jane Doe", "email": "jane@example.com"},
)
```

### Success responses

| Status | Meaning | When it happens |
| ------ | ------- | --------------- |
| `201 Created` | Resource created | New user was created |

### Example success response

```json
{
  "id": "usr_123456",
  "name": "Jane Doe",
  "email": "jane@example.com",
  "created_at": "2026-03-09T12:00:00Z"
}
```

### Using the response

Always show how to extract the useful data. Match the pattern to the response shape.

**Single object:**
```python
user = response.json()
print(user["id"], user["email"])
```

**List of objects:**
```python
users = response.json()["data"]
for user in users:
    print(user["id"], user["name"])
```

**Paginated response (include cursor/next page logic):**
```python
page = response.json()
users = page["data"]
next_cursor = page["meta"]["next_cursor"]
```

**No-content response (204):**
```python
assert response.status_code == 204  # user deleted
```

Use whichever pattern fits the actual response. Adapt field names to match the real API.

### Response body

For nested objects, arrays, or polymorphic responses, use the patterns in [response-schema-tables.md](response-schema-tables.md).

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| id | string | Unique identifier | `usr_123456` |
| name | string | User display name | `"Jane Doe"` |
| email | string | User email address | `"jane@example.com"` |
| created_at | string | ISO 8601 timestamp | `2026-03-09T12:00:00Z` |

### Error responses

| Status | Meaning | Cause | Notes |
| ------ | ------- | ----- | ----- |
| `400 Bad Request` | Invalid request | Missing or invalid input | Check required fields |
| `401 Unauthorized` | Auth failed | Missing or invalid token | Supply a valid bearer token |
| `403 Forbidden` | Not allowed | Insufficient permission | Requires proper scope/role |
| `404 Not Found` | Resource missing | Unknown identifier | Verify the path parameter |
| `409 Conflict` | Conflict | Duplicate or incompatible state | Often retriable after correction |
| `429 Too Many Requests` | Rate limited | Too many requests | Honor retry guidance |
| `500 Internal Server Error` | Server error | Unexpected failure | Retry or contact support |

### Notes

_Include only when there are important caveats, warnings, migration guidance, or security notes for this specific endpoint. Omit this section entirely otherwise._
