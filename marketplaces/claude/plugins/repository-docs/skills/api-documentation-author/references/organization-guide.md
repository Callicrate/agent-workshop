# Organization Guide

How to structure API and interface documentation based on size, permissions, and engineer workflows.

## Default Output Location

Follow existing project conventions when API or interface docs already live somewhere.
If no convention exists, use the surface type:

- **Single-file REST API:** `./docs/apis/<api-name>.md`
- **Complex REST API (folder):** `./docs/apis/<api-name>/README.md` with `./docs/apis/<api-name>/endpoints/`
- **Non-REST interface contract:** `./docs/interfaces/<surface-name>.md`
- **Complex non-REST interface (folder):** `./docs/interfaces/<surface-name>/README.md` with task-specific child files

Keep install and access reproduction docs near existing operations docs when the repo already has that convention.

## Structure Decision Tree

Use this to quickly decide between single-file and folder structure. Permissions matter, but file splits should primarily match how engineers use the API.

```
                    How many endpoints?
                          |
               +----------+----------+
               |                     |
           <= 15                   > 15
               |                     |
     All same permission?      Use FOLDER structure
        |             |              |
       Yes           No         Split by object type
        |             |         (users.md, orders.md, ...)
   Total < 500     > 3 tiers
    lines?          or > 8 per
     |    |         group?
    Yes   No         |    |
     |     |        Yes   No
  SINGLE  FOLDER   FOLDER SINGLE
   FILE            (with admin.md  FILE
                    if needed)     (group by tier)
```

**Quick rules:**
- <= 15 endpoints, single permission tier, < 500 lines → **single file**
- > 15 endpoints → **folder**, always
- Mixed permissions with > 3 tiers or > 8 endpoints per group → **folder**
- When in doubt, start single-file; split when it becomes unwieldy

## Permission Tiers

Classify every endpoint into a tier before writing:

| Tier | Who | Examples |
|------|-----|----------|
| Public | Anyone, no auth | Health checks, public listings, OAuth discovery |
| Consumer | Standard authenticated user | CRUD on own resources, preferences, profile |
| Elevated | Higher privilege (team lead, org manager, billing admin) | Team management, billing changes |
| Admin / Internal | Full admin, system-level | User suspension, role assignment, feature flags, config |

If the API defines its own roles or scopes, use those names and map them to these tiers.

## Single-File Organization

For APIs with ≤15 endpoints, same permission level, or <500 lines total.

Group endpoints by permission tier within the file. Each section gets:
1. A heading for the tier
2. A 1-2 sentence description of what the section covers
3. The required role/scope stated clearly
4. Endpoints ordered logically (list → get → create → update → delete)

```markdown
# Users API

## Public endpoints

Endpoints that require no authentication. Use these for public-facing profile lookups.

### GET /users/{id}/public-profile
...

## Consumer endpoints

Standard authenticated user operations for managing your own account and preferences.

### GET /users/me
### PATCH /users/me
...

## Admin endpoints

Restricted operations for managing other users. Requires the `admin` role.

### DELETE /users/{id}
### POST /users/{id}/suspend
...
```

### Mixed permissions

If an endpoint behaves differently by role (e.g., `GET /users` returns all users for admins, only self for consumers), document it once in the highest tier. Call out the differences:

> **Permission note:** Consumers see only their own record. Admin-scoped tokens return all users.

## Folder Structure

For APIs with >15 endpoints or >8 per logical group.

### Layout

```
docs/apis/
  api-name/
    README.md              # API overview, auth guide, common patterns
    endpoints/
      users.md             # Everything about user objects (CRUD, preferences, profile)
      orders.md            # Order lifecycle (create, update, fulfill, cancel)
      billing.md           # Subscriptions, invoices, payment methods
      admin.md             # Admin-only operations (suspension, role assignment, config)
```

### How to decide where to split

Think from the perspective of an engineer building a feature. They want all endpoints related to their workflow in one place.

- **Group by object type and workflow first.** All endpoints operating on the same resource or feature flow go in one file. An engineer working on user features should not jump between files just because permissions differ.
- **Keep the full lifecycle together.** CRUD, state transitions, and related queries for one object type belong in the same file. `POST /orders`, `GET /orders/{id}`, `PATCH /orders/{id}/cancel`, `GET /orders/{id}/history` → all in `orders.md`.
- **Separate admin endpoints only when they form their own workflow.** 1-2 admin endpoints for a resource? Keep them in the resource file under an "Admin" heading. Many admin operations? Dedicated `admin.md`.
- **Group by persona when object types overlap.** If a workflow spans multiple types (onboarding touches users, teams, billing), consider a workflow-oriented file like `onboarding.md`.
- **Preserve permission visibility inside each file.** Use headings, callouts, and the README index to make roles and scopes clear without splitting one workflow across many files.
- **Ask: "Where would an engineer look for this?"** If the answer isn't obvious, the split is wrong.

### README.md requirements

The README is the index. It must contain:

1. Brief API overview
2. Authentication and authorization summary
3. Table linking to each endpoint file
4. Common patterns (pagination, error schema, rate limits)

```markdown
## Endpoints

| File | Audience | Description |
| ---- | -------- | ----------- |
| [endpoints/users.md](endpoints/users.md) | Consumer | User CRUD, preferences, profile |
| [endpoints/orders.md](endpoints/orders.md) | Consumer | Order lifecycle, fulfillment, history |
| [endpoints/billing.md](endpoints/billing.md) | Consumer | Subscriptions, invoices, payment methods |
| [endpoints/admin.md](endpoints/admin.md) | Admin | User suspension, role assignment, system config |
```

### Endpoint file requirements

Each file must be self-contained:

- Auth summary at the top (no flipping back to README)
- 1-2 sentence description at the start of the file and at the start of every section within it
- No heading goes straight into endpoint definitions without context

Example of a self-contained endpoint file header:

```markdown
# User Endpoints

Endpoints for managing user accounts, profiles, and preferences. All endpoints require a valid Bearer token with `user:read` or `user:write` scope.

- **Authentication:** Bearer token
- **Base URL:** `https://api.example.com`

## Consumer endpoints

Standard operations for managing your own account. Requires `user:read` or `user:write` scope.

### GET /users/me
...
```
