# Response Schema Table Format

Consistent patterns for documenting response body fields, including nested objects, arrays, and polymorphic types (oneOf/anyOf).

## Flat Object

For simple response bodies with no nesting:

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| id | string | Unique identifier | `usr_123456` |
| name | string | User display name | `"Jane Doe"` |
| created_at | string | ISO 8601 timestamp | `2026-03-09T12:00:00Z` |

## Nested Objects

### Option A — Dotted notation (preferred for 1-2 levels)

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| id | string | Unique identifier | `usr_123456` |
| address.street | string | Street address | `"123 Main St"` |
| address.city | string | City name | `"Portland"` |
| address.zip | string | ZIP/postal code | `"97201"` |

### Option B — Structured list (preferred for 3+ levels or complex nesting)

- `id`: string — Unique identifier
- `address`: object
  - `street`: string — Street address
  - `city`: string — City name
  - `coordinates`: object
    - `lat`: number — Latitude
    - `lng`: number — Longitude

### When to use which

| Depth | Recommendation |
|-------|---------------|
| 1-2 levels | Dotted notation in table |
| 3+ levels | Structured list |
| Mixed simple + deep | Table for top-level, structured list for deep nested sub-objects |

## Arrays

### Array of primitives

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| tags | string[] | List of tag labels | `["urgent", "billing"]` |
| scores | number[] | Confidence scores | `[0.95, 0.87, 0.72]` |

### Array of objects

| Field | Type | Description |
| ----- | ---- | ----------- |
| items | object[] | List of order line items |

Each item in `items`:

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| product_id | string | Product identifier | `prod_789` |
| quantity | integer | Number ordered | `2` |
| unit_price | number | Price per unit in cents | `1999` |

### Nested array within array

Use structured list format for deeply nested arrays:

- `orders`: object[]
  - `id`: string — Order identifier
  - `items`: object[]
    - `product_id`: string — Product identifier
    - `quantity`: integer — Number ordered
    - `modifiers`: object[]
      - `type`: string — Modifier type (`"discount"`, `"addon"`)
      - `value`: number — Modifier value

## Polymorphic Types (oneOf / anyOf)

When a field can be one of several shapes, document each variant separately.

### Discriminated union (common pattern)

The `type` field determines the shape:

| Field | Type | Description |
| ----- | ---- | ----------- |
| type | string | Event type — determines the `data` shape |
| data | object | Event payload (see variants below) |

**When `type` = `"user.created"`:**

| Field | Type | Description |
| ----- | ---- | ----------- |
| data.user_id | string | New user's identifier |
| data.email | string | New user's email |

**When `type` = `"order.completed"`:**

| Field | Type | Description |
| ----- | ---- | ----------- |
| data.order_id | string | Completed order identifier |
| data.total | number | Order total in cents |

### Non-discriminated union

When there is no discriminator field, list all possible shapes:

| Field | Type | Description |
| ----- | ---- | ----------- |
| result | string \| object | Either a URL string or an object with `url` and `metadata` fields |

**Shape 1 — string:**
A direct URL string: `"https://cdn.example.com/file.pdf"`

**Shape 2 — object:**

| Field | Type | Description |
| ----- | ---- | ----------- |
| result.url | string | Download URL |
| result.metadata.size | integer | File size in bytes |
| result.metadata.mime_type | string | MIME type |

## Nullable Fields

Mark nullable fields explicitly in the type column:

| Field | Type | Description | Example |
| ----- | ---- | ----------- | ------- |
| deleted_at | string \| null | ISO 8601 timestamp, null if not deleted | `null` |
| avatar_url | string \| null | URL to avatar image, null if not set | `"https://..."` |

## Enum Values

List valid values inline or in a separate row:

| Field | Type | Description | Values |
| ----- | ---- | ----------- | ------ |
| status | string | Current order status | `pending`, `processing`, `shipped`, `delivered`, `cancelled` |
| priority | integer | Priority level (1 = highest) | `1`, `2`, `3` |
