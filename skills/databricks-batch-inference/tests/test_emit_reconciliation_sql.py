"""Golden and safety tests for the review-only reconciliation SQL generator."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import random
import re
import sqlite3
import subprocess
import sys
import unittest
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "emit_reconciliation_sql.py"
DECISION_SCRIPT = SKILL_ROOT / "scripts" / "derive_publication_decision.py"
BUSINESS_KEY_SCRIPT = SKILL_ROOT / "scripts" / "business_key_contract.py"
GOLDEN = SKILL_ROOT / "tests" / "fixtures" / "full-branches.sql"
DECISION_CASES = SKILL_ROOT / "tests" / "fixtures" / "publication-decision-cases.json"
LABEL_GATE = SKILL_ROOT / "references" / "label-share-anomaly-gating.md"
MODEL_LOADING = SKILL_ROOT / "references" / "uc-model-loading.md"
MODEL_SIGNATURE = SKILL_ROOT / "references" / "model-signature-contract.md"
CORE_PATTERNS = SKILL_ROOT / "references" / "core-batch-inference-patterns.md"
SAFE_WRITES = SKILL_ROOT / "references" / "safe-write-patterns.md"
SCORING_DDL = SKILL_ROOT / "assets" / "scoring-table-ddl.sql"
AUDIT_DDL = SKILL_ROOT / "assets" / "scoring-run-audit-ddl.sql"
QUARANTINE_DDL = SKILL_ROOT / "assets" / "scoring-quarantine-table-ddl.sql"
VALID_IDENTITY = "a" * 64
VALID_UNKNOWN_SET_DIGEST = "c" * 64

SPEC = importlib.util.spec_from_file_location("emit_reconciliation_sql", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

DECISION_SPEC = importlib.util.spec_from_file_location(
    "derive_publication_decision", DECISION_SCRIPT
)
assert DECISION_SPEC is not None and DECISION_SPEC.loader is not None
DECISION_MODULE = importlib.util.module_from_spec(DECISION_SPEC)
sys.modules[DECISION_SPEC.name] = DECISION_MODULE
DECISION_SPEC.loader.exec_module(DECISION_MODULE)

BUSINESS_KEY_SPEC = importlib.util.spec_from_file_location(
    "business_key_contract", BUSINESS_KEY_SCRIPT
)
assert BUSINESS_KEY_SPEC is not None and BUSINESS_KEY_SPEC.loader is not None
BUSINESS_KEY_MODULE = importlib.util.module_from_spec(BUSINESS_KEY_SPEC)
sys.modules[BUSINESS_KEY_SPEC.name] = BUSINESS_KEY_MODULE
BUSINESS_KEY_SPEC.loader.exec_module(BUSINESS_KEY_MODULE)


def parse_args(arguments: list[str]):
    """Parse generator arguments through the same validation path as the CLI."""
    parser = MODULE.build_parser()
    return MODULE.validate_args(parser, parser.parse_args(arguments))


def generate(arguments: list[str]) -> str:
    """Generate SQL directly, avoiding shell quoting differences in golden tests."""
    return MODULE.emit_reconciliation_sql(parse_args(arguments))


def base_arguments() -> list[str]:
    """Return the minimal safe invocation arguments."""
    return [
        "--table",
        "catalog.schema.predictions",
        "--key_col",
        "message_id",
        "--score_col",
        "spam_score",
    ]


def cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the public CLI with captured text output."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=SKILL_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def decision_cli(
    expected_run_id: str, expected_digest: str, rows: object
) -> subprocess.CompletedProcess[str]:
    """Run the publication-decision CLI with captured output."""
    return subprocess.run(
        [
            sys.executable,
            str(DECISION_SCRIPT),
            "--expected-scoring-run-id",
            expected_run_id,
            "--expected-run-contract-digest",
            expected_digest,
            "--gate-results-json",
            json.dumps(rows),
        ],
        cwd=SKILL_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def query_decision_cli(rows: object) -> subprocess.CompletedProcess[str]:
    """Run the diagnostic-rich label-gate query projection CLI path."""
    return subprocess.run(
        [
            sys.executable,
            str(DECISION_SCRIPT),
            "--expected-scoring-run-id",
            VALID_IDENTITY,
            "--expected-run-contract-digest",
            VALID_IDENTITY,
            "--label-gate-query-results-json",
            json.dumps(rows),
        ],
        cwd=SKILL_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def raw_decision_cli(flag: str, payload: str) -> subprocess.CompletedProcess[str]:
    """Run either public JSON flag with an already serialized payload."""
    return subprocess.run(
        [
            sys.executable,
            str(DECISION_SCRIPT),
            "--expected-scoring-run-id",
            VALID_IDENTITY,
            "--expected-run-contract-digest",
            VALID_IDENTITY,
            flag,
            payload,
        ],
        cwd=SKILL_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def label_gate_query_row(
    publish_gate: str = "ok", **overrides: object
) -> dict[str, object]:
    """Return one row in the closed diagnostic-rich label-gate SQL schema."""
    row: dict[str, object] = {
        "gate_scoring_run_id": VALID_IDENTITY,
        "gate_run_contract_digest": VALID_IDENTITY,
        "label_name": "ham",
        "is_contract_gate_row": False,
        "bucket_rows": 100,
        "label_rows": 90,
        "label_share": 0.9,
        "mean_share": 0.88,
        "std_share": 0.02,
        "expected_label_count": 2,
        "expected_row_count": 100,
        "staged_row_count": 100,
        "scoreable_row_count": 100,
        "unscorable_row_count": 0,
        "unknown_label_count": 0,
        "unknown_label_set_digest": VALID_UNKNOWN_SET_DIGEST,
        "unexpected_null_count": 0,
        "share_z_score": 1.0,
        "publish_gate": publish_gate,
    }
    row.update(overrides)
    return row


def file_hashes(root: Path) -> dict[str, str]:
    """Return stable hashes for files under the owned skill."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def markdown_literal_assignment(path: Path, assignment_name: str):
    """Read one literal assignment from a fenced Python example."""
    for block in re.findall(
        r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL
    ):
        tree = ast.parse(block)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == assignment_name
                for target in node.targets
            ):
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing literal assignment: {assignment_name}")


