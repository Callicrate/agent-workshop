# Request Template Checklist

Use this checklist for API docs that need exact copyable requests, especially integration tasks, internal tools, and debugging handoffs.

Each endpoint example should state:

- method
- path
- base URL variable
- required headers
- authentication mechanism
- query parameters
- JSON body, if any
- expected status codes
- response fields used as evidence
- pagination or retry behavior
- safety boundary for state-changing calls

## Evidence-Oriented Examples

For investigative or debugging APIs, include what proves success:

```text
Success evidence: response status 200 plus `<field>` equals `<expected state>`.
Failure evidence: response status, error code, request ID, and the exact field that was rejected.
```

Do not hide required headers, request IDs, or response fields behind prose when the user needs to reproduce the call.

## Sensitive Values

Use placeholders for secrets:

- `<token>`
- `<cookie>`
- `<workspace-host>`
- `<account-id>`

Explain where the value comes from without pasting real secret material.