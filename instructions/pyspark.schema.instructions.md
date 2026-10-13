---
description: "PySpark schema standards for Spark-specific schema modules"
applyTo: '**/pyspark/**/*schema*.py,**/spark/**/*schema*.py,**/*pyspark*schema*.py,**/*spark*schema*.py'
---

# PySpark Schema Standards

## Schema Organization

### File Structure

Organize schemas hierarchically with reusable components:

```python
"""
Schema definitions for [domain] data.

Schemas:
    field_schema: Core fields without metadata
    source_schema: Complete schema with tracking fields
"""

from pyspark.sql import types as T
```

### Nested Schema Components

Define reusable nested schemas as module-level constants:

```python
# Reusable address schema
address_schema = T.StructType([
    T.StructField("address_raw", T.StringType(), True),
    T.StructField("address_domain", T.StringType(), True),
    T.StructField("address_local", T.StringType(), True),
])

# Schema using the nested component
message_schema = T.StructType([
    T.StructField("message_id", T.StringType(), True),
    T.StructField("sender", address_schema, True),
    T.StructField("recipients", T.ArrayType(address_schema), True),
])
```

---

## Type Selection

### Temporal Fields

Use proper temporal types:

```python
# ✅ CORRECT
T.StructField("created_at", T.TimestampType(), True),
T.StructField("event_date", T.DateType(), True),

# ❌ WRONG - epochs as integers
T.StructField("created_at", T.LongType(), True),  # milliseconds
```

### Numeric Precision

| Data | Type | Example |
|------|------|---------|
| Counts | `ShortType`, `IntegerType` | `attachments_count` |
| Large counts/IDs | `LongType` | `file_size`, `asn` |
| Scores/percentages | `DoubleType` | `spam_score` |
| Precise decimals | `DecimalType(p, s)` | Currency |

### Boolean Fields

Use `BooleanType` for flags, not strings or integers:

```python
T.StructField("is_active", T.BooleanType(), True),
T.StructField("has_attachments", T.BooleanType(), True),
```

---

## Nullable Fields

Mark all fields as nullable (`True`) unless business logic requires non-null:

```python
# All fields nullable by default for source data
T.StructField("optional_field", T.StringType(), True),
```

---

## Array and Struct Types

### Arrays

```python
T.StructField("keywords", T.ArrayType(T.StringType()), True),
T.StructField("attachments", T.ArrayType(attachment_schema), True),
```

### Nested Structs

```python
location_schema = T.StructType([
    T.StructField("lat", T.DoubleType(), True),
    T.StructField("lon", T.DoubleType(), True),
])

geoip_schema = T.StructType([
    T.StructField("city", T.StringType(), True),
    T.StructField("country", T.StringType(), True),
    T.StructField("location", location_schema, True),
])
```

---

## Schema Composition

### Base Schema with Extensions

```python
# Core business fields
core_fields = T.StructType([
    T.StructField("id", T.StringType(), True),
    T.StructField("data", T.StringType(), True),
])

# Extended schema with SCD2 tracking
source_schema = T.StructType(
    core_fields.fields + [
        T.StructField("valid_from", T.TimestampType(), True),
        T.StructField("valid_to", T.TimestampType(), True),
        T.StructField("is_current", T.BooleanType(), True),
        T.StructField("hash", T.StringType(), True),
    ]
)
```

---

## Documentation

### Schema Docstrings

Document the purpose and key fields:

```python
"""
Schema for email attachment metadata.

Fields:
    file_size: Size in bytes
    md5: MD5 hash of file content
    sha256: SHA-256 hash of file content
    mime_type: MIME type string
"""
attachment_schema = T.StructType([...])
```

### Field Comments

Use inline comments for non-obvious fields:

```python
T.StructField("date", T.DateType(), True),  # Email header date (sender-controlled)
T.StructField("add_timestamp", T.TimestampType(), True),  # System ingestion time
```

---

## Schema Evolution

### Adding Columns

New columns should always be nullable to maintain backwards compatibility with existing data:

```python
# Safe - new nullable column with default None in existing rows
updated_schema = T.StructType(
    existing_schema.fields + [
        T.StructField("new_metric", T.DoubleType(), True),
    ]
)
```

### Removing Columns

Never drop columns from a schema used by downstream consumers. Instead, deprecate by convention and stop populating:

```python
# Deprecate a field - stop writing, keep in schema for readers
T.StructField("legacy_score", T.DoubleType(), True),  # DEPRECATED: use new_score
```

Remove the field from the schema only after confirming no downstream jobs read it.

### Renaming Columns

Renaming is a breaking change. Prefer add-then-deprecate:

```python
# Step 1: Add new column, populate both
df = df.withColumn("user_email", F.col("email"))

# Step 2: After all consumers migrate, deprecate the old column
```

### Type Changes

Widening (e.g., `IntegerType` to `LongType`) is generally safe. Narrowing or changing type families (e.g., `StringType` to `IntegerType`) is breaking. Always widen rather than narrow.

---

## Backwards Compatibility

### mergeSchema vs overwriteSchema

Use `mergeSchema` when appending data with new columns to an existing Delta table. Existing columns are preserved, new columns get null for historical rows:

```python
# Safe additive evolution - new columns merge in
df.write.format("delta").option("mergeSchema", "true").mode("append").save(path)
```

Use `overwriteSchema` only when intentionally replacing the entire schema (e.g., a full reprocessing backfill). This drops columns not present in the new data:

```python
# Destructive - replaces schema entirely, use only for full rewrites
df.write.format("delta").option("overwriteSchema", "true").mode("overwrite").save(path)
```

**Rule of thumb:** If downstream jobs read this table, use `mergeSchema`. Use `overwriteSchema` only when you control all readers and are doing a coordinated migration.

---

## Schema Validation

### Asserting Expected Schema

Validate incoming data against the expected schema before processing to catch upstream changes early:

```python
def validate_schema(df: DataFrame, expected: T.StructType) -> None:
    """Assert that df matches expected schema. Raises on mismatch."""
    missing = set(expected.fieldNames()) - set(df.schema.fieldNames())
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    for expected_field in expected.fields:
        actual_field = df.schema[expected_field.name]
        if actual_field.dataType != expected_field.dataType:
            raise TypeError(
                f"Column '{expected_field.name}' type mismatch: "
                f"expected {expected_field.dataType}, got {actual_field.dataType}"
            )
```

### Fail-Fast Pattern

Place schema validation at the top of transformation functions so jobs fail immediately on unexpected input rather than producing corrupt output:

```python
def transform_events(df: DataFrame) -> DataFrame:
    validate_schema(df, expected_event_schema)
    # Safe to proceed - schema matches expectations
    return df.select(...)
```