def ddl_columns(path: Path) -> tuple[list[str], set[str]]:
    """Return ordered top-level columns and declared NOT NULL columns from starter DDL."""
    columns: list[str] = []
    required: set[str] = set()
    pattern = re.compile(
        r"^    ([a-z][a-z0-9_]*) (?:STRING|BIGINT|TIMESTAMP|DOUBLE|ARRAY<)",
        re.IGNORECASE,
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        column = match.group(1)
        columns.append(column)
        if " NOT NULL" in line.upper():
            required.add(column)
    return columns, required


def cte_body(sql: str, name: str) -> str:
    """Extract one CTE body while respecting nested parentheses and SQL strings."""
    marker = f"{name} AS ("
    start = sql.index(marker) + len(marker)
    depth = 1
    quoted = False
    index = start
    while index < len(sql):
        character = sql[index]
        if character == "'":
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
        elif not quoted and character == ")":
            depth -= 1
            if depth == 0:
                return sql[start:index]
        index += 1
    raise AssertionError(f"unterminated CTE: {name}")


class ReconciliationSqlTests(unittest.TestCase):
    """Exercise exact output and conservative rejection behavior."""

    def test_full_branches_match_golden(self) -> None:
        arguments = [
            "--table",
            "`pred``ictions`.`prod schema`.`scores`",
            "--source_table",
            "`source_cat`.`raw`.`messages`",
            "--key_col",
            "`message id`",
            "--score_col",
            "score",
            "--label_col",
            "label",
            "--window_col",
            "event_date",
            "--source_key_col",
            "source_id",
            "--target_key_col",
            "`target id`",
            "--source_null_cols",
            "`subject`,`body text`",
            "--source_updated_at_col",
            "ingested_at",
            "--unscorable_reason_col",
            "unscorable_reason",
            "--unsafe-source-where-sql",
            "`ingested_at` >= TIMESTAMP '2026-08-28 00:00:00'",
            "--unsafe-target-where-sql",
            "`event_date` = DATE '2026-08-29'",
            "--unsafe-stale-after-sql",
            "current_timestamp() - INTERVAL 2 DAYS",
            "--allow-unsafe-sql-fragments",
            "--sample_n",
            "25",
            "--table_role",
            "source=`source_cat`.`raw`.`messages`",
            "--table_role",
            "target=`pred``ictions`.`prod schema`.`scores`",
        ]
        self.assertEqual(GOLDEN.read_text(encoding="utf-8"), generate(arguments))

    def test_backtick_identifiers_and_embedded_quotes_are_canonicalized(self) -> None:
        output = generate(
            [
                "--table",
                "`cat.alog`.`schema name`.`table``name`",
                "--key_col",
                "`event.id`",
                "--score_col",
                "`score value`",
            ]
        )
        self.assertIn("FROM `cat.alog`.`schema name`.`table``name`", output)
        self.assertIn("`event.id`", output)
        self.assertIn("`score value`", output)

    def test_all_structural_injection_shapes_exit_two(self) -> None:
        invalid_invocations = [
            base_arguments()[:-6]
            + ["--table", "catalog.schema.predictions; DROP TABLE x"]
            + base_arguments()[-4:],
            [
                "--table",
                "catalog.schema.predictions -- comment",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
            ],
            [
                "--table",
                "catalog.schema",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
            ],
            [
                "--table",
                "catalog.schema.predictions.extra",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
            ],
            [
                "--table",
                "catalog.schema.`C:\\device`",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "source.message_id",
                "--score_col",
                "score",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score; DELETE",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
                "--label_col",
                "label name",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
                "--source_table",
                "catalog.schema.source;DELETE",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
                "--source_null_cols",
                "subject,,body",
                "--source_table",
                "catalog.schema.source",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
                "--table_role",
                "owner=catalog.schema.predictions",
            ],
            [
                "--table",
                "catalog.schema.predictions",
                "--key_col",
                "message_id",
                "--score_col",
                "score",
                "--table_role",
                "target=catalog.schema.predictions;DELETE",
            ],
        ]
        for arguments in invalid_invocations:
            with self.subTest(arguments=arguments):
                result = cli(arguments)
                self.assertEqual(2, result.returncode, result.stderr)

    def test_every_structural_argument_rejects_control_and_injection_text(self) -> None:
        invalid_value = "name; DROP TABLE x"
        cases = [
            (base_arguments(), "--table", invalid_value),
            (
                [*base_arguments(), "--source_table", "catalog.schema.source"],
                "--source_table",
                invalid_value,
            ),
            (base_arguments(), "--key_col", invalid_value),
            (
                [*base_arguments(), "--source_table", "catalog.schema.source"],
                "--source_key_col",
                invalid_value,
            ),
            (base_arguments(), "--target_key_col", invalid_value),
            (base_arguments(), "--score_col", invalid_value),
            (base_arguments(), "--label_col", invalid_value),
            (base_arguments(), "--window_col", invalid_value),
            (
                [*base_arguments(), "--attest-sample-order-unique"],
                "--sample_order_col",
                invalid_value,
            ),
            (
                [*base_arguments(), "--source_table", "catalog.schema.source"],
                "--source_null_cols",
                invalid_value,
            ),
            (base_arguments(), "--unscorable_reason_col", invalid_value),
            (
                [*base_arguments(), "--source_table", "catalog.schema.source"],
                "--source_updated_at_col",
                invalid_value,
            ),
            (base_arguments(), "--model_version_col", invalid_value),
            (base_arguments(), "--model_run_id_col", invalid_value),
        ]
        for base, flag, value in cases:
            arguments = [*base]
            index = arguments.index(flag) if flag in arguments else None
            if index is None:
                arguments.extend([flag, value])
            else:
                arguments[index + 1] = value
            with self.subTest(flag=flag):
                self.assertEqual(2, cli(arguments).returncode)

        for value in [
            "catalog.schema.bad\nname",
            "catalog.schema.bad\x01name",
            "catalog.schema.bad\u2028name",
            "catalog.schema.`C:\\device`",
        ]:
            with self.subTest(table=value):
                self.assertEqual(
                    2,
                    cli(
                        [
                            "--table",
                            value,
                            "--key_col",
                            "message_id",
                            "--score_col",
                            "score",
                        ]
                    ).returncode,
                )

    def test_identifier_fuzz_rejects_unquoted_control_and_punctuation(self) -> None:
        randomizer = random.Random(0)
        dangerous_characters = ";-/\\ \t\n\r\x00"
        suffix_alphabet = "abcdefghijklmnopqrstuvwxyz0123456789_"
        for _ in range(128):
            suffix = "".join(randomizer.choice(suffix_alphabet) for _ in range(12))
            dangerous = randomizer.choice(dangerous_characters)
            with self.subTest(dangerous=dangerous, suffix=suffix):
                with self.assertRaises(argparse.ArgumentTypeError):
                    MODULE.parse_table_identifier(
                        f"catalog.schema.safe{dangerous}{suffix}"
                    )

        for _ in range(32):
            safe = "".join(
                randomizer.choice("abcdefghijklmnopqrstuvwxyz_") for _ in range(12)
            )
            self.assertEqual(
                f"`catalog`.`schema`.`{safe}`",
                MODULE.parse_table_identifier(f"catalog.schema.{safe}"),
            )

    def test_identifier_fragment_and_repetition_bounds_exit_two(self) -> None:
        too_long_segment = "a" * (MODULE.MAX_IDENTIFIER_SEGMENT_CHARS + 1)
        self.assertEqual(
            2,
            cli(
                [
                    "--table",
                    f"catalog.schema.`{too_long_segment}`",
                    "--key_col",
                    "message_id",
                    "--score_col",
                    "score",
                ]
            ).returncode,
        )
        too_long_fragment = "x" * (MODULE.MAX_UNSAFE_FRAGMENT_CHARS + 1)
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--unsafe-target-where-sql",
                    too_long_fragment,
                    "--allow-unsafe-sql-fragments",
                ]
            ).returncode,
        )
        repeated_order = [
            item
            for index in range(65)
            for item in ["--sample_order_col", f"tie_{index}"]
        ]
        self.assertEqual(
            2,
            cli(
                [*base_arguments(), "--attest-sample-order-unique", *repeated_order]
            ).returncode,
        )
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--attest-sample-order-unique",
                    "--sample_order_col",
                    "tie",
                    "--sample_order_col",
                    "tie",
                ]
            ).returncode,
        )
        source_columns = ",".join(f"source_{index}" for index in range(65))
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--source_table",
                    "catalog.schema.source",
                    "--source_null_cols",
                    source_columns,
                ]
            ).returncode,
        )

    def test_pre_render_total_output_budget_rejects_large_valid_parts(self) -> None:
        repeated_order = [
            item
            for index in range(64)
            for item in ["--sample_order_col", f"tie_{index}"]
        ]
        source_columns = ",".join(f"source_{index}" for index in range(64))
        fragment = "x" * MODULE.MAX_UNSAFE_FRAGMENT_CHARS
        result = cli(
            [
                *base_arguments(),
                "--source_table",
                "catalog.schema.source",
                "--source_null_cols",
                source_columns,
                "--source_updated_at_col",
                "updated_at",
                "--attest-sample-order-unique",
                *repeated_order,
                "--unsafe-source-where-sql",
                fragment,
                "--unsafe-target-where-sql",
                fragment,
                "--unsafe-stale-after-sql",
                fragment,
                "--allow-unsafe-sql-fragments",
                "--table_role",
                "source=catalog.schema.source",
                "--table_role",
                "target=catalog.schema.predictions",
            ]
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("render more than", result.stderr)

    def test_unsafe_fragments_require_acknowledgement_and_are_copied_verbatim(
        self,
    ) -> None:
        arguments = [
            *base_arguments(),
            "--unsafe-target-where-sql",
            "event_date = DATE '2026-08-29'",
        ]
        missing_acknowledgement = cli(arguments)
        self.assertEqual(2, missing_acknowledgement.returncode)
        output = generate([*arguments, "--allow-unsafe-sql-fragments"])
        self.assertIn("copied verbatim from trusted expert input", output)
        self.assertIn(
            "Do not put secrets in unsafe SQL fragments. Human review is required",
            output,
        )
        self.assertIn("WHERE event_date = DATE '2026-08-29';", output)

    def test_legacy_unsafe_alias_is_deprecated_and_requires_acknowledgement(
        self,
    ) -> None:
        arguments = [
            *base_arguments(),
            "--target_where",
            "event_date = DATE '2026-08-29'",
        ]
        self.assertEqual(2, cli(arguments).returncode)
        help_output = cli(["--help"])
        self.assertIn("DEPRECATED", help_output.stdout)
        output = generate([*arguments, "--allow-unsafe-sql-fragments"])
        self.assertIn("UNSAFE SQL FRAGMENTS", output)
        self.assertIn("deprecated raw-SQL alias", output)

    def test_sql_literal_escapes_oreilly(self) -> None:
        self.assertEqual("'O''Reilly'", MODULE.sql_literal("O'Reilly"))

    def test_staleness_query_distinguishes_all_states(self) -> None:
        output = generate(
            [
                *base_arguments(),
                "--source_table",
                "catalog.schema.source",
                "--source_updated_at_col",
                "updated_at",
            ]
        )
        self.assertIn("COUNT(*) AS source_row_count", output)
        self.assertIn("COUNT(`updated_at`) AS nonnull_timestamp_count", output)
        self.assertIn("MAX(`updated_at`) AS max_source_timestamp", output)
        for status in ["empty_source", "missing_timestamps", "stale", "fresh"]:
            self.assertIn(f"'{status}'", output)

    def test_model_version_checks_are_scoped_to_scored_rows(self) -> None:
        output = generate(
            [
                *base_arguments(),
                "--unsafe-target-where-sql",
                "event_date = DATE '2026-08-29'",
                "--allow-unsafe-sql-fragments",
            ]
        )
        unresolved = output.split(
            "-- Unresolved model versions among scored target rows only", 1
        )[1].split("-- Model versions", 1)[0]
        versions = output.split(
            "-- Model versions present among scored target rows only", 1
        )[1].split("-- Score distribution", 1)[0]
        self.assertIn("`spam_score` IS NOT NULL", unresolved)
        self.assertIn("`spam_score` IS NOT NULL", versions)
        self.assertIn("(event_date = DATE '2026-08-29')", unresolved)
        self.assertIn("(event_date = DATE '2026-08-29')", versions)

    def test_distribution_uses_the_same_scored_target_population(self) -> None:
        output = generate(
            [
                *base_arguments(),
                "--label_col",
                "prediction",
                "--unsafe-target-where-sql",
                "event_date = DATE '2026-08-29'",
                "--allow-unsafe-sql-fragments",
            ]
        )
        distribution = output.split(
            "-- Score distribution in the target population", 1
        )[1].split("-- Bounded key-ordered sample", 1)[0]
        self.assertIn("WHERE `spam_score` IS NOT NULL", distribution)
        self.assertIn("AND (event_date = DATE '2026-08-29')", distribution)
        self.assertIn("COUNT(*) AS row_count", distribution)
        self.assertIn("AVG(`spam_score`) AS avg_score", distribution)
        self.assertIn("MIN(`spam_score`) AS min_score", distribution)
        self.assertIn("MAX(`spam_score`) AS max_score", distribution)

        unfiltered = (
            generate(base_arguments())
            .split("-- Score distribution in the target population", 1)[1]
            .split("-- Bounded key-ordered sample", 1)[0]
        )
        self.assertIn("WHERE `spam_score` IS NOT NULL", unfiltered)

    def test_null_keys_and_duplicate_diagnostics_remain_separate(self) -> None:
        output = generate(base_arguments())
        self.assertIn("COUNT(*) AS null_key_count", output)
        self.assertIn("WHERE `message_id` IS NULL", output)
        self.assertIn("WHERE `message_id` IS NOT NULL", output)
        self.assertIn("Single non-NULL equality-key duplicate diagnostic", output)
        self.assertIn("not proof of a composite key", output)

    def test_blank_or_whitespace_reason_is_missing_for_outcome_and_distribution(
        self,
    ) -> None:
        output = generate([*base_arguments(), "--unscorable_reason_col", "reason"])
        normalized_reason = "NULLIF(TRIM(CAST(`reason` AS STRING)), '')"
        self.assertIn("AS population_scope", output)
        self.assertIn(
            f"`spam_score` IS NULL AND {normalized_reason} IS NOT NULL", output
        )
        self.assertIn("AS unscorable_count", output)
        self.assertIn(f"`spam_score` IS NULL AND {normalized_reason} IS NULL", output)
        self.assertIn("AS unexpected_null_score_count", output)
        self.assertIn(f"{normalized_reason} AS unscorable_reason", output)
        self.assertIn(f"GROUP BY {normalized_reason}", output)

    def test_invalid_or_mixed_table_roles_exit_two(self) -> None:
        for arguments in [
            [*base_arguments(), "--table_role", "source=catalog.schema.source"],
            [*base_arguments(), "--table_role", "target=catalog.schema.other"],
            [
                *base_arguments(),
                "--table_role",
                "target=catalog.schema.predictions",
                "--table_role",
                "target=catalog.schema.predictions",
            ],
            [
                *base_arguments(),
                "--source_table",
                "catalog.schema.source",
                "--table_role",
                "source=catalog.schema.other",
            ],
        ]:
            with self.subTest(arguments=arguments):
                self.assertEqual(2, cli(arguments).returncode)

    def test_sample_bounds_and_honest_key_ordering(self) -> None:
        self.assertEqual(2, cli([*base_arguments(), "--sample_n", "0"]).returncode)
        self.assertEqual(2, cli([*base_arguments(), "--sample_n", "1001"]).returncode)
        self.assertEqual(
            0,
            cli(
                [*base_arguments(), "--sample_n", "1001", "--allow-large-sample"]
            ).returncode,
        )
        self.assertEqual(
            2,
            cli(
                [*base_arguments(), "--sample_n", "10001", "--allow-large-sample"]
            ).returncode,
        )
        output = generate([*base_arguments(), "--sample_n", "3"])
        self.assertIn(
            "Bounded key-ordered sample; nondeterministic among duplicate keys", output
        )
        self.assertNotIn("-- Deterministic scored-row sample", output)
        self.assertIn("ORDER BY `message_id` ASC NULLS LAST", output)
        self.assertNotIn("RAND()", output)

    def test_attested_sample_order_uses_repeatable_tie_breakers(self) -> None:
        self.assertEqual(
            2, cli([*base_arguments(), "--sample_order_col", "scored_at"]).returncode
        )
        output = generate(
            [
                *base_arguments(),
                "--attest-sample-order-unique",
                "--sample_order_col",
                "scored_at",
                "--sample_order_col",
                "event_id",
            ]
        )
        self.assertIn("caller-attested unique key and tie-breaker tuple", output)
        self.assertIn(
            "ORDER BY `message_id` ASC NULLS LAST,\n    `scored_at` ASC NULLS LAST,\n    `event_id` ASC NULLS LAST",
            output,
        )
        key_only_output = generate([*base_arguments(), "--attest-sample-order-unique"])
        self.assertIn(
            "Total-order sample using the caller-attested unique key", key_only_output
        )

    def test_source_only_flags_need_source_table(self) -> None:
        for arguments in [
            [*base_arguments(), "--source_key_col", "source_id"],
            [*base_arguments(), "--source_null_cols", "subject"],
            [*base_arguments(), "--source_updated_at_col", "updated_at"],
            [
                *base_arguments(),
                "--unsafe-source-where-sql",
                "TRUE",
                "--allow-unsafe-sql-fragments",
            ],
            [
                *base_arguments(),
                "--unsafe-stale-after-sql",
                "current_timestamp()",
                "--allow-unsafe-sql-fragments",
            ],
        ]:
            with self.subTest(arguments=arguments):
                self.assertEqual(2, cli(arguments).returncode)

    def test_empty_or_incompatible_unsafe_staleness_fragments_exit_two(self) -> None:
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--unsafe-target-where-sql",
                    "",
                    "--allow-unsafe-sql-fragments",
                ]
            ).returncode,
        )
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--source_table",
                    "catalog.schema.source",
                    "--unsafe-stale-after-sql",
                    "current_timestamp()",
                    "--allow-unsafe-sql-fragments",
                ]
            ).returncode,
        )

    def test_explicit_model_version_disable_warns_and_empty_identifier_rejects(
        self,
    ) -> None:
        output = generate([*base_arguments(), "--no-model-version-check"])
        self.assertIn("WARNING: --no-model-version-check was selected", output)
        self.assertNotIn("Unresolved model versions", output)
        self.assertEqual(
            2, cli([*base_arguments(), "--model_version_col", ""]).returncode
        )
        self.assertEqual(
            2,
            cli(
                [
                    *base_arguments(),
                    "--no-model-version-check",
                    "--model_run_id_col",
                    "run_id",
                ]
            ).returncode,
        )

    def test_generator_output_is_repeatable_and_does_not_write_files(self) -> None:
        before = file_hashes(SKILL_ROOT)
        first = cli(base_arguments())
        second = cli(base_arguments())
        after = file_hashes(SKILL_ROOT)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(before, after)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("model_uri", source)
        self.assertNotIn("write_text", source)
        self.assertNotIn("open(", source)

    def test_label_gate_contract_covers_absent_negative_zero_variance_and_rare_cases(
        self,
    ) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        self.assertIn("expected_labels AS", contract)
        self.assertIn("staged_candidate_predictions", contract)
        self.assertIn("published_predictions", contract)
        self.assertIn("candidate_contract AS", contract)
        self.assertIn("LEFT JOIN candidate_counts", contract)
        self.assertIn("COALESCE(counts.label_rows, 0)", contract)
        self.assertIn("ABS((candidate.label_share - baseline.mean_share)", contract)
        self.assertIn(
            "baseline.std_share = 0 AND candidate.label_share <> baseline.mean_share",
            contract,
        )
        self.assertIn("ok_zero_variance_unchanged", contract)
        self.assertIn(
            "baseline.mean_share * candidate.bucket_rows < params.min_expected_label_rows",
            contract,
        )
        self.assertIn("candidate.bucket_rows < params.min_bucket_rows", contract)
        self.assertIn("window_state = 'closed'", contract)
        self.assertIn("source_window_end <= :evaluation_as_of", contract)
        self.assertIn("stage reconciliation is incomplete", contract)
        self.assertNotIn("date_trunc('hour', current_timestamp())", contract)
        self.assertIn("LEFT ANTI JOIN expected_labels", contract)
        self.assertIn("'unknown_label' AS quarantine_reason", contract)
        self.assertIn("BLOCK_PUBLISH_UNKNOWN_LABEL", contract)
        self.assertIn("BLOCK_PUBLISH_UNEXPECTED_NULL", contract)
        self.assertIn("BLOCK_PUBLISH_RECONCILIATION_MISMATCH", contract)
        self.assertIn("BLOCK_PUBLISH_MANIFEST_MISMATCH", contract)
        self.assertIn("artifact is empty, contains duplicates", contract)
        self.assertIn("An empty expected-label join is not a passing gate", contract)
        self.assertEqual(2, contract.count("expected_label_assertions AS ("))
        self.assertEqual(2, contract.count("gate_prerequisite_assertions AS ("))
        self.assertGreaterEqual(
            contract.count("manifest.source_table = params.source_table"), 2
        )
        self.assertGreaterEqual(
            contract.count(
                "manifest.target_population_contract_digest\n"
                "            = params.target_population_contract_digest"
            ),
            2,
        )
        gate_sql = (
            contract.split("## Gate The Staged Candidate", 1)[1]
            .split("```sql", 1)[1]
            .split("```", 1)[0]
        )
        sql_blocks = re.findall(r"```sql\n(.*?)```", contract, re.DOTALL)
        quarantine_sql = sql_blocks[1]
        publication_sql = sql_blocks[2]
        quarantine_parameters = quarantine_sql.split("parameter_assertions AS (", 1)[
            1
        ].split("latest_closed_manifest AS (", 1)[0]
        publication_parameters = publication_sql.split("parameter_assertions AS (", 1)[
            1
        ].split("latest_closed_manifest AS (", 1)[0]
        self.assertEqual(quarantine_parameters, publication_parameters)
        quarantine_gate = quarantine_sql.split("gate_prerequisite_assertions AS (", 1)[
            1
        ].split("candidate_contract AS (", 1)[0]
        publication_gate = publication_sql.split(
            "gate_prerequisite_assertions AS (", 1
        )[1].split("candidate_contract AS (", 1)[0]
        self.assertEqual(quarantine_gate, publication_gate)
        for cte_name in [
            "params",
            "parameter_assertions",
            "latest_closed_manifest",
            "matched_manifest",
            "matched_reconciliation",
            "prerequisite_assertions",
            "expected_labels",
            "expected_label_assertions",
            "gate_prerequisite_assertions",
        ]:
            quarantine_body = cte_body(quarantine_sql, cte_name)
            publication_body = cte_body(publication_sql, cte_name)
            self.assertEqual(
                hashlib.sha256(quarantine_body.encode("utf-8")).hexdigest(),
                hashlib.sha256(publication_body.encode("utf-8")).hexdigest(),
                cte_name,
            )
        self.assertIn("AND prerequisites.prerequisites_valid", quarantine_sql)
        self.assertLess(
            gate_sql.index("expected_label_assertions AS"),
            gate_sql.index("CROSS JOIN expected_label_assertions AS expected"),
        )
        candidate_ctes = gate_sql.split("baseline_source AS", 1)[0]
        self.assertIn("FROM staged_candidate_predictions AS staged", candidate_ctes)
        self.assertNotIn("published_predictions", candidate_ctes)
        self.assertIn("FROM published_predictions AS published", gate_sql)
        for binding in [
            "staged.scoring_run_id = params.scoring_run_id",
            "staged.scoring_run_id = staged.run_contract_digest",
            "SHA2(staged.run_contract_json, 256) = staged.run_contract_digest",
            "staged.run_contract_digest = params.run_contract_digest",
            "staged.run_contract_json = params.run_contract_json",
            "staged.source_table = params.source_table",
            "staged.source_delta_version = params.source_delta_version",
            "staged.source_window_start = params.source_window_start",
            "staged.source_window_end = params.source_window_end",
            "staged.resolved_model_uri = params.resolved_model_uri",
            "staged.model_version = params.model_version",
            "staged.model_run_id = params.model_run_id",
            "staged.threshold_version = params.threshold_version",
            "staged.label_map_version = params.label_map_version",
            "staged.expected_label_artifact_digest = params.expected_label_artifact_digest",
            "staged.target_population_contract_digest = params.target_population_contract_digest",
            "staged.unscorable_policy_version = params.unscorable_policy_version",
            "staged.feature_lookup_strategy = params.feature_lookup_strategy",
            "staged.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest",
            "staged.staged_candidate_table = params.staged_candidate_table",
            "staged.staged_candidate_delta_version = params.staged_candidate_delta_version",
            "staged.staged_candidate_snapshot_digest = params.staged_candidate_snapshot_digest",
        ]:
            self.assertIn(binding, contract)
        self.assertIn("INNER JOIN matched_manifest AS manifest", contract)
        self.assertIn("INNER JOIN matched_reconciliation AS reconciliation", contract)
        self.assertIn("reconciliation_status = 'succeeded'", contract)
        self.assertIn("reconciliation_complete = TRUE", contract)
        for reconciliation_binding in [
            "reconciliation.source_delta_version = params.source_delta_version",
            "reconciliation.resolved_model_uri = params.resolved_model_uri",
            "reconciliation.threshold_version = params.threshold_version",
            "reconciliation.label_map_version = params.label_map_version",
            "reconciliation.expected_label_artifact_digest",
            "reconciliation.unscorable_policy_version = params.unscorable_policy_version",
            "reconciliation.feature_lookup_strategy = params.feature_lookup_strategy",
            "reconciliation.feature_snapshot_pins_digest = params.feature_snapshot_pins_digest",
            "reconciliation.staged_candidate_table = params.staged_candidate_table",
            "reconciliation.staged_candidate_delta_version",
            "reconciliation.staged_candidate_snapshot_digest",
        ]:
            self.assertIn(reconciliation_binding, contract)
        for count_name in [
            "expected_row_count",
            "staged_row_count",
            "scoreable_row_count",
            "unscorable_row_count",
            "unknown_label_count",
            "unexpected_null_count",
        ]:
            self.assertIn(count_name, contract)
        self.assertIn("FROM versioned_unscorable_policy", contract)
        self.assertIn("policy.normalized_reason IS NOT NULL", contract)
        candidate_totals = contract.split("candidate_totals AS", 1)[1].split(
            "candidate_counts AS", 1
        )[0]
        self.assertIn("FROM candidate_classified", candidate_totals)
        self.assertNotIn("WHERE prediction IS NOT NULL", candidate_totals)
        self.assertIn("WHEN baseline.std_share IS NULL", contract)
        self.assertIn("min_baseline_buckets >= 2", contract)
        self.assertIn("NOT isnan(z_score_threshold)", contract)
        self.assertIn("bucket_width_hours = 1", contract)
        self.assertIn("computed_expected_label_artifact_digest", contract)
        self.assertIn("= params.expected_label_artifact_digest", contract)
        self.assertIn("gate_label_universe AS", publication_sql)
        self.assertIn("TRUE AS is_contract_gate_row", publication_sql)
        self.assertIn("WHERE expected_label_count = 0", publication_sql)
        self.assertIn("candidate.is_contract_gate_row", publication_sql)
        self.assertIn("params.scoring_run_id AS gate_scoring_run_id", publication_sql)
        self.assertIn(
            "params.run_contract_digest AS gate_run_contract_digest", publication_sql
        )
        self.assertEqual(3, contract.count("QUALIFY RANK() OVER"))
        self.assertNotIn("QUALIFY ROW_NUMBER", contract)
        self.assertIn(
            "source_window_end = source_window_start + INTERVAL 1 HOUR", contract
        )
        self.assertIn(
            "source_window_start = date_trunc('hour', source_window_start)", contract
        )
        for fixture_name in [
            "staged candidates from unrelated historical rows",
            "duplicate/tied latest manifest rows blocking both paths",
            "quarantine under invalid parameters",
            "manifest source/version/window mismatches",
            "missing or duplicate reconciliation",
            "empty expected-label artifact",
            "expected-label digest mismatch",
            "explicit empty-artifact gate-row emission",
            "forged mutually consistent run IDs/digests",
            "gated staged-snapshot mutation isolation",
            "exact outcome counts",
            "legitimate unscorable rows",
            "unexplained NULLs",
            "unknown labels",
            "incomplete latest bucket",
            "absent expected label",
            "negative drift",
            "one-bucket baseline",
            "zero-variance change",
            "unchanged zero variance",
            "rare labels",
            "undersized buckets",
            "non-finite parameters",
            "one-hour boundary alignment",
        ]:
            self.assertIn(fixture_name, contract)

    def test_one_bucket_baseline_and_nonfinite_parameters_fail_closed(self) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        std_null = contract.index("WHEN baseline.std_share IS NULL")
        zero_variance = contract.index(
            "WHEN baseline.std_share = 0 AND candidate.label_share <> baseline.mean_share"
        )
        self.assertLess(std_null, zero_variance)
        self.assertIn("min_baseline_buckets >= 2", contract)
        for numeric_name in [
            "min_baseline_buckets",
            "min_bucket_rows",
            "min_expected_label_rows",
            "z_score_threshold",
            "bucket_width_hours",
        ]:
            self.assertIn(f"{numeric_name} IS NOT NULL", contract)
        self.assertIn("CAST('Infinity' AS DOUBLE)", contract)
        self.assertIn(
            "min_baseline_buckets = CAST(min_baseline_buckets AS BIGINT)", contract
        )
        self.assertIn("min_bucket_rows = CAST(min_bucket_rows AS BIGINT)", contract)
        self.assertIn("BLOCK_PUBLISH_INVALID_PARAMETERS", contract)

    def test_candidate_bucket_is_exactly_one_aligned_closed_hour(self) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        self.assertIn("bucket_width_hours = 1", contract)
        self.assertIn(
            "source_window_start = date_trunc('hour', source_window_start)", contract
        )
        self.assertIn(
            "source_window_end = date_trunc('hour', source_window_end)", contract
        )
        self.assertIn(
            "source_window_end = source_window_start + INTERVAL 1 HOUR", contract
        )
        self.assertIn("source_window_end <= evaluation_as_of", contract)
        self.assertIn("reject every other value", contract)

    def test_quarantine_fails_empty_on_parameter_manifest_or_reconciliation_mismatch(
        self,
    ) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        quarantine_sql = re.findall(r"```sql\n(.*?)```", contract, re.DOTALL)[1]
        self.assertIn("COALESCE(parameters.parameters_valid, FALSE)", quarantine_sql)
        self.assertIn("prerequisites.matched_manifest_count = 1", quarantine_sql)
        self.assertIn("prerequisites.matched_reconciliation_count = 1", quarantine_sql)
        for manifest_binding in [
            "manifest.source_table = params.source_table",
            "manifest.source_delta_version = params.source_delta_version",
            "manifest.source_window_start = params.source_window_start",
            "manifest.source_window_end = params.source_window_end",
            "manifest.target_population_contract_digest",
        ]:
            self.assertIn(manifest_binding, quarantine_sql)
        self.assertIn(
            "reconciliation.reconciliation_status = 'succeeded'", quarantine_sql
        )
        self.assertIn("reconciliation.reconciliation_complete = TRUE", quarantine_sql)
        self.assertIn("AND prerequisites.prerequisites_valid", quarantine_sql)

    def test_empty_or_digest_mismatched_expected_labels_emit_explicit_block_row(
        self,
    ) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        publication_sql = re.findall(r"```sql\n(.*?)```", contract, re.DOTALL)[2]
        self.assertIn(
            "SHA2(TO_JSON(ARRAY_SORT(COLLECT_LIST(label_name))), 256)",
            publication_sql,
        )
        self.assertIn("expected.expected_label_count > 0", publication_sql)
        self.assertIn(
            "expected.computed_expected_label_artifact_digest", publication_sql
        )
        self.assertIn("= params.expected_label_artifact_digest", publication_sql)
        self.assertIn("SELECT CAST(NULL AS STRING) AS label_name", publication_sql)
        self.assertIn("TRUE AS is_contract_gate_row", publication_sql)
        self.assertIn("WHERE expected_label_count = 0", publication_sql)
        self.assertIn("BLOCK_PUBLISH_EXPECTED_LABEL_CONTRACT", publication_sql)

    def test_model_alias_is_resolved_once_and_only_immutable_uri_is_loaded(
        self,
    ) -> None:
        contract = MODEL_LOADING.read_text(encoding="utf-8")
        self.assertEqual(1, contract.count("get_model_version_by_alias("))
        self.assertIn(
            'immutable_model_uri = f"models:/{registered_model_name}/{resolved_version}"',
            contract,
        )
        self.assertIn("mlflow.pyfunc.load_model(immutable_model_uri)", contract)
        self.assertIn("loaded.metadata.run_id != resolved_run_id", contract)
        self.assertIn(
            "mlflow.pyfunc.spark_udf(\n    spark,\n    immutable_model_uri", contract
        )
        self.assertIn("requested_model_alias", contract)
        self.assertNotIn("load_model(model_version_uri)", contract)

    def test_signature_contract_requires_exact_named_struct_and_explicit_non_tabular_paths(
        self,
    ) -> None:
        contract = MODEL_SIGNATURE.read_text(encoding="utf-8")
        for property_name in [
            "exact name",
            "exact position",
            "compatible MLflow-to-Spark type",
            "required/nullability",
        ]:
            self.assertIn(property_name, contract)
        self.assertIn("F.struct(", contract)
        self.assertIn("F.col(name).alias(name)", contract)
        self.assertIn(
            "if spec.required and spec.name not in actual_feature_names", contract
        )
        self.assertIn("if spec.required or spec.name in actual_feature_names", contract)
        self.assertIn("if missing_required or extra_features", contract)
        self.assertIn("if actual_feature_names != ordered_feature_names", contract)
        self.assertIn("An absent optional field is valid", contract)
        self.assertIn("type with its MLflow type", contract)
        self.assertIn("actual values for required-field NULLs", contract)
        self.assertIn("Reject them in the default tabular named-struct path", contract)
        self.assertIn("multiple named column outputs", contract)
        self.assertIn(
            "declare a Spark `struct` with exact names, order, types, and nullability",
            contract,
        )
        for fixture_name in [
            "absent optional input",
            "present optional input in signature order",
            "missing required input",
            "reordered input",
            "extra feature",
            "type mismatch",
            "required input containing NULL",
        ]:
            self.assertIn(fixture_name, contract)

    def test_source_snapshot_feature_lookup_and_retry_pins_are_explicit(self) -> None:
        contract = CORE_PATTERNS.read_text(encoding="utf-8")
        self.assertIn('.option("versionAsOf", SOURCE_DELTA_VERSION)', contract)
        self.assertIn(
            "FeatureEngineeringClient.score_batch(immutable_model_uri, inference_df)",
            contract,
        )
        self.assertIn("observation timestamp with the same name and type", contract)
        self.assertIn("pin every feature table version", contract)
        self.assertIn(
            "Every partial or scheduler retry byte-compares canonical run-contract JSON",
            contract,
        )
        self.assertIn("ordered feature dependency snapshot pins", contract)
        self.assertIn("if any dependency advanced, fail", contract)

    def test_retry_safe_write_and_audit_recovery_contract_is_complete(self) -> None:
        contract = SAFE_WRITES.read_text(encoding="utf-8")
        self.assertIn("(business_key, scoring_run_id)", contract)
        self.assertIn("whenNotMatchedInsert", contract)
        self.assertNotIn("whenMatchedUpdate", contract)
        self.assertNotIn(".append()", contract)
        self.assertNotIn(".writeTo(", contract)
        self.assertIn(
            "pinned scoring source has invalid or duplicate business keys", contract
        )
        self.assertIn('canonical_pinned_keys.groupBy("business_key")', contract)
        self.assertIn('invalid_business_key(F.col("business_key"))', contract)
        self.assertIn('.where(F.col("count") != 1)', contract)
        self.assertIn('source.groupBy("business_key", "scoring_run_id")', contract)
        self.assertIn(
            "final prediction source has NULL or duplicate run keys", contract
        )
        self.assertIn(
            "scoring or feature lookup changed the pinned source cardinality", contract
        )
        self.assertIn("unexpected_keys = final_keys.join(", contract)
        self.assertIn("missing_keys = expected_keys.join(", contract)
        self.assertIn(
            "validate the actual final DataFrame immediately before `MERGE`", contract
        )
        self.assertIn("Serialize the run contract as canonical UTF-8 JSON", contract)
        self.assertIn("set `scoring_run_id` to that digest", contract)
        self.assertIn("byte-compare the canonical JSON", contract)
        self.assertIn(
            'F.col("scoring_run_id") != F.col("run_contract_digest")', contract
        )
        self.assertIn('F.sha2(F.col("run_contract_json"), 256)', contract)
        self.assertIn(
            'F.col("scoring_run_id") != F.lit(EXPECTED_SCORING_RUN_ID)', contract
        )
        self.assertIn("mixed, prior, or invalid run contract", contract)
        self.assertIn("feature_lookup_strategy", contract)
        self.assertIn("ordered `feature_snapshot_pins`", contract)
        self.assertIn(
            "one concurrent run or acquire a durable run-contract lock", contract
        )
        self.assertIn(
            "constraints do not replace this serialization contract", contract
        )
        self.assertIn("`NOT NULL` and `CHECK` clauses as informational", contract)
        self.assertIn(
            'attempt_id = sha256(scoring_run_id + ":" + decimal_attempt_ordinal)',
            contract,
        )
        for state in ["`running`", "`succeeded`", "`failed`"]:
            self.assertIn(state, contract)
        for recovery_case in [
            "No prediction rows",
            "Partial prediction rows",
            "All prediction rows committed but final audit update failed",
            "Existing `succeeded` audit",
        ]:
            self.assertIn(recovery_case, contract)

    def test_unique_pinned_input_does_not_excuse_duplicated_final_source(self) -> None:
        contract = SAFE_WRITES.read_text(encoding="utf-8")
        pinned_check = contract.index('canonical_pinned_keys.groupBy("business_key")')
        final_selection = contract.index(
            "source = staged_candidate_df.select(*PREDICTION_COLUMNS)"
        )
        final_check = contract.index('source.groupBy("business_key", "scoring_run_id")')
        merge = contract.index('target.alias("t").merge(')
        self.assertLess(pinned_check, final_selection)
        self.assertLess(final_selection, final_check)
        self.assertLess(final_check, merge)
        self.assertIn('(F.col("count") != 1)', contract[final_check:merge])
        self.assertIn("unexpected_keys.limit(1).count()", contract[final_check:merge])
        self.assertIn("missing_keys.limit(1).count()", contract[final_check:merge])

    def test_final_source_run_contract_identity_is_checked_before_merge(self) -> None:
        contract = SAFE_WRITES.read_text(encoding="utf-8")
        digest_check = contract.index(
            'F.col("run_contract_digest") != F.sha2(F.col("run_contract_json"), 256)'
        )
        mixed_check = contract.index(
            'F.col("scoring_run_id") != F.lit(EXPECTED_SCORING_RUN_ID)'
        )
        merge = contract.index('target.alias("t").merge(')
        self.assertLess(digest_check, merge)
        self.assertLess(mixed_check, merge)

    def test_merge_rereads_exact_gated_staged_snapshot_and_rechecks_outcomes(
        self,
    ) -> None:
        contract = SAFE_WRITES.read_text(encoding="utf-8")
        self.assertNotIn("source = scored_df.select(*PREDICTION_COLUMNS)", contract)
        self.assertIn(
            '.option("versionAsOf", GATED_STAGED_CANDIDATE_DELTA_VERSION)', contract
        )
        self.assertIn(".table(GATED_STAGED_CANDIDATE_TABLE)", contract)
        self.assertIn(
            "source = staged_candidate_df.select(*PREDICTION_COLUMNS)", contract
        )
        self.assertIn("GATED_STAGED_CANDIDATE_SNAPSHOT_DIGEST", contract)
        self.assertIn("merge source differs from the gated staged snapshot", contract)
        self.assertIn("metadata_mismatch = (", contract)
        self.assertIn("EXPECTED_TARGET_POPULATION_DIGEST", contract)
        self.assertIn("EXPECTED_FEATURE_PINS_DIGEST", contract)
        self.assertIn("EXPECTED_RESOLVED_MODEL_URI", contract)
        self.assertIn("EXPECTED_LABEL_ARTIFACT_DIGEST", contract)
        self.assertIn("gated snapshot metadata differs", contract)
        self.assertIn("gated snapshot contains an unexplained NULL", contract)
        self.assertIn("gated snapshot contains an unknown prediction label", contract)
        self.assertIn("outside the pinned unscorable policy", contract)
        self.assertIn("never recompute scoring after the gate", contract)

    def test_business_key_grammar_rejects_blank_whitespace_and_mixed_inputs(
        self,
    ) -> None:
        pattern = BUSINESS_KEY_MODULE.BUSINESS_KEY_PATTERN
        maximum = BUSINESS_KEY_MODULE.BUSINESS_KEY_MAX_CHARS
        self.assertEqual(512, maximum)
        for valid in ["a", "-128", "A-1", "tenant:key/42", "abc_def@example.com"]:
            self.assertIsNotNone(pattern.fullmatch(valid), valid)
        invalid = ["", " ", "\t", " a", "a ", "a b", "é", "a" * 513]
        for value in invalid:
            self.assertIsNone(pattern.fullmatch(value), repr(value))
        mixed = ["valid-1", " ", "valid-2"]
        self.assertTrue(any(pattern.fullmatch(value) is None for value in mixed))

        contract = SAFE_WRITES.read_text(encoding="utf-8")
        self.assertIn("canonical_pinned_keys.where(", contract)
        self.assertIn('invalid_business_key(F.col("business_key"))', contract)
        self.assertIn("final staged source has an invalid business key", contract)
        self.assertIn("pinned scoring source has an invalid business key", contract)
        ddl = SCORING_DDL.read_text(encoding="utf-8")
        self.assertIn("CONSTRAINT canonical_business_key CHECK", ddl)
        self.assertIn("business_key = TRIM(business_key)", ddl)
        self.assertIn("TRIM(CAST(business_key AS STRING)) <> ''", ddl)
        self.assertIn("LENGTH(business_key) BETWEEN 1 AND 512", ddl)
        self.assertIn(BUSINESS_KEY_MODULE.BUSINESS_KEY_PATTERN.pattern, ddl)

    def test_business_key_schema_helper_rejects_staged_unsupported_types(self) -> None:
        for allowed in [
            "string",
            "tinyint",
            "smallint",
            "int",
            "bigint",
            "decimal(1,0)",
            "decimal(38,18)",
        ]:
            self.assertTrue(
                BUSINESS_KEY_MODULE.is_supported_spark_type_name(allowed), allowed
            )
        for rejected in [
            None,
            True,
            "boolean",
            "float",
            "double",
            "binary",
            "array<string>",
            "map<string,string>",
            "struct<id:string>",
            "decimal(39,0)",
            "decimal(10,19)",
            "decimal(0,0)",
            " string",
        ]:
            self.assertFalse(
                BUSINESS_KEY_MODULE.is_supported_spark_type_name(rejected), rejected
            )

        contract = SAFE_WRITES.read_text(encoding="utf-8")
        pinned_check = contract.index(
            'require_supported_business_key_schema(pinned_source_df, "pinned scoring source")'
        )
        staged_check = contract.index(
            'require_supported_business_key_schema(staged_candidate_df, "staged candidate source")'
        )
        staged_cast = contract.index(
            '"business_key", F.col("business_key").cast("string")'
        )
        self.assertLess(pinned_check, staged_check)
        self.assertLess(staged_check, staged_cast)

    def test_business_key_value_helper_rejects_boolean_float_collection_and_mixed(
        self,
    ) -> None:
        self.assertEqual(
            "valid-1",
            BUSINESS_KEY_MODULE.canonical_business_key("valid-1", "string"),
        )
        self.assertEqual("42", BUSINESS_KEY_MODULE.canonical_business_key(42, "bigint"))
        self.assertEqual(
            "12.5",
            BUSINESS_KEY_MODULE.canonical_business_key(Decimal("12.5"), "decimal(3,1)"),
        )
        invalid_cases = [
            (True, "boolean"),
            (True, "int"),
            (1.5, "double"),
            (["a"], "array<string>"),
            ({"id": "a"}, "map<string,string>"),
            (b"a", "binary"),
            (" ", "string"),
        ]
        for value, type_name in invalid_cases:
            with self.subTest(type_name=type_name):
                self.assertIsNone(
                    BUSINESS_KEY_MODULE.canonical_business_key(value, type_name)
                )
        mixed_staged = [("valid", "string"), (True, "boolean")]
        self.assertTrue(
            any(
                BUSINESS_KEY_MODULE.canonical_business_key(value, type_name) is None
                for value, type_name in mixed_staged
            )
        )

    def test_business_key_integral_domains_are_exact_and_total(self) -> None:
        domains = {
            "tinyint": (-(1 << 7), (1 << 7) - 1),
            "smallint": (-(1 << 15), (1 << 15) - 1),
            "int": (-(1 << 31), (1 << 31) - 1),
            "bigint": (-(1 << 63), (1 << 63) - 1),
        }
        for type_name, (lower, upper) in domains.items():
            with self.subTest(type_name=type_name, boundary="lower"):
                self.assertEqual(
                    str(lower),
                    BUSINESS_KEY_MODULE.canonical_business_key(lower, type_name),
                )
            with self.subTest(type_name=type_name, boundary="upper"):
                self.assertEqual(
                    str(upper),
                    BUSINESS_KEY_MODULE.canonical_business_key(upper, type_name),
                )
            self.assertIsNone(
                BUSINESS_KEY_MODULE.canonical_business_key(lower - 1, type_name)
            )
            self.assertIsNone(
                BUSINESS_KEY_MODULE.canonical_business_key(upper + 1, type_name)
            )
        self.assertIsNone(
            BUSINESS_KEY_MODULE.canonical_business_key(10**100_000, "bigint")
        )

    def test_business_key_decimal_precision_scale_and_canonicalization(self) -> None:
        valid_cases = [
            (Decimal("123.4"), "decimal(4,1)", "123.4"),
            (Decimal("1.230"), "decimal(3,2)", "1.23"),
            (Decimal("1E+2"), "decimal(3,0)", "100"),
            (Decimal("-0.00"), "decimal(1,0)", "0"),
            (Decimal("-12.30"), "decimal(4,2)", "-12.3"),
            (Decimal("0.00100"), "decimal(3,3)", "0.001"),
        ]
        for value, type_name, expected in valid_cases:
            with self.subTest(value=str(value), type_name=type_name):
                self.assertEqual(
                    expected,
                    BUSINESS_KEY_MODULE.canonical_business_key(value, type_name),
                )

        invalid_cases = [
            (Decimal("123.4"), "decimal(3,1)"),
            (Decimal("1.23"), "decimal(3,1)"),
            (Decimal("1000"), "decimal(3,0)"),
            (Decimal("0.001"), "decimal(2,2)"),
            (Decimal("NaN"), "decimal(3,1)"),
            (Decimal("Infinity"), "decimal(3,1)"),
            (Decimal("1E+100000"), "decimal(38,0)"),
        ]
        for value, type_name in invalid_cases:
            with self.subTest(value=str(value), type_name=type_name):
                self.assertIsNone(
                    BUSINESS_KEY_MODULE.canonical_business_key(value, type_name)
                )

    def test_business_key_declared_decimal_parser_is_total(self) -> None:
        self.assertFalse(
            BUSINESS_KEY_MODULE.is_supported_spark_type_name(
                "decimal(" + ("9" * 10_000) + ",0)"
            )
        )

    def test_both_gates_rehash_staged_run_contract_json(self) -> None:
        contract = LABEL_GATE.read_text(encoding="utf-8")
        self.assertEqual(
            2,
            contract.count(
                "SHA2(staged.run_contract_json, 256) = staged.run_contract_digest"
            ),
        )

    def test_publication_decision_golden_cases(self) -> None:
        cases = json.loads(DECISION_CASES.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "all_ok_allows",
                "zero_variance_unchanged_allows",
                "mixed_allow_statuses_allow",
                "mixed_allow_and_block_blocks",
                "empty_blocks",
                "unknown_status_blocks",
                "mixed_contract_blocks",
                "null_row_identity_blocks",
                "blank_row_identity_blocks",
            },
            {case["name"] for case in cases},
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    case["expected"],
                    DECISION_MODULE.derive_publication_decision(
                        case["rows"], VALID_IDENTITY, VALID_IDENTITY
                    ),
                )

    def test_invalid_expected_identity_blocks_direct_derivation(self) -> None:
        invalid_values = [None, True, "", " ", "a" * 63, "A" * 64]
        allow_row = {
            "gate_scoring_run_id": VALID_IDENTITY,
            "gate_run_contract_digest": VALID_IDENTITY,
            "publish_gate": "ok",
        }
        for invalid in invalid_values:
            with self.subTest(invalid=repr(invalid)):
                result = DECISION_MODULE.derive_publication_decision(
                    [allow_row], invalid, invalid
                )
                self.assertEqual("BLOCK_PUBLISH", result["publication_decision"])
                self.assertEqual(
                    "invalid_expected_contract", result["publication_reason"]
                )
        mismatch = DECISION_MODULE.derive_publication_decision(
            [allow_row], VALID_IDENTITY, "b" * 64
        )
        self.assertEqual("invalid_expected_contract", mismatch["publication_reason"])

    def test_valid_expected_identity_rejects_invalid_row_identity(self) -> None:
        for invalid in [None, True, "", " ", "a" * 63, "A" * 64]:
            row = {
                "gate_scoring_run_id": invalid,
                "gate_run_contract_digest": invalid,
                "publish_gate": "ok",
            }
            with self.subTest(invalid=repr(invalid)):
                result = DECISION_MODULE.derive_publication_decision(
                    [row], VALID_IDENTITY, VALID_IDENTITY
                )
                self.assertEqual("BLOCK_PUBLISH", result["publication_decision"])
                expected_reason = (
                    "mixed_gate_contract"
                    if isinstance(invalid, str)
                    else "invalid_gate_row"
                )
                self.assertEqual(expected_reason, result["publication_reason"])

    def test_malformed_gate_rows_block_without_exception_or_value_reflection(
        self,
    ) -> None:
        valid_fields = {
            "gate_scoring_run_id": VALID_IDENTITY,
            "gate_run_contract_digest": VALID_IDENTITY,
            "publish_gate": "ok",
        }
        malformed_rows = [
            None,
            True,
            "secret-row-value",
            [],
            {"gate_scoring_run_id": VALID_IDENTITY},
            {**valid_fields, "extra": "secret-extra-value"},
            {**valid_fields, "publish_gate": ["ok"]},
            {**valid_fields, "gate_scoring_run_id": {"nested": "secret"}},
        ]
        for malformed in malformed_rows:
            with self.subTest(malformed=type(malformed).__name__):
                result = DECISION_MODULE.derive_publication_decision(
                    [malformed], VALID_IDENTITY, VALID_IDENTITY
                )
                self.assertEqual(
                    {
                        "publication_decision": "BLOCK_PUBLISH",
                        "publication_reason": "invalid_gate_row",
                        "gate_row_count": 1,
                    },
                    result,
                )
                self.assertNotIn("secret", json.dumps(result))
        top_level = DECISION_MODULE.derive_publication_decision(
            None, VALID_IDENTITY, VALID_IDENTITY
        )
        self.assertEqual("invalid_gate_row", top_level["publication_reason"])

    def test_decision_cli_malformed_gate_rows_return_fixed_block(self) -> None:
        malformed_rows = [None, True, "secret-row-value", [], {"publish_gate": "ok"}]
        for malformed in malformed_rows:
            with self.subTest(malformed=type(malformed).__name__):
                result = decision_cli(VALID_IDENTITY, VALID_IDENTITY, [malformed])
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stderr)
                parsed = json.loads(result.stdout)
                self.assertEqual("BLOCK_PUBLISH", parsed["publication_decision"])
                self.assertEqual("invalid_gate_row", parsed["publication_reason"])
                self.assertNotIn("secret", result.stdout)

    def test_decision_cli_rejects_invalid_expected_identity_without_echo(self) -> None:
        for invalid in ["", " ", "a" * 63, "A" * 64]:
            with self.subTest(invalid=repr(invalid)):
                result = decision_cli(invalid, invalid, [])
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("", result.stdout)
                if invalid.strip():
                    self.assertNotIn(invalid, result.stderr)
        mismatch = decision_cli(VALID_IDENTITY, "b" * 64, [])
        self.assertNotEqual(0, mismatch.returncode)
        self.assertEqual("", mismatch.stdout)

    def test_decision_cli_allows_valid_identity_and_allow_status(self) -> None:
        row = {
            "gate_scoring_run_id": VALID_IDENTITY,
            "gate_run_contract_digest": VALID_IDENTITY,
            "publish_gate": "ok_zero_variance_unchanged",
        }
        result = decision_cli(VALID_IDENTITY, VALID_IDENTITY, [row])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "publication_decision": "ALLOW_PUBLISH",
                "publication_reason": "all_label_gates_allow",
                "gate_row_count": 1,
            },
            json.loads(result.stdout),
        )

    def test_diagnostic_query_projection_allows_exact_allow_rows(self) -> None:
        for status in ["ok", "ok_zero_variance_unchanged"]:
            with self.subTest(status=status):
                projected = DECISION_MODULE.project_label_gate_query_rows(
                    [label_gate_query_row(status)]
                )
                self.assertEqual(
                    [
                        {
                            "gate_scoring_run_id": VALID_IDENTITY,
                            "gate_run_contract_digest": VALID_IDENTITY,
                            "publish_gate": status,
                        }
                    ],
                    projected,
                )
                result = DECISION_MODULE.derive_publication_decision(
                    projected, VALID_IDENTITY, VALID_IDENTITY
                )
                self.assertEqual("ALLOW_PUBLISH", result["publication_decision"])

    def test_diagnostic_query_projection_block_mixed_and_mismatch(self) -> None:
        blocked = DECISION_MODULE.derive_publication_decision(
            DECISION_MODULE.project_label_gate_query_rows(
                [label_gate_query_row("BLOCK_PUBLISH_UNKNOWN_LABEL")]
            ),
            VALID_IDENTITY,
            VALID_IDENTITY,
        )
        self.assertEqual("BLOCK_PUBLISH", blocked["publication_decision"])

        mixed = DECISION_MODULE.derive_publication_decision(
            DECISION_MODULE.project_label_gate_query_rows(
                [
                    label_gate_query_row("ok"),
                    label_gate_query_row("insufficient_baseline", label_name="spam"),
                ]
            ),
            VALID_IDENTITY,
            VALID_IDENTITY,
        )
        self.assertEqual("BLOCK_PUBLISH", mixed["publication_decision"])

        with self.assertRaisesRegex(ValueError, "invalid label-gate query row"):
            DECISION_MODULE.project_label_gate_query_rows(
                [label_gate_query_row("ok", gate_scoring_run_id="b" * 64)]
            )

    def test_diagnostic_query_rejects_invalid_or_mixed_unknown_set_digest(
        self,
    ) -> None:
        for invalid in [None, "", "C" * 64, "c" * 63, "secret-digest"]:
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaisesRegex(
                    ValueError, "^invalid label-gate query row$"
                ):
                    DECISION_MODULE.project_label_gate_query_rows(
                        [label_gate_query_row("ok", unknown_label_set_digest=invalid)]
                    )
        with self.assertRaisesRegex(ValueError, "^invalid label-gate query row$"):
            DECISION_MODULE.project_label_gate_query_rows(
                [
                    label_gate_query_row("ok"),
                    label_gate_query_row(
                        "ok",
                        label_name="spam",
                        unknown_label_set_digest="d" * 64,
                    ),
                ]
            )
        cli_result = query_decision_cli(
            [label_gate_query_row("ok", unknown_label_set_digest="secret-digest")]
        )
        self.assertEqual(2, cli_result.returncode)
        self.assertEqual("", cli_result.stdout)
        self.assertNotIn("secret-digest", cli_result.stderr)
        with self.assertRaisesRegex(ValueError, "^invalid label-gate query row$"):
            DECISION_MODULE.project_label_gate_query_rows(
                [
                    label_gate_query_row("ok", unknown_label_count=0),
                    label_gate_query_row(
                        "ok", label_name="spam", unknown_label_count=1
                    ),
                ]
            )

    def test_diagnostic_query_projection_rejects_schema_drift_without_value_leak(
        self,
    ) -> None:
        extra = label_gate_query_row("ok", unexpected_field="secret-extra")
        with self.assertRaisesRegex(ValueError, "^invalid label-gate query row$"):
            DECISION_MODULE.project_label_gate_query_rows([extra])
        direct = DECISION_MODULE.derive_publication_decision(
            [extra], VALID_IDENTITY, VALID_IDENTITY
        )
        self.assertEqual("invalid_gate_row", direct["publication_reason"])

        cli_result = query_decision_cli([extra])
        self.assertNotEqual(0, cli_result.returncode)
        self.assertEqual("", cli_result.stdout)
        self.assertNotIn("secret-extra", cli_result.stderr)

    def test_diagnostic_query_projection_cli_end_to_end(self) -> None:
        result = query_decision_cli(
            [label_gate_query_row("ok_zero_variance_unchanged")]
        )
        self.assertEqual(0, result.returncode, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual("ALLOW_PUBLISH", parsed["publication_decision"])
        self.assertEqual(VALID_UNKNOWN_SET_DIGEST, parsed["unknown_label_set_digest"])
        self.assertEqual(0, parsed["unknown_label_count"])
        contract = LABEL_GATE.read_text(encoding="utf-8")
        documented = json.loads(
            re.findall(r"```json\n(.*?)```", contract, re.DOTALL)[0]
        )
        self.assertEqual(
            DECISION_MODULE.LABEL_GATE_QUERY_ROW_FIELDS, frozenset(documented)
        )
        self.assertIn("--label-gate-query-results-json $gateRows", contract)

    def test_diagnostic_query_cli_rejects_giant_and_nonfinite_numbers_without_trace(
        self,
    ) -> None:
        invalid_numbers = [10**400, float("nan"), float("inf"), float("-inf")]
        for invalid in invalid_numbers:
            with self.subTest(kind=type(invalid).__name__, value=str(invalid)[:16]):
                result = query_decision_cli(
                    [label_gate_query_row("ok", label_share=invalid)]
                )
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("invalid label-gate query results", result.stderr)
                if type(invalid) is int:
                    self.assertNotIn(str(invalid), result.stderr)

    def test_public_json_flags_reject_decoder_digit_limit_without_trace_or_reflection(
        self,
    ) -> None:
        digits = "7" * 5_000
        rich_row = label_gate_query_row("ok", label_share="NUMERIC_MARKER")
        core_row = {
            "gate_scoring_run_id": VALID_IDENTITY,
            "gate_run_contract_digest": VALID_IDENTITY,
            "publish_gate": "NUMERIC_MARKER",
        }
        payloads = {
            "--label-gate-query-results-json": json.dumps(
                [rich_row], separators=(",", ":")
            ).replace(json.dumps("NUMERIC_MARKER"), digits),
            "--gate-results-json": json.dumps(
                [core_row], separators=(",", ":")
            ).replace(json.dumps("NUMERIC_MARKER"), digits),
        }
        for flag, payload in payloads.items():
            self.assertLess(len(payload), DECISION_MODULE.MAX_GATE_RESULTS_JSON_CHARS)
            with self.subTest(flag=flag):
                result = raw_decision_cli(flag, payload)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(digits[:64], result.stderr)
                self.assertIn(
                    "gate results JSON contains an invalid numeric token",
                    result.stderr,
                )

    def test_public_json_decode_error_message_remains_fixed_and_value_free(
        self,
    ) -> None:
        result = raw_decision_cli("--gate-results-json", "[{bad-json]")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("gate results JSON is invalid:", result.stderr)
        self.assertNotIn("bad-json", result.stderr)

    def test_finite_number_validator_is_total_for_arbitrary_exact_integers(
        self,
    ) -> None:
        self.assertTrue(DECISION_MODULE._valid_finite_number(10**308))
        self.assertFalse(DECISION_MODULE._valid_finite_number(10**309))
        self.assertFalse(DECISION_MODULE._valid_finite_number(10**100_000))
        self.assertFalse(DECISION_MODULE._valid_finite_number(float("nan")))
        self.assertFalse(DECISION_MODULE._valid_finite_number(float("inf")))

    def test_sql_and_merge_use_the_same_run_level_admission_mapping(self) -> None:
        gate_contract = LABEL_GATE.read_text(encoding="utf-8")
        write_contract = SAFE_WRITES.read_text(encoding="utf-8")
        self.assertEqual(
            {"ok", "ok_zero_variance_unchanged"}, DECISION_MODULE.ALLOW_STATUSES
        )
        self.assertIn(
            "publish_gate NOT IN ('ok', 'ok_zero_variance_unchanged')",
            gate_contract,
        )
        self.assertIn("gate_row_count = 0", gate_contract)
        self.assertIn(
            "expected_scoring_run_id = TRIM(expected_scoring_run_id)", gate_contract
        )
        self.assertIn("expected_scoring_run_id RLIKE '^[0-9a-f]{64}$'", gate_contract)
        self.assertIn(
            "expected_scoring_run_id = expected_run_contract_digest", gate_contract
        )
        self.assertIn("gate_scoring_run_id IS DISTINCT FROM", gate_contract)
        self.assertIn(
            "gate_scoring_run_id IS DISTINCT FROM gate_run_contract_digest",
            gate_contract,
        )
        self.assertIn("invalid_expected_contract", gate_contract)
        self.assertIn("contract_mismatch_count > 0", gate_contract)
        self.assertIn("non_allow_status_count > 0", gate_contract)
        self.assertIn(
            'DERIVED_RUN_PUBLICATION_DECISION != "ALLOW_PUBLISH"', write_contract
        )
        self.assertIn(
            'DERIVED_RUN_PUBLICATION_REASON != "all_label_gates_allow"',
            write_contract,
        )
        self.assertEqual("ALLOW_PUBLISH", DECISION_MODULE.ALLOW_DECISION)

    def test_prediction_and_audit_ddl_carry_required_traceability(self) -> None:
        prediction_ddl = SCORING_DDL.read_text(encoding="utf-8").lower()
        audit_ddl = AUDIT_DDL.read_text(encoding="utf-8").lower()
        for column in [
            "business_key",
            "scoring_run_id",
            "source_delta_version",
            "source_window_start",
            "source_window_end",
            "observation_timestamp",
            "requested_model_alias",
            "resolved_model_uri",
            "model_version",
            "model_run_id",
            "score_kind",
            "raw_score",
            "calibrated_score",
            "threshold_version",
            "label_map_version",
            "unscorable_reason",
            "run_contract_digest",
            "run_contract_json",
            "run_contract_artifact_uri",
            "target_population_contract_digest",
            "staged_candidate_table",
            "staged_candidate_delta_version",
            "staged_candidate_snapshot_digest",
            "feature_lookup_strategy",
            "feature_snapshot_pins",
            "feature_snapshot_pins_digest",
            "expected_label_artifact_digest",
            "unscorable_policy_version",
        ]:
            self.assertIn(column, prediction_ddl)
        for column in [
            "scoring_run_id",
            "attempt_id",
            "attempt_ordinal",
            "source_delta_version",
            "observation_timestamp_definition",
            "signature_digest",
            "prediction_target_commit_version",
            "status",
            "run_contract_digest",
            "run_contract_json",
            "run_contract_artifact_uri",
            "target_population_contract_digest",
            "staged_candidate_table",
            "staged_candidate_delta_version",
            "staged_candidate_snapshot_digest",
            "feature_lookup_strategy",
            "feature_snapshot_pins",
            "feature_snapshot_pins_digest",
            "expected_label_artifact_digest",
            "unscorable_policy_version",
            "expected_row_count",
            "staged_row_count",
            "scoreable_row_count",
            "unscorable_row_count",
            "unknown_label_count",
            "unexpected_null_prediction_count",
            "publication_decision",
            "publication_reason",
            "publication_gate_row_count",
        ]:
            self.assertIn(column, audit_ddl)
        self.assertIn("running", audit_ddl)
        self.assertIn("succeeded", audit_ddl)
        self.assertIn("failed", audit_ddl)
        self.assertIn("run_id_matches_contract_digest", prediction_ddl)
        self.assertIn("run_id_matches_contract_digest", audit_ddl)
        self.assertIn("canonical_run_identity", prediction_ddl)
        self.assertIn("canonical_run_identity", audit_ddl)
        self.assertIn("valid_publication_decision", audit_ddl)

    def test_quarantine_ddl_is_complete_and_has_no_value_markers(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        gate = LABEL_GATE.read_text(encoding="utf-8")
        ddl = QUARANTINE_DDL.read_text(encoding="utf-8")

        def table_columns(marker: str) -> list[str]:
            body = ddl.split(marker, 1)[1].split("\n)\nUSING DELTA", 1)[0]
            return [
                match.group(1)
                for line in body.splitlines()
                if (
                    match := re.match(
                        r"^    ([a-z][a-z0-9_]*) "
                        r"(?:STRING|BIGINT|TIMESTAMP)",
                        line,
                    )
                )
            ]

        intent_columns = [
            "intent_record_id",
            "record_kind",
            "quarantine_row_id",
            "intent_content_digest",
            "business_key",
            "scoring_run_id",
            "run_contract_digest",
            "source_table",
            "source_delta_version",
            "source_window_start",
            "source_window_end",
            "observation_timestamp",
            "target_population_contract_digest",
            "staged_candidate_table",
            "staged_candidate_delta_version",
            "staged_candidate_snapshot_digest",
            "resolved_model_uri",
            "model_version",
            "model_run_id",
            "label_map_version",
            "expected_label_artifact_digest",
            "prediction",
            "quarantine_reason",
            "expected_unknown_label_count",
            "expected_unknown_label_set_digest",
            "intent_created_at",
        ]
        target_columns = [
            "quarantine_row_id",
            "business_key",
            "scoring_run_id",
            "run_contract_digest",
            "source_table",
            "source_delta_version",
            "source_window_start",
            "source_window_end",
            "observation_timestamp",
            "target_population_contract_digest",
            "staged_candidate_table",
            "staged_candidate_delta_version",
            "staged_candidate_snapshot_digest",
            "resolved_model_uri",
            "model_version",
            "model_run_id",
            "label_map_version",
            "expected_label_artifact_digest",
            "prediction",
            "quarantine_reason",
            "quarantined_at",
        ]
        self.assertEqual(
            intent_columns,
            table_columns("${catalog}.${schema}.${quarantine_intent_table}"),
        )
        self.assertEqual(
            target_columns,
            table_columns("${catalog}.${schema}.${quarantine_table}"),
        )
        self.assertIn("assets/scoring-quarantine-table-ddl.sql", skill)
        self.assertIn("../assets/scoring-quarantine-table-ddl.sql", gate)
        self.assertIn("${quarantine_intent_table}", ddl)
        self.assertIn("${quarantine_table}", ddl)
        self.assertEqual(2, ddl.count("USING DELTA"))
        self.assertIsNone(re.search(r":[a-z_][a-z0-9_]*", ddl))
        self.assertIn("CONSTRAINT valid_intent_record_shape CHECK", ddl)
        self.assertIn("CONSTRAINT canonical_quarantine_row_id CHECK", ddl)
        self.assertIn("CONSTRAINT unknown_label_only CHECK", ddl)

    def test_quarantine_persistence_is_insert_only_retry_safe_and_exact(self) -> None:
        gate = LABEL_GATE.read_text(encoding="utf-8")
        ddl = QUARANTINE_DDL.read_text(encoding="utf-8")
        sql_blocks = re.findall(r"```sql\n(.*?)```", gate, re.DOTALL)
        intent_merge = next(
            block
            for block in sql_blocks
            if "MERGE INTO ${catalog}.${schema}.${quarantine_intent_table}" in block
        )
        intent_check = next(
            block for block in sql_blocks if "quarantine_intent_asserted" in block
        )
        target_merge = next(
            block
            for block in sql_blocks
            if "MERGE INTO ${catalog}.${schema}.${quarantine_table}" in block
        )
        final_check = next(
            block for block in sql_blocks if "quarantine_persistence_asserted" in block
        )

        def ddl_columns_for(marker: str) -> list[str]:
            body = ddl.split(marker, 1)[1].split("\n)\nUSING DELTA", 1)[0]
            return [
                match.group(1)
                for line in body.splitlines()
                if (
                    match := re.match(
                        r"^    ([a-z][a-z0-9_]*) "
                        r"(?:STRING|BIGINT|TIMESTAMP)",
                        line,
                    )
                )
            ]

        def inserted_columns(merge: str) -> list[str]:
            inserted = re.search(
                r"WHEN NOT MATCHED THEN INSERT \(\n(.*?)\n\) VALUES",
                merge,
                re.DOTALL,
            )
            self.assertIsNotNone(inserted)
            assert inserted is not None
            return [
                line.strip().removesuffix(",")
                for line in inserted.group(1).splitlines()
                if line.strip()
            ]

        self.assertNotIn("CREATE OR REPLACE TEMP VIEW", gate)
        self.assertIn("LEFT ANTI JOIN expected_labels", intent_merge)
        self.assertIn("ASSERT_TRUE(", intent_merge)
        self.assertIn(":expected_unknown_label_count", intent_merge)
        self.assertIn(":expected_unknown_label_set_digest", intent_merge)
        self.assertIn("actual_unknown_set_digest", intent_merge)
        self.assertIn("'header' AS record_kind", intent_merge)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", intent_merge)
        self.assertNotIn("WHEN MATCHED", intent_merge)
        self.assertNotIn("UPDATE", intent_merge)
        self.assertNotIn("DELETE", intent_merge)
        self.assertEqual(
            ddl_columns_for("${catalog}.${schema}.${quarantine_intent_table}"),
            inserted_columns(intent_merge),
        )

        self.assertIn("summary.header_count = 1", intent_check)
        self.assertIn("expected.expected_unknown_label_count + 1", intent_check)
        self.assertIn("actual_unknown_set_digest", intent_check)
        self.assertIn("recomputed_content_digest", intent_check)
        self.assertIn("summary.content_mismatch_count = 0", intent_check)
        self.assertIn("summary.contract_mismatch_count = 0", intent_check)

        self.assertIn("WHEN NOT MATCHED THEN INSERT", target_merge)
        self.assertNotIn("WHEN MATCHED", target_merge)
        self.assertNotIn("UPDATE", target_merge)
        self.assertNotIn("INSERT *", target_merge)
        self.assertIn("quarantine_intent_table", target_merge)
        self.assertIn("source_assertions AS", target_merge)
        self.assertIn("summary.content_mismatch_count = 0", target_merge)
        self.assertIn("summary.actual_unknown_set_digest", target_merge)
        self.assertEqual(
            ddl_columns_for("${catalog}.${schema}.${quarantine_table}"),
            inserted_columns(target_merge),
        )
        for mutable_relation in [
            "staged_candidate_predictions",
            "staged_scoring_window_manifest",
            "staged_scoring_reconciliation",
            "versioned_expected_labels",
            "label_gate_results",
        ]:
            self.assertNotIn(mutable_relation, target_merge)
        for marker in [
            ":scoring_run_id",
            ":run_contract_digest",
            ":staged_candidate_delta_version",
            ":staged_candidate_snapshot_digest",
            ":expected_unknown_label_count",
            ":expected_unknown_label_set_digest",
        ]:
            self.assertIn(marker, target_merge)

        self.assertIn("reconciliation.header_count = 1", final_check)
        self.assertIn("expected_unknown_label_count", final_check)
        self.assertIn("expected_unknown_label_set_digest", final_check)
        self.assertIn("recomputed_content_digest", final_check)
        self.assertIn("reconciliation.intent_content_mismatch_count = 0", final_check)
        self.assertIn("missing_keys AS", final_check)
        self.assertIn("unexpected_keys AS", final_check)
        self.assertGreaterEqual(final_check.count("LEFT ANTI JOIN"), 2)
        self.assertIn("mismatched_rows AS", final_check)
        self.assertIn("reconciliation.missing_key_count = 0", final_check)
        self.assertIn("reconciliation.unexpected_key_count = 0", final_check)
        self.assertIn("reconciliation.mismatched_row_count = 0", final_check)

    def test_quarantine_toctou_uses_persisted_intent_after_materialization(
        self,
    ) -> None:
        gate = LABEL_GATE.read_text(encoding="utf-8")
        sql_blocks = re.findall(r"```sql\n(.*?)```", gate, re.DOTALL)
        intent_merge = next(
            block
            for block in sql_blocks
            if "MERGE INTO ${catalog}.${schema}.${quarantine_intent_table}" in block
        )
        target_merge = next(
            block
            for block in sql_blocks
            if "quarantine_table} AS quarantine_target" in block
        )
        final_check = next(
            block for block in sql_blocks if "quarantine_persistence_asserted" in block
        )
        self.assertIn("expected_unknown_label_set_digest", intent_merge)
        self.assertIn("WHEN NOT MATCHED", intent_merge)
        self.assertNotIn("WHEN MATCHED", intent_merge)
        for downstream in [target_merge, final_check]:
            self.assertIn("quarantine_intent_table", downstream)
            self.assertNotIn("staged_candidate_predictions", downstream)
            self.assertNotIn("versioned_expected_labels", downstream)
            self.assertNotIn("staged_scoring_window_manifest", downstream)
        self.assertIn("missing intent plus empty target", gate)
        self.assertIn("existing intent rows are neither updated nor deleted", gate)

    def test_quarantine_final_aggregate_nonempty_and_corrupt_relationally(
        self,
    ) -> None:
        run_digest = VALID_IDENTITY
        snapshot_digest = "b" * 64
        set_digest = VALID_UNKNOWN_SET_DIGEST
        database = sqlite3.connect(":memory:")
        try:
            database.executescript(
                """
                CREATE TABLE intent (
                    record_kind TEXT NOT NULL,
                    run_contract_digest TEXT NOT NULL,
                    staged_candidate_delta_version INTEGER NOT NULL,
                    staged_candidate_snapshot_digest TEXT NOT NULL,
                    expected_unknown_label_count INTEGER NOT NULL,
                    expected_unknown_label_set_digest TEXT NOT NULL
                );
                CREATE TABLE target (quarantine_row_id TEXT NOT NULL);
                """
            )
            database.executemany(
                "INSERT INTO intent VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("header", run_digest, 7, snapshot_digest, 1, set_digest),
                    (
                        "unknown_label",
                        run_digest,
                        7,
                        snapshot_digest,
                        1,
                        set_digest,
                    ),
                ],
            )
            database.execute("INSERT INTO target VALUES ('candidate-row')")

            def aggregate() -> tuple[int, int, int, int]:
                row = database.execute(
                    """
                    WITH expected AS (
                        SELECT ? AS run_contract_digest,
                               7 AS staged_candidate_delta_version,
                               ? AS staged_candidate_snapshot_digest,
                               1 AS expected_unknown_label_count,
                               ? AS expected_unknown_label_set_digest
                    ),
                    intent_aggregate AS (
                        SELECT
                            COUNT(CASE WHEN record_kind = 'header' THEN 1 END)
                                AS header_count,
                            COUNT(CASE WHEN record_kind = 'unknown_label' THEN 1 END)
                                AS candidate_count,
                            COALESCE(SUM(CASE WHEN
                                intent.run_contract_digest
                                    IS NOT expected.run_contract_digest
                                OR intent.staged_candidate_delta_version
                                    IS NOT expected.staged_candidate_delta_version
                                OR intent.staged_candidate_snapshot_digest
                                    IS NOT expected.staged_candidate_snapshot_digest
                                OR intent.expected_unknown_label_count
                                    IS NOT expected.expected_unknown_label_count
                                OR intent.expected_unknown_label_set_digest
                                    IS NOT expected.expected_unknown_label_set_digest
                                THEN 1 ELSE 0 END), 0) AS mismatch_count
                        FROM intent
                        CROSS JOIN expected
                    ),
                    target_aggregate AS (
                        SELECT COUNT(*) AS target_count FROM target
                    )
                    SELECT header_count, candidate_count, target_count, mismatch_count
                    FROM intent_aggregate
                    CROSS JOIN target_aggregate
                    """,
                    (run_digest, snapshot_digest, set_digest),
                ).fetchone()
                assert row is not None
                return row

            def passes(row: tuple[int, int, int, int]) -> bool:
                return row == (1, 1, 1, 0)

            valid = aggregate()
            self.assertEqual((1, 1, 1, 0), valid)
            self.assertTrue(passes(valid))
            database.execute(
                "UPDATE intent SET run_contract_digest = 'corrupt' "
                "WHERE record_kind = 'unknown_label'"
            )
            corrupted = aggregate()
            self.assertEqual((1, 1, 1), corrupted[:3])
            self.assertEqual(1, corrupted[3])
            self.assertFalse(passes(corrupted))
        finally:
            database.close()

        gate = LABEL_GATE.read_text(encoding="utf-8")
        final_check = next(
            block
            for block in re.findall(r"```sql\n(.*?)```", gate, re.DOTALL)
            if "quarantine_persistence_asserted" in block
        )
        self.assertIn("intent_aggregate AS", final_check)
        self.assertIn("target_aggregate AS", final_check)
        self.assertNotIn("FULL OUTER JOIN", final_check)

    def test_quarantine_business_key_assertion_matches_target_ddl(self) -> None:
        gate = LABEL_GATE.read_text(encoding="utf-8")
        ddl = QUARANTINE_DDL.read_text(encoding="utf-8")
        normalized_gate = " ".join(gate.split())
        normalized_ddl = " ".join(ddl.split())
        predicate_parts = [
            "business_key IS NOT NULL",
            "business_key = TRIM(business_key)",
            "LENGTH(business_key) BETWEEN 1 AND 512",
            "business_key RLIKE '^-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}$'",
        ]
        for part in predicate_parts:
            self.assertIn(part, normalized_gate)
            self.assertIn(part, normalized_ddl)
        for invalid_nonnull in ["", " ", " leading", "trailing ", "a b", "é"]:
            with self.subTest(value=repr(invalid_nonnull)):
                self.assertIsNone(
                    BUSINESS_KEY_MODULE.BUSINESS_KEY_PATTERN.fullmatch(invalid_nonnull)
                )
        self.assertIsNone(BUSINESS_KEY_MODULE.BUSINESS_KEY_PATTERN.fullmatch("a" * 513))

    def test_quarantine_gate_digest_and_intent_identity_are_exact(self) -> None:
        gate = LABEL_GATE.read_text(encoding="utf-8")
        sql_blocks = re.findall(r"```sql\n(.*?)```", gate, re.DOTALL)
        intent_merge = next(
            block
            for block in sql_blocks
            if "MERGE INTO ${catalog}.${schema}.${quarantine_intent_table}" in block
        )
        publication_gate = next(
            block for block in sql_blocks if "unknown.unknown_label_set_digest" in block
        )
        for cte_name in ["unknown_identified", "unknown_prepared"]:
            self.assertEqual(
                hashlib.sha256(
                    cte_body(intent_merge, cte_name).encode("utf-8")
                ).hexdigest(),
                hashlib.sha256(
                    cte_body(publication_gate, cte_name).encode("utf-8")
                ).hexdigest(),
                cte_name,
            )
        self.assertIn("COUNT(*) = COUNT(DISTINCT intent_record_id)", intent_merge)
        self.assertIn(
            "CONCAT('candidate:', scoring_run_id, ':', business_key)",
            intent_merge,
        )
        self.assertIn("CONCAT('header:', params.scoring_run_id)", intent_merge)

        run_id = VALID_IDENTITY
        exact_business_key = "quarantine_intent_header"
        header_id = hashlib.sha256(f"header:{run_id}".encode()).hexdigest()
        candidate_id = hashlib.sha256(
            f"candidate:{run_id}:{exact_business_key}".encode()
        ).hexdigest()
        self.assertNotEqual(header_id, candidate_id)
        self.assertRegex(header_id, r"^[0-9a-f]{64}$")
        self.assertRegex(candidate_id, r"^[0-9a-f]{64}$")

    def test_candidate_content_json_options_are_identical_and_timezone_stable(
        self,
    ) -> None:
        gate = LABEL_GATE.read_text(encoding="utf-8")
        sql = "\n".join(re.findall(r"```sql\n(.*?)```", gate, re.DOTALL))
        normalized = " ".join(sql.split())
        canonical_options = (
            "MAP( 'timeZone', 'UTC', 'timestampFormat', "
            "'yyyy-MM-dd''T''HH:mm:ss.SSSSSSXXX' )"
        )
        self.assertEqual(5, sql.count("NAMED_STRUCT("))
        self.assertEqual(5, normalized.count(canonical_options))
        self.assertEqual(5, sql.count("'timeZone', 'UTC'"))
        self.assertEqual(5, sql.count("'timestampFormat'"))

        def canonical_timestamp(value: datetime) -> str:
            return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        utc_value = datetime(
            2026,
            8,
            31,
            12,
            34,
            56,
            123456,
            tzinfo=timezone.utc,
        )
        eastern_session_value = utc_value.astimezone(timezone(timedelta(hours=-4)))
        self.assertNotEqual(utc_value.isoformat(), eastern_session_value.isoformat())
        utc_preimage = json.dumps(
            {"observation_timestamp": canonical_timestamp(utc_value)},
            separators=(",", ":"),
        )
        eastern_preimage = json.dumps(
            {"observation_timestamp": canonical_timestamp(eastern_session_value)},
            separators=(",", ":"),
        )
        self.assertEqual(utc_preimage, eastern_preimage)
        self.assertEqual(
            hashlib.sha256(utc_preimage.encode()).hexdigest(),
            hashlib.sha256(eastern_preimage.encode()).hexdigest(),
        )

    def test_quarantine_assets_use_lf_only(self) -> None:
        for path in [
            SKILL_ROOT / "SKILL.md",
            LABEL_GATE,
            QUARANTINE_DDL,
            DECISION_SCRIPT,
            Path(__file__),
        ]:
            with self.subTest(path=path.name):
                self.assertNotIn(b"\r", path.read_bytes())

    def test_merge_column_list_matches_prediction_ddl_and_required_fields(self) -> None:
        ddl_order, ddl_required = ddl_columns(SCORING_DDL)
        merge_columns = list(
            markdown_literal_assignment(SAFE_WRITES, "PREDICTION_COLUMNS")
        )
        merge_required = set(
            markdown_literal_assignment(SAFE_WRITES, "REQUIRED_PREDICTION_COLUMNS")
        )
        self.assertEqual(ddl_order, merge_columns)
        self.assertEqual(ddl_required, merge_required)
        contract = SAFE_WRITES.read_text(encoding="utf-8")
        self.assertIn(
            "source = staged_candidate_df.select(*PREDICTION_COLUMNS)", contract
        )
        self.assertIn("source.where(missing_required)", contract)

    def test_privilege_and_lineage_inspection_matrix_is_read_only_and_complete(
        self,
    ) -> None:
        contract = MODEL_LOADING.read_text(encoding="utf-8")
        for privilege in [
            "`SELECT`",
            "`MODIFY`",
            "`EXECUTE`",
            "`USE CATALOG`",
            "`USE SCHEMA`",
            "`READ FEATURE`",
        ]:
            self.assertIn(privilege, contract)
        self.assertIn("SHOW GRANTS ON <object>", contract)
        self.assertIn("Do not add grants as part of a scoring notebook", contract)
        self.assertIn("system.access.table_lineage", contract)
        self.assertIn(
            "read the immutable model's packaged feature specification", contract
        )
        self.assertIn(
            "A model-level `EXECUTE` grant does not imply feature-data access", contract
        )
        self.assertIn("Every packaged feature table or view", contract)
        self.assertIn("Every first-class Feature or Feature View entity", contract)


if __name__ == "__main__":
    unittest.main()
