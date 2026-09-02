# <API Name> API

> **Client library:** This API has an official Python client — [`<package-name>`](https://pypi.org/project/<package-name>/). The examples below use raw `requests` for clarity, but prefer the client library in production code.

_Remove the client library callout above if no official client exists._

## Authentication

_Describe the auth mechanism — see [auth-patterns.md](../references/auth-patterns.md) for common patterns._

- **Method:** Bearer token / API key / OAuth2
- **Header:** `Authorization: Bearer <token>`

## Common Patterns

_Document pagination, error schema, and rate limits here. See [common-patterns.md](../references/common-patterns.md) for templates._

## Public Endpoints

_Endpoints that require no authentication. Remove this section if none exist._

### GET /example/public

Short summary of what this endpoint does.

- **Purpose:** ...
- **Authentication:** None
- **Stability:** Stable

#### Example request

```python
response = requests.get("https://api.example.com/example/public")
```

#### Success responses

| Status | Meaning | When it happens |
| ------ | ------- | --------------- |
| `200 OK` | Success | Request valid |

#### Example response

```json
{}
```

#### Error responses

| Status | Meaning | When it happens |
| ------ | ------- | --------------- |
| `400 Bad Request` | Input rejected | Request does not match the contract |

## Consumer Endpoints

_Standard authenticated user operations. Remove this section if none exist._

## Elevated Endpoints

_Higher privilege operations. Remove this section if none exist._

## Admin Endpoints

_Full admin access operations. Remove this section if none exist._
