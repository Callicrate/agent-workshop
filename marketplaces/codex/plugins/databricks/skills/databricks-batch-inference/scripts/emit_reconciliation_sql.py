"""Emit review-only SQL reconciliation checks for Databricks batch inference.

Structural inputs are parsed as Unity Catalog table or column identifiers. SQL
predicates and expressions are deliberately a separate, explicitly acknowledged
expert-only escape hatch: this utility cannot parse or sanitize arbitrary SQL.
It only prints SQL to stdout and never executes SQL or writes files.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass


DEFAULT_STALE_AFTER_SQL = "current_timestamp() - INTERVAL 1 DAY"
DEFAULT_SAMPLE_N = 20
MAX_SAMPLE_N = 1_000
MAX_LARGE_SAMPLE_N = 10_000
MAX_IDENTIFIER_CHARS = 773
MAX_IDENTIFIER_SEGMENT_CHARS = 255
MAX_UNSAFE_FRAGMENT_CHARS = 4_096
MAX_REPEATED_COLUMNS = 64
MAX_TOTAL_ARGUMENT_CHARS = 32_768
MAX_RENDERED_SQL_CHARS = 131_072
_BASE_RENDER_BUDGET = 24_000
_BARE_IDENTIFIER = re.compile(r"(?=.*[A-Za-z_])^[A-Za-z0-9_]+$")
_WINDOWS_DEVICE_OR_UNC = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2}(?:[.?][\\/])?)")


@dataclass(frozen=True)
class TableRole:
    """Expected source or target table role for an inventory check."""

    role: str
    table_name: str


class StoreWithPresence(argparse.Action):
    """Store a value and remember that the caller explicitly supplied it."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_supplied", True)


def sql_literal(value: str) -> str:
    """Return a single-quoted SQL literal with embedded quotes escaped."""
    return "'" + value.replace("'", "''") + "'"


def _identifier_error(message: str) -> argparse.ArgumentTypeError:
    return argparse.ArgumentTypeError(message)


