# Auth Patterns

How to document different authentication and authorization mechanisms. Use whichever pattern matches the actual API — do not assume Bearer token.

## Bearer Token (most common)

```markdown
- **Authentication:** Bearer token in `Authorization` header

### Headers

| Header | Required | Value | Notes |
| ------ | -------- | ----- | ----- |
| Authorization | Yes | Bearer `<token>` | Obtain from login or API key dashboard |
```

```python
response = requests.get(
    "https://api.example.com/users/me",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
)
```

## API Key in Header

Some APIs use a custom header instead of the `Authorization` header.

```markdown
- **Authentication:** API key via `X-API-Key` header

### Headers

| Header | Required | Value | Notes |
| ------ | -------- | ----- | ----- |
| X-API-Key | Yes | `<api-key>` | Found in your account settings |
```

```python
response = requests.get(
    "https://api.example.com/data",
    headers={"X-API-Key": "YOUR_API_KEY"},
)
```

## API Key in Query Parameter

Some APIs pass the key as a query parameter. Document it, but include the security warning.

```markdown
- **Authentication:** API key via `api_key` query parameter

> **Security warning:** API keys in query parameters may appear in server logs and browser history. Use header-based auth when available.

### Query parameters

| Name | Type | Required | Description |
| ---- | ---- | -------- | ----------- |
| api_key | string | Yes | Your API key |
```

```python
response = requests.get(
    "https://api.example.com/data",
    params={"api_key": "YOUR_API_KEY"},
)
```

## Basic Auth

```markdown
- **Authentication:** HTTP Basic Auth (username:password or username:api_key)
```

```python
response = requests.get(
    "https://api.example.com/account",
    auth=("your_username", "YOUR_API_KEY"),
)
```

Note: `requests` handles the Base64 encoding and `Authorization: Basic ...` header automatically.

## OAuth2 — Client Credentials

For server-to-server auth where no user context is needed.

````markdown
- **Authentication:** OAuth2 client credentials flow

### Getting a token

```txt
POST /oauth/token
```

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| grant_type | string | Yes | Must be `client_credentials` |
| client_id | string | Yes | Your application's client ID |
| client_secret | string | Yes | Your application's client secret |
| scope | string | No | Space-separated list of scopes |

```python
token_response = requests.post(
    "https://api.example.com/oauth/token",
    data={
        "grant_type": "client_credentials",
        "client_id": "YOUR_CLIENT_ID",
        "client_secret": "YOUR_CLIENT_SECRET",
        "scope": "read write",
    },
)
access_token = token_response.json()["access_token"]
```

Then use the token for subsequent requests:

```python
response = requests.get(
    "https://api.example.com/users",
    headers={"Authorization": f"Bearer {access_token}"},
)
```
````

## OAuth2 — Authorization Code

For user-facing applications where the user grants access.

````markdown
- **Authentication:** OAuth2 authorization code flow

### Flow

1. Redirect user to authorization URL
2. User grants access → redirected back with `code`
3. Exchange `code` for `access_token`
4. Use `access_token` for API calls

### Token exchange

```txt
POST /oauth/token
```

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| grant_type | string | Yes | Must be `authorization_code` |
| code | string | Yes | Authorization code from redirect |
| redirect_uri | string | Yes | Must match the registered redirect URI |
| client_id | string | Yes | Your application's client ID |
| client_secret | string | Yes | Your application's client secret |

```python
token_response = requests.post(
    "https://api.example.com/oauth/token",
    data={
        "grant_type": "authorization_code",
        "code": "AUTH_CODE_FROM_REDIRECT",
        "redirect_uri": "https://yourapp.com/callback",
        "client_id": "YOUR_CLIENT_ID",
        "client_secret": "YOUR_CLIENT_SECRET",
    },
)
tokens = token_response.json()
access_token = tokens["access_token"]
refresh_token = tokens.get("refresh_token")
```
````

### Token refresh

If the API supports refresh tokens, document it:

````markdown
### Refreshing a token

```python
token_response = requests.post(
    "https://api.example.com/oauth/token",
    data={
        "grant_type": "refresh_token",
        "refresh_token": "YOUR_REFRESH_TOKEN",
        "client_id": "YOUR_CLIENT_ID",
        "client_secret": "YOUR_CLIENT_SECRET",
    },
)
access_token = token_response.json()["access_token"]
```
````

## Multi-Step / Custom Auth

Some APIs require non-standard auth (e.g., HMAC signing, two-step token exchange, mTLS). Document the full flow step-by-step:

````markdown
- **Authentication:** HMAC-signed requests

### Signing requests

Each request must include a signature in the `X-Signature` header, computed from the request body and your secret key.

```python
import hashlib
import hmac
import json

body = json.dumps({"amount": 5000})
signature = hmac.new(
    b"YOUR_SECRET_KEY",
    body.encode(),
    hashlib.sha256,
).hexdigest()

response = requests.post(
    "https://api.example.com/payments",
    headers={
        "X-API-Key": "YOUR_API_KEY",
        "X-Signature": signature,
        "Content-Type": "application/json",
    },
    data=body,
)
```
````

## Authorization (Scopes and Roles)

After documenting the auth mechanism, document the authorization model.

### Scope-based

```markdown
### Scopes

| Scope | Description |
| ----- | ----------- |
| `user:read` | Read user profiles and preferences |
| `user:write` | Create and update user data |
| `admin:users` | Suspend, delete, and manage all users |
| `billing:read` | View invoices and subscription status |
| `billing:write` | Update payment methods, change plans |
```

### Role-based

```markdown
### Roles

| Role | Permissions | Notes |
| ---- | ----------- | ----- |
| viewer | Read-only access to own resources | Default role for new users |
| editor | Read and write access to own resources | — |
| admin | Full access to all resources | Includes user management |
```

## Where to Put Auth Documentation

- **Single-file API:** Auth section near the top, before any endpoints
- **Folder-based API:** In `README.md` under "Authentication" heading, with a summary repeated at the top of each endpoint file
- **Per-endpoint:** In the metadata block (`Authentication:` and `Authorization:` fields)
