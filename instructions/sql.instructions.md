---
description: "Dialect-neutral SQL standards for readable, deterministic, and safe queries"
applyTo: '**/*.sql'
---

# SQL Development Standards

## Dialect and Ownership

- Confirm the target database and SQL dialect before using engine-specific syntax.
- Preserve existing project naming and migration conventions when they are documented.
- Keep schema changes in the repository's migration or DDL surface rather than embedding them in unrelated application logic.

## Naming

- Use descriptive `snake_case` names unless the target database or project requires another convention.
- Use `*_date` for dates, `*_at` or `*_timestamp` for timestamps, `is_*` or `has_*` for booleans, and `*_id` for identifiers.
- Qualify ambiguous columns with table aliases.
- Prefer meaningful aliases such as `orders` or `customer_totals` over `a`, `b`, or `t1` in non-trivial queries.

## Formatting

- Use consistent keyword casing and indentation within a project.
- Place major clauses on separate lines.
- Select only required columns in production queries. Use `SELECT *` only for explicit exploration or an interface that intentionally returns every column.
- Put comments above the logic they explain.

```sql
SELECT
    orders.order_id,
    orders.created_at,
    customers.customer_name
FROM orders
INNER JOIN customers
    ON customers.customer_id = orders.customer_id
WHERE orders.status = 'active'
ORDER BY orders.created_at DESC;
```

## Correctness

- Make row cardinality explicit before adding joins. Verify whether each join is one-to-one, one-to-many, or many-to-many.
- Use an explicit `ORDER BY` whenever row order affects output, pagination, ranking, or deterministic tests.
- Handle `NULL` intentionally with `IS NULL`, `IS NOT NULL`, `COALESCE`, or dialect-equivalent functions.
- Do not encode missing values as magic strings or numbers.
- Use parameter binding for application inputs. Do not build SQL by concatenating untrusted values.

## Data Changes

- Scope `UPDATE` and `DELETE` statements with a reviewed predicate.
- Preview affected rows with the same predicate before a high-impact data change when the environment supports it.
- Use transactions for related changes when the target engine supports them.
- Make migrations idempotent only when the migration framework and target engine expect that behavior.
- Avoid engine-specific DDL such as storage formats, clustering, table properties, or complex types unless matching scoped guidance or project docs require it.

## Common Table Expressions

Use CTEs to separate logical steps in complex queries.

```sql
WITH active_orders AS (
    SELECT
        order_id,
        customer_id,
        order_total
    FROM orders
    WHERE status = 'active'
),

customer_totals AS (
    SELECT
        customer_id,
        SUM(order_total) AS active_order_total
    FROM active_orders
    GROUP BY customer_id
)

SELECT
    customers.customer_id,
    customers.customer_name,
    customer_totals.active_order_total
FROM customer_totals
INNER JOIN customers
    ON customers.customer_id = customer_totals.customer_id;
```

- Give each CTE one clear purpose and a descriptive name.
- Put the final query after all CTE definitions.
- CTE optimization behavior varies by database. Inspect the execution plan before relying on materialization or reuse.

## Window Functions

Use window functions for row-relative calculations and make their ordering deterministic.

```sql
WITH ranked_orders AS (
    SELECT
        order_id,
        customer_id,
        created_at,
        ROW_NUMBER() OVER (
            PARTITION BY customer_id
            ORDER BY created_at DESC, order_id DESC
        ) AS recency_rank
    FROM orders
)

SELECT
    order_id,
    customer_id,
    created_at
FROM ranked_orders
WHERE recency_rank = 1;
```

- Use `ROW_NUMBER()` when exactly one row must win.
- Use `RANK()` or `DENSE_RANK()` when ties are meaningful.
- Include a stable tiebreaker in window ordering when values can be equal.

## Validation

- Parse or lint SQL with the project's configured tool when available.
- Run the narrowest safe query, migration check, or explain plan that verifies the change.
- Confirm expected row counts, uniqueness, and `NULL` behavior at important boundaries.
