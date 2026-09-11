# <API Name> API

> **Client library:** This API has an official Python client — [`<package-name>`](https://pypi.org/project/<package-name>/). The examples below use raw `requests` for clarity, but prefer the client library in production code.

_Remove the client library callout above if no official client exists._

## Overview

_Brief description of what this API does and who it's for._

## Authentication

_Describe the auth mechanism — see [auth-patterns.md](../references/auth-patterns.md) for common patterns._

- **Method:** Bearer token / API key / OAuth2
- **Header:** `Authorization: Bearer <token>`

## Common Patterns

_Document pagination, error schema, and rate limits here. See [common-patterns.md](../references/common-patterns.md) for templates._

## Endpoints

Group endpoint files by object type or workflow first. Use the audience column and section headings inside endpoint files to show permissions.

| File | Audience | Description |
| ---- | -------- | ----------- |
| `endpoints/users.md` | Consumer | User CRUD, preferences, profile |
| `endpoints/orders.md` | Consumer | Order lifecycle, fulfillment, history |
| `endpoints/admin.md` | Admin | User suspension, role assignment, config |

_Update the table above to reflect your actual endpoint files._

## Error Response Format

_Document the standard error body here if the API uses one across all endpoints._

```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable message"
  }
}
```
