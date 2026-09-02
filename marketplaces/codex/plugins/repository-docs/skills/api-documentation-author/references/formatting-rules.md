# Formatting Rules

Use this reference only for markdown presentation after the structure is set.
Use [organization-guide.md](organization-guide.md) for file layout and section grouping.
Use [response-schema-tables.md](response-schema-tables.md) for nested object and array table patterns.

## Tables

Use tables for:

- Parameters (path, query, header)
- Field definitions (request body, response body)
- Status codes (success and error)

## Code Blocks

Always specify a language tag:

- `python` — request examples, response usage
- `json` — response examples, request body examples
- `txt` — method/path declarations
- `markdown` — documentation structure examples

## Notes and Warnings

Use blockquotes only for important information. Do not clutter with obvious comments.

```markdown
> **Security warning:** Never send API keys in query parameters. Use the `Authorization` header.

> **Deprecation:** This endpoint will be removed in v3. Use `POST /users/batch` instead.

> **Permission note:** Consumers see only their own record. Admin-scoped tokens return all users.
```

Prefix with a bold label: `Security warning:`, `Deprecation:`, `Note:`, `Permission note:`.

## Text Style

- Bold sparingly — labels and critical callouts only
- Short paragraphs — one concept each
- Imperative mood in instructions
- No humor in reference docs

## Nested Objects

For nested fields in request or response bodies, choose one:

**Option A — Dotted field names in the table:**

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| preferences.newsletter | boolean | No | Receives newsletters |
| preferences.theme | string | No | Selected UI theme |

**Option B — Structured list:**

- `preferences`: object
  - `newsletter`: boolean — whether the user receives newsletters
  - `theme`: string — selected UI theme

Use whichever reads better for the depth and complexity.

## Add Only When Relevant

Add these sections only when they materially apply:

- Pagination behavior
- Sorting and filtering
- Enum values
- Nullable fields
- Retries and backoff
- Idempotency keys
- Webhooks triggered by the operation
- Asynchronous processing behavior
- Eventual consistency caveats
- Deprecation or migration notes
- Permission/scope requirements
- Rate limiting details
- Resource lifecycle/state transitions

## Final Pass

Before finalizing:

- [ ] Heading levels are consistent
- [ ] Every code block has a language tag
- [ ] Tables have a stable column count and no placeholder rows
- [ ] Examples and tables do not contradict each other
- [ ] Unknowns are marked as `TBD`, `Unknown`, or `Not documented`
- [ ] Warnings use blockquotes only when they are real warnings or caveats
- [ ] Markdown reads cleanly in raw form