def _reject_unsafe_identifier_text(value: str) -> None:
    """Reject text that cannot be an argument-controlled SQL identifier."""
    if not value or value != value.strip():
        raise _identifier_error(
            "identifiers must be non-empty and have no outer whitespace"
        )
    if len(value) > MAX_IDENTIFIER_CHARS:
        raise _identifier_error(
            f"identifiers cannot exceed {MAX_IDENTIFIER_CHARS} characters"
        )
    if any(
        unicodedata.category(character) == "Cc"
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise _identifier_error(
            "identifiers cannot contain control characters or newlines"
        )
    if ";" in value or "--" in value or "/*" in value or "*/" in value:
        raise _identifier_error("identifiers cannot contain SQL comments or semicolons")
    if value.startswith(("\\\\", "//")):
        raise _identifier_error("identifiers cannot use UNC or device-like paths")


def _reject_device_like_segment(segment: str) -> None:
    if _WINDOWS_DEVICE_OR_UNC.match(segment):
        raise _identifier_error("identifiers cannot use UNC or device-like paths")


def _parse_identifier_parts(value: str) -> list[str]:
    """Split a dotted identifier outside paired backticks and decode escapes."""
    _reject_unsafe_identifier_text(value)
    parts: list[str] = []
    index = 0
    length = len(value)

    while index < length:
        if value[index] == ".":
            raise _identifier_error("identifier segments cannot be empty")

        if value[index] == "`":
            index += 1
            characters: list[str] = []
            closed = False
            while index < length:
                character = value[index]
                if character == "`":
                    if index + 1 < length and value[index + 1] == "`":
                        characters.append("`")
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                characters.append(character)
                index += 1
            if not closed:
                raise _identifier_error(
                    "delimited identifiers must have paired backticks"
                )
            segment = "".join(characters)
            if not segment:
                raise _identifier_error("identifier segments cannot be empty")
            if index < length and value[index] != ".":
                raise _identifier_error(
                    "characters after a closing backtick must be a dot or the end"
                )
        else:
            start = index
            while index < length and value[index] != ".":
                character = value[index]
                if character == "`" or not (
                    character.isascii() and (character.isalnum() or character == "_")
                ):
                    raise _identifier_error(
                        "unquoted identifiers may contain only ASCII letters, digits, and underscores"
                    )
                index += 1
            segment = value[start:index]
            if not _BARE_IDENTIFIER.fullmatch(segment):
                raise _identifier_error(
                    "unquoted identifiers must contain at least one letter or underscore"
                )

        if len(segment) > MAX_IDENTIFIER_SEGMENT_CHARS:
            raise _identifier_error(
                f"identifier segments cannot exceed {MAX_IDENTIFIER_SEGMENT_CHARS} characters"
            )
        _reject_device_like_segment(segment)
        parts.append(segment)
        if index == length:
            break
        index += 1
        if index == length:
            raise _identifier_error("identifier segments cannot be empty")

    return parts


def _quote_identifier(parts: list[str]) -> str:
    return ".".join("`" + part.replace("`", "``") + "`" for part in parts)


def parse_table_identifier(value: str) -> str:
    """Parse and canonically quote a three-part Unity Catalog table identifier."""
    parts = _parse_identifier_parts(value)
    if len(parts) != 3:
        raise _identifier_error(
            "table identifiers must have exactly three catalog.schema.table segments"
        )
    return _quote_identifier(parts)


def parse_column_identifier(value: str) -> str:
    """Parse and canonically quote a single column identifier."""
    parts = _parse_identifier_parts(value)
    if len(parts) != 1:
        raise _identifier_error(
            "column identifiers must have exactly one segment, not a qualified name"
        )
    return _quote_identifier(parts)


def _split_outside_backticks(value: str, delimiter: str) -> list[str]:
    """Split text on a delimiter only while outside a paired backtick identifier."""
    pieces: list[str] = []
    start = 0
    index = 0
    quoted = False
    while index < len(value):
        if value[index] == "`":
            if quoted and index + 1 < len(value) and value[index + 1] == "`":
                index += 2
                continue
            quoted = not quoted
        elif value[index] == delimiter and not quoted:
            pieces.append(value[start:index])
            start = index + 1
        index += 1
    if quoted:
        raise _identifier_error("delimited identifiers must have paired backticks")
    pieces.append(value[start:])
    return pieces


def parse_column_csv(value: str) -> list[str]:
    """Parse a comma-separated list of structurally safe column identifiers."""
    if not value:
        raise _identifier_error("column lists cannot be empty")
    pieces = _split_outside_backticks(value, ",")
    if any(not piece for piece in pieces):
        raise _identifier_error("column lists cannot contain empty entries")
    if len(pieces) > MAX_REPEATED_COLUMNS:
        raise _identifier_error(
            f"column lists cannot exceed {MAX_REPEATED_COLUMNS} entries"
        )
    columns = [parse_column_identifier(piece) for piece in pieces]
    if len(columns) != len(set(columns)):
        raise _identifier_error("column lists cannot contain duplicate entries")
    return columns


def parse_unsafe_fragment(value: str) -> str:
    """Bound an acknowledged expert-authored SQL fragment before rendering."""
    if len(value) > MAX_UNSAFE_FRAGMENT_CHARS:
        raise argparse.ArgumentTypeError(
            f"unsafe SQL fragments cannot exceed {MAX_UNSAFE_FRAGMENT_CHARS} characters"
        )
    return value


def parse_table_role(value: str) -> TableRole:
    """Parse source=table or target=table values with a typed table identifier."""
    assignments = _split_outside_backticks(value, "=")
    if len(assignments) != 2:
        raise _identifier_error(
            "table roles must use source=catalog.schema.table or target=catalog.schema.table"
        )
    role, table_name = assignments
    if role not in {"source", "target"}:
        raise _identifier_error("table roles must be exactly source or target")
    return TableRole(role=role, table_name=parse_table_identifier(table_name))


def where_clause(predicate: str | None) -> str:
    """Return a WHERE clause for an acknowledged expert-authored SQL predicate."""
    if not predicate:
        return ""
    return f"\nWHERE {predicate}"


def and_predicate(prefix: str, predicates: list[str]) -> str:
    """Join already validated identifier expressions and acknowledged predicates."""
    if not predicates:
        return ""
    return f"\n{prefix} " + "\n    AND ".join(predicates)


def emit(lines: list[str], sql: str) -> None:
    """Append a SQL block to the output lines."""
    lines.append(sql.rstrip())
    lines.append("")


def emit_header(lines: list[str], args: argparse.Namespace) -> None:
    """Emit scope and unsafe-fragment warnings before executable-looking SQL."""
    emit(
        lines,
        """-- GENERATED RECONCILIATION SQL. Review against the scoring contract before execution.
-- Typed table and column arguments were validated and canonicalized as quoted identifiers.
-- This generator does not execute SQL and does not prove cross-table reconciliation, key cardinality, or MERGE safety.""",
    )
    if args.unsafe_fragments_used:
        emit(
            lines,
            """-- UNSAFE SQL FRAGMENTS: copied verbatim from trusted expert input after explicit acknowledgement.
-- Do not put secrets in unsafe SQL fragments. Human review is required before execution.
-- The generator does not parse, validate, or sanitize those fragments.""",
        )
    if args.legacy_fragments_used:
        emit(
            lines,
            """-- WARNING: a deprecated raw-SQL alias was used.
-- Replace --source_where, --target_where, or --stale_after_expr with the corresponding --unsafe-*-sql flag.""",
        )
    if args.no_model_version_check:
        emit(
            lines,
            """-- WARNING: --no-model-version-check was selected.
-- Model-version coverage and mixed-version diagnostics are intentionally omitted.""",
        )


def emit_basic_counts(
    lines: list[str], table: str, score_col: str, target_where: str | None
) -> None:
    """Emit before and after target-population count checks."""
    emit(
        lines,
        f"""-- Before count for the target population, run before the scoring job
SELECT
    COUNT(*) AS total_rows,
    COUNT({score_col}) AS already_scored_rows
FROM {table}{where_clause(target_where)};""",
    )
    emit(
        lines,
        f"""-- After count for the target population, run after the scoring job
SELECT
    COUNT(*) AS total_rows,
    COUNT({score_col}) AS scored_rows,
    COUNT(*) - COUNT({score_col}) AS null_score_count
FROM {table}{where_clause(target_where)};""",
    )


def emit_single_key_diagnostics(
    lines: list[str],
    table: str,
    key_col: str,
    label: str,
    predicate: str | None,
) -> None:
    """Emit null and duplicate diagnostics for one non-null equality key only."""
    scope_predicates = [f"{key_col} IS NULL"]
    if predicate:
        scope_predicates.append(f"({predicate})")
    emit(
        lines,
        f"""-- NULL-key count for the {label} population
SELECT
    COUNT(*) AS null_key_count
FROM {table}{and_predicate("WHERE", scope_predicates)};""",
    )
    duplicate_predicates = [f"{key_col} IS NOT NULL"]
    if predicate:
        duplicate_predicates.append(f"({predicate})")
    emit(
        lines,
        f"""-- Single non-NULL equality-key duplicate diagnostic for {label}.
-- It is not proof of a composite key, source-to-target cardinality, or general MERGE safety.
SELECT
    {key_col},
    COUNT(*) AS row_count
FROM {table}{and_predicate("WHERE", duplicate_predicates)}
GROUP BY {key_col}
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 100;""",
    )


def emit_null_source_check(
    lines: list[str],
    source_table: str,
    source_null_cols: list[str],
    source_where: str | None,
) -> None:
    """Emit source evidence missingness checks without claiming a target match."""
    if not source_null_cols:
        return
    null_predicate = " OR ".join(
        f"{column_name} IS NULL" for column_name in source_null_cols
    )
    predicates = [f"({null_predicate})"]
    if source_where:
        predicates.append(f"({source_where})")
    emit(
        lines,
        f"""-- Source rows with required evidence missing in the source population
SELECT
    COUNT(*) AS null_source_row_count
FROM {source_table}{and_predicate("WHERE", predicates)};""",
    )


def emit_target_outcome_check(
    lines: list[str],
    table: str,
    score_col: str,
    unscorable_reason_col: str | None,
    target_where: str | None,
) -> None:
    """Emit explicit unscorable and unexplained-null counts for target scope."""
    reason_expression = (
        f"NULLIF(TRIM(CAST({unscorable_reason_col} AS STRING)), '')"
        if unscorable_reason_col
        else "CAST(NULL AS STRING)"
    )
    scope = (
        "target population with the acknowledged target predicate"
        if target_where
        else "entire target population"
    )
    emit(
        lines,
        f"""-- Target outcome accounting. Without an unscorable reason column, every NULL score is unexpected.
SELECT
    {sql_literal(scope)} AS population_scope,
    COUNT(CASE WHEN {score_col} IS NULL AND {reason_expression} IS NOT NULL THEN 1 END)
        AS unscorable_count,
    COUNT(CASE WHEN {score_col} IS NULL AND {reason_expression} IS NULL THEN 1 END)
        AS unexpected_null_score_count
FROM {table}{where_clause(target_where)};""",
    )
    if not unscorable_reason_col:
        return
    predicates = [f"{score_col} IS NULL", f"{reason_expression} IS NOT NULL"]
    if target_where:
        predicates.append(f"({target_where})")
    emit(
        lines,
        f"""-- Recorded unscorable reasons in the target population
SELECT
    {reason_expression} AS unscorable_reason,
    COUNT(*) AS row_count
FROM {table}{and_predicate("WHERE", predicates)}
GROUP BY {reason_expression}
ORDER BY row_count DESC;""",
    )


def emit_staleness_check(
    lines: list[str],
    source_table: str,
    timestamp_col: str | None,
    stale_after_sql: str | None,
    source_where: str | None,
) -> None:
    """Emit source freshness status that distinguishes no rows from NULL timestamps."""
    if not timestamp_col:
        return
    stale_expression = stale_after_sql or DEFAULT_STALE_AFTER_SQL
    emit(
        lines,
        f"""-- Source freshness for the selected source population
WITH source_freshness AS (
    SELECT
        COUNT(*) AS source_row_count,
        COUNT({timestamp_col}) AS nonnull_timestamp_count,
        MAX({timestamp_col}) AS max_source_timestamp
    FROM {source_table}{where_clause(source_where)}
)
SELECT
    source_row_count,
    nonnull_timestamp_count,
    max_source_timestamp,
    CASE
        WHEN source_row_count = 0 THEN 'empty_source'
        WHEN nonnull_timestamp_count = 0 THEN 'missing_timestamps'
        WHEN max_source_timestamp < {stale_expression} THEN 'stale'
        ELSE 'fresh'
    END AS source_freshness_status
FROM source_freshness;""",
    )


def emit_model_version_check(
    lines: list[str],
    table: str,
    score_col: str,
    model_version_col: str,
    model_run_id_col: str | None,
    target_where: str | None,
) -> None:
    """Emit model metadata checks limited to rows that actually received a score."""
    run_id_expression = model_run_id_col or "CAST(NULL AS STRING)"
    scored_predicates = [f"{score_col} IS NOT NULL"]
    if target_where:
        scored_predicates.append(f"({target_where})")
    unresolved_predicates = [
        f"{score_col} IS NOT NULL",
        f"({model_version_col} IS NULL OR TRIM(CAST({model_version_col} AS STRING)) = '')",
    ]
    if target_where:
        unresolved_predicates.append(f"({target_where})")
    emit(
        lines,
        f"""-- Unresolved model versions among scored target rows only
SELECT
    COUNT(*) AS unresolved_model_version_count
FROM {table}{and_predicate("WHERE", unresolved_predicates)};""",
    )
    emit(
        lines,
        f"""-- Model versions present among scored target rows only
SELECT
    {model_version_col} AS model_version,
    {run_id_expression} AS model_run_id,
    COUNT(*) AS row_count
FROM {table}{and_predicate("WHERE", scored_predicates)}
GROUP BY {model_version_col}, {run_id_expression}
ORDER BY row_count DESC;""",
    )


def emit_distribution_check(
    lines: list[str],
    table: str,
    score_col: str,
    label_col: str | None,
    window_col: str | None,
    target_where: str | None,
) -> None:
    """Emit score distribution for the same scoped scored population as other checks."""
    group_cols = [column for column in [label_col, window_col] if column]
    select_group_cols = ",\n    ".join(group_cols)
    group_select = f"    {select_group_cols},\n" if select_group_cols else ""
    group_by = f"\nGROUP BY {', '.join(group_cols)}" if group_cols else ""
    order_by = f"\nORDER BY {', '.join(group_cols)}" if group_cols else ""
    scored_predicates = [f"{score_col} IS NOT NULL"]
    if target_where:
        scored_predicates.append(f"({target_where})")
    emit(
        lines,
        f"""-- Score distribution in the target population
SELECT
{group_select}    COUNT(*) AS row_count,
    AVG({score_col}) AS avg_score,
    MIN({score_col}) AS min_score,
    MAX({score_col}) AS max_score
FROM {table}{and_predicate("WHERE", scored_predicates)}{group_by}{order_by};""",
    )


def emit_sample_check(
    lines: list[str],
    table: str,
    key_col: str,
    sample_order_cols: list[str],
    sample_order_unique_attested: bool,
    score_col: str,
    sample_n: int,
    target_where: str | None,
) -> None:
    """Emit a key-ordered sample without inferring uniqueness from one key."""
    predicates = [f"{score_col} IS NOT NULL"]
    if target_where:
        predicates.append(f"({target_where})")
    order_columns = [key_col, *sample_order_cols]
    order_by = ",\n    ".join(f"{column} ASC NULLS LAST" for column in order_columns)
    if sample_order_unique_attested:
        comment = (
            "Total-order sample using the caller-attested unique key and tie-breaker tuple"
            if sample_order_cols
            else "Total-order sample using the caller-attested unique key"
        )
    else:
        comment = "Bounded key-ordered sample; nondeterministic among duplicate keys"
    emit(
        lines,
        f"""-- {comment}
SELECT
    {key_col},
    {score_col}
FROM {table}{and_predicate("WHERE", predicates)}
ORDER BY {order_by}
LIMIT {sample_n};""",
    )


def emit_table_role_inventory(lines: list[str], table_roles: list[TableRole]) -> None:
    """Emit a validated source/target Unity Catalog table inventory query."""
    if not table_roles:
        return
    values: list[str] = []
    catalogs: set[str] = set()
    for role in table_roles:
        catalog_name, schema_name, table_name = _parse_identifier_parts(role.table_name)
        catalogs.add(_quote_identifier([catalog_name]))
        values.append(
            "    ("
            + ", ".join(
                [
                    sql_literal(role.role),
                    sql_literal(catalog_name),
                    sql_literal(schema_name),
                    sql_literal(table_name),
                    sql_literal(role.table_name),
                ]
            )
            + ")"
        )
    union_queries = "\nUNION ALL\n".join(
        f"""    SELECT
        table_catalog,
        table_schema,
        table_name
    FROM {catalog}.`information_schema`.`tables`"""
        for catalog in sorted(catalogs)
    )
    expected_values = ",\n".join(values)
    emit(
        lines,
        f"""-- Expected versus actual validated source and target table roles
WITH expected_tables AS (
    SELECT *
    FROM VALUES
{expected_values}
    AS t(table_role, table_catalog, table_schema, table_name, fully_qualified_name)
),
existing_tables AS (
{union_queries}
)
SELECT
    e.table_role,
    e.fully_qualified_name,
    CASE WHEN x.table_name IS NULL THEN FALSE ELSE TRUE END AS table_exists
FROM expected_tables AS e
LEFT JOIN existing_tables AS x
    ON e.table_catalog = x.table_catalog
    AND e.table_schema = x.table_schema
    AND e.table_name = x.table_name
ORDER BY e.table_role;""",
    )


def emit_reconciliation_sql(args: argparse.Namespace) -> str:
    """Build reconciliation SQL from already validated arguments."""
    lines: list[str] = []
    source_table = args.source_table
    source_key_col = args.source_key_col or args.key_col
    target_key_col = args.target_key_col or args.key_col

    emit_header(lines, args)
    emit_basic_counts(lines, args.table, args.score_col, args.unsafe_target_where_sql)
    emit_single_key_diagnostics(
        lines, args.table, target_key_col, "target", args.unsafe_target_where_sql
    )
    if source_table:
        emit_single_key_diagnostics(
            lines, source_table, source_key_col, "source", args.unsafe_source_where_sql
        )
        emit_null_source_check(
            lines,
            source_table,
            args.source_null_cols or [],
            args.unsafe_source_where_sql,
        )
        emit_staleness_check(
            lines,
            source_table,
            args.source_updated_at_col,
            args.unsafe_stale_after_sql,
            args.unsafe_source_where_sql,
        )
    emit_target_outcome_check(
        lines,
        args.table,
        args.score_col,
        args.unscorable_reason_col,
        args.unsafe_target_where_sql,
    )
    if not args.no_model_version_check:
        emit_model_version_check(
            lines,
            args.table,
            args.score_col,
            args.model_version_col,
            args.model_run_id_col,
            args.unsafe_target_where_sql,
        )
    emit_distribution_check(
        lines,
        args.table,
        args.score_col,
        args.label_col,
        args.window_col,
        args.unsafe_target_where_sql,
    )
    emit_sample_check(
        lines,
        args.table,
        target_key_col,
        args.sample_order_col or [],
        args.attest_sample_order_unique,
        args.score_col,
        args.sample_n,
        args.unsafe_target_where_sql,
    )
    emit_table_role_inventory(lines, args.table_role or [])
    rendered = "\n".join(lines).rstrip() + "\n"
    if len(rendered) > MAX_RENDERED_SQL_CHARS:
        raise ValueError("rendered SQL exceeded the validated output bound")
    return rendered


def _resolve_fragment(
    parser: argparse.ArgumentParser,
    modern_value: str | None,
    legacy_value: str | None,
    modern_flag: str,
    legacy_flag: str,
) -> str | None:
    if modern_value is not None and legacy_value is not None:
        parser.error(f"use only {modern_flag}; {legacy_flag} is deprecated")
    if legacy_value is not None:
        return legacy_value
    return modern_value


def _source_flags_present(args: argparse.Namespace) -> bool:
    return any(
        value is not None and value != []
        for value in [
            args.source_key_col,
            args.source_null_cols,
            args.source_updated_at_col,
            args.unsafe_source_where_sql,
            args.unsafe_stale_after_sql,
        ]
    )


def _argument_character_count(args: argparse.Namespace) -> int:
    """Count bounded caller-controlled text after parsing and alias resolution."""
    total = 0
    for value in vars(args).values():
        if isinstance(value, str):
            total += len(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    total += len(item)
                elif isinstance(item, TableRole):
                    total += len(item.role) + len(item.table_name)
    return total


def _validate_render_budget(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject oversized requests before constructing any SQL blocks."""
    argument_chars = _argument_character_count(args)
    if argument_chars > MAX_TOTAL_ARGUMENT_CHARS:
        parser.error(
            f"total argument text cannot exceed {MAX_TOTAL_ARGUMENT_CHARS} characters"
        )
    repeated_columns = len(args.sample_order_col or []) + len(
        args.source_null_cols or []
    )
    role_count = len(args.table_role or [])
    estimated_output = (
        _BASE_RENDER_BUDGET
        + (argument_chars * 8)
        + (repeated_columns * 256)
        + (role_count * 2_048)
    )
    if estimated_output > MAX_RENDERED_SQL_CHARS:
        parser.error(
            f"request can render more than {MAX_RENDERED_SQL_CHARS} SQL characters"
        )


def validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> argparse.Namespace:
    """Resolve deprecated fragments and reject invalid combinations with exit code 2."""
    args.legacy_fragments_used = any(
        value is not None
        for value in [
            args.legacy_source_where,
            args.legacy_target_where,
            args.legacy_stale_after_expr,
        ]
    )
    args.unsafe_source_where_sql = _resolve_fragment(
        parser,
        args.unsafe_source_where_sql,
        args.legacy_source_where,
        "--unsafe-source-where-sql",
        "--source_where",
    )
    args.unsafe_target_where_sql = _resolve_fragment(
        parser,
        args.unsafe_target_where_sql,
        args.legacy_target_where,
        "--unsafe-target-where-sql",
        "--target_where",
    )
    args.unsafe_stale_after_sql = _resolve_fragment(
        parser,
        args.unsafe_stale_after_sql,
        args.legacy_stale_after_expr,
        "--unsafe-stale-after-sql",
        "--stale_after_expr",
    )
    args.unsafe_fragments_used = any(
        value is not None
        for value in [
            args.unsafe_source_where_sql,
            args.unsafe_target_where_sql,
            args.unsafe_stale_after_sql,
        ]
    )
    if any(
        value == ""
        for value in [
            args.unsafe_source_where_sql,
            args.unsafe_target_where_sql,
            args.unsafe_stale_after_sql,
        ]
    ):
        parser.error("unsafe SQL fragments cannot be empty")
    if args.unsafe_fragments_used and not args.allow_unsafe_sql_fragments:
        parser.error("unsafe SQL fragments require --allow-unsafe-sql-fragments")
    if _source_flags_present(args) and not args.source_table:
        parser.error("source-specific flags require --source_table")
    if args.unsafe_stale_after_sql is not None and not args.source_updated_at_col:
        parser.error("--unsafe-stale-after-sql requires --source_updated_at_col")
    if args.sample_n <= 0:
        parser.error("--sample_n must be a positive integer")
    if args.sample_n > MAX_LARGE_SAMPLE_N:
        parser.error(f"--sample_n cannot exceed {MAX_LARGE_SAMPLE_N}")
    if args.sample_n > MAX_SAMPLE_N and not args.allow_large_sample:
        parser.error(f"--sample_n above {MAX_SAMPLE_N} requires --allow-large-sample")
    if args.sample_order_col and not args.attest_sample_order_unique:
        parser.error("--sample_order_col requires --attest-sample-order-unique")
    if len(args.sample_order_col or []) > MAX_REPEATED_COLUMNS:
        parser.error(
            f"--sample_order_col cannot be repeated more than {MAX_REPEATED_COLUMNS} times"
        )
    if len(args.sample_order_col or []) != len(set(args.sample_order_col or [])):
        parser.error("--sample_order_col cannot contain duplicate columns")
    if args.no_model_version_check and getattr(
        args, "model_run_id_col_supplied", False
    ):
        parser.error(
            "--model_run_id_col cannot be supplied with --no-model-version-check"
        )

    seen_roles: set[str] = set()
    for table_role in args.table_role or []:
        if table_role.role in seen_roles:
            parser.error(f"duplicate table role: {table_role.role}")
        seen_roles.add(table_role.role)
        if table_role.role == "target" and table_role.table_name != args.table:
            parser.error("target table role must match --table")
        if table_role.role == "source":
            if not args.source_table:
                parser.error("source table role requires --source_table")
            if table_role.table_name != args.source_table:
                parser.error("source table role must match --source_table")
    _validate_render_budget(parser, args)
    return args


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for typed structural inputs and explicit raw SQL."""
    parser = argparse.ArgumentParser(
        description="Emit review-only reconciliation SQL for Databricks batch inference"
    )
    parser.add_argument(
        "--table",
        required=True,
        type=parse_table_identifier,
        help="Target table: catalog.schema.table",
    )
    parser.add_argument(
        "--source_table",
        type=parse_table_identifier,
        help="Source table: catalog.schema.table",
    )
    parser.add_argument(
        "--key_col",
        required=True,
        type=parse_column_identifier,
        help="Default single equality key column",
    )
    parser.add_argument(
        "--source_key_col",
        type=parse_column_identifier,
        help="Source single equality key column",
    )
    parser.add_argument(
        "--target_key_col",
        type=parse_column_identifier,
        help="Target single equality key column",
    )
    parser.add_argument(
        "--score_col",
        required=True,
        type=parse_column_identifier,
        help="Score or prediction score column",
    )
    parser.add_argument(
        "--label_col", type=parse_column_identifier, help="Predicted label column"
    )
    parser.add_argument(
        "--window_col",
        type=parse_column_identifier,
        help="Target window or partition column",
    )
    parser.add_argument(
        "--sample_n",
        type=int,
        default=DEFAULT_SAMPLE_N,
        help=f"Positive bounded sample size, at most {MAX_SAMPLE_N} unless explicitly overridden",
    )
    parser.add_argument(
        "--allow-large-sample",
        action="store_true",
        help=f"Acknowledge a sample above {MAX_SAMPLE_N}, still capped at {MAX_LARGE_SAMPLE_N}",
    )
    parser.add_argument(
        "--sample_order_col",
        action="append",
        type=parse_column_identifier,
        help="Repeatable sample tie-breaker column; requires --attest-sample-order-unique",
    )
    parser.add_argument(
        "--attest-sample-order-unique",
        action="store_true",
        help="Attest that the key plus optional sample-order columns form a unique total-order tuple",
    )
    parser.add_argument(
        "--unsafe-source-where-sql",
        type=parse_unsafe_fragment,
        help="Trusted expert SQL predicate for source checks, copied verbatim only with acknowledgement",
    )
    parser.add_argument(
        "--unsafe-target-where-sql",
        type=parse_unsafe_fragment,
        help="Trusted expert SQL predicate for target checks, copied verbatim only with acknowledgement",
    )
    parser.add_argument(
        "--unsafe-stale-after-sql",
        type=parse_unsafe_fragment,
        help="Trusted expert SQL timestamp expression, copied verbatim only with acknowledgement",
    )
    parser.add_argument(
        "--allow-unsafe-sql-fragments",
        action="store_true",
        help="Required acknowledgement for every --unsafe-*-sql argument",
    )
    parser.add_argument(
        "--source_where",
        dest="legacy_source_where",
        type=parse_unsafe_fragment,
        help="DEPRECATED: use --unsafe-source-where-sql",
    )
    parser.add_argument(
        "--target_where",
        dest="legacy_target_where",
        type=parse_unsafe_fragment,
        help="DEPRECATED: use --unsafe-target-where-sql",
    )
    parser.add_argument(
        "--stale_after_expr",
        dest="legacy_stale_after_expr",
        type=parse_unsafe_fragment,
        help="DEPRECATED: use --unsafe-stale-after-sql",
    )
    parser.add_argument(
        "--source_null_cols",
        type=parse_column_csv,
        help="Comma-separated source evidence columns; each is a single typed identifier",
    )
    parser.add_argument(
        "--unscorable_reason_col",
        type=parse_column_identifier,
        help="Target unscorable-reason column",
    )
    parser.add_argument(
        "--source_updated_at_col",
        type=parse_column_identifier,
        help="Source update timestamp column",
    )
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument(
        "--model_version_col",
        type=parse_column_identifier,
        default=parse_column_identifier("model_version"),
    )
    model_group.add_argument(
        "--no-model-version-check",
        action="store_true",
        help="Intentionally omit model-version diagnostics and emit a warning",
    )
    parser.add_argument(
        "--model_run_id_col",
        action=StoreWithPresence,
        type=parse_column_identifier,
        default=parse_column_identifier("model_run_id"),
    )
    parser.add_argument(
        "--table_role",
        action="append",
        type=parse_table_role,
        help="Validated role mapping: source=catalog.schema.table or target=catalog.schema.table",
    )
    return parser


def main() -> int:
    """Print generated SQL and return a conventional success code."""
    parser = build_parser()
    args = validate_args(parser, parser.parse_args())
    print(emit_reconciliation_sql(args), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
