"""Behavioral coverage for the bounded Spark static auditor."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "audit_spark_antipatterns.py"
SPARK_PREAMBLE = "from pyspark.sql import SparkSession\nspark = SparkSession.builder.getOrCreate()\ndf = spark.table('input')\n"
SPEC = importlib.util.spec_from_file_location("spark_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class SparkAuditTests(unittest.TestCase):
    """Exercise the public report contract through its command-line entry point."""

    def run_audit(self, target: Path, *options: str) -> tuple[int, dict[str, object], str]:
        """Run the CLI and parse its one JSON stdout document."""
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), str(target), *options],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return completed.returncode, json.loads(completed.stdout), completed.stderr

    @staticmethod
    def patterns(report: dict[str, object]) -> set[str]:
        """Return finding identifiers from one report."""
        findings = report["findings"]
        assert isinstance(findings, list)
        return {str(finding["pattern"]) for finding in findings if isinstance(finding, dict)}

    @staticmethod
    def diagnostic_codes(report: dict[str, object]) -> set[str]:
        """Return value-free diagnostic codes from one report."""
        diagnostics = report["diagnostics"]
        assert isinstance(diagnostics, list)
        return {str(item["code"]) for item in diagnostics if isinstance(item, dict)}

    def test_schema_complete_and_exit_zero_with_findings(self) -> None:
        """Findings are successful completion, not process failure."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "job.py").write_text(SPARK_PREAMBLE + "df.collect()\n", encoding="utf-8")
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(report["schema"], 1)
        self.assertTrue(report["complete"])
        self.assertEqual(report["root"], ".")
        self.assertEqual(report["summary"], {"discovered": 1, "scanned": 1, "skipped": 0, "findings": 1})
        self.assertEqual(self.patterns(report), {"collect_call"})
        self.assertNotIn("excerpt", report["findings"][0])

    def test_malformed_and_utf8_files_do_not_stop_valid_neighbor(self) -> None:
        """Per-file parsing failures become value-free diagnostics and scanning continues."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "good.PY").write_text(SPARK_PREAMBLE + "df.cache()\n", encoding="utf-8")
            (root / "bad.ipynb").write_text("{bad", encoding="utf-8")
            (root / "bytes.sql").write_bytes(b"SELECT \xff")
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 1, stderr)
        self.assertFalse(report["complete"])
        self.assertIn("serverless_df_cache", self.patterns(report))
        self.assertTrue({"notebook_json_error", "utf8_error"}.issubset(self.diagnostic_codes(report)))
        serialized = json.dumps(report)
        self.assertNotIn("UnicodeDecodeError", serialized)
        self.assertNotIn("{bad", serialized)

    def test_permission_and_notebook_schema_failures_are_value_free(self) -> None:
        """Locked reads and malformed cell schemas do not expose OS error values."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            locked = root / "locked.py"
            locked.write_text("df.collect()\n", encoding="utf-8")
            malformed = root / "schema.ipynb"
            malformed.write_text(json.dumps({"cells": [{"cell_type": "code", "source": 1}]}), encoding="utf-8")
            audit = AUDIT.Audit(
                AUDIT.Caps(10, 1024, 4096, 10, 1024, 1024, 10, 10, 10, 100),
                include_excerpts=False,
            )
            with patch.object(AUDIT, "_open_nofollow", side_effect=PermissionError("private lock details")):
                AUDIT.process_file(locked, root, audit)
            code, report, stderr = self.run_audit(root)
        self.assertIn("permission_error", {item["code"] for item in audit.diagnostics})
        self.assertNotIn("private lock details", json.dumps(audit.report()))
        self.assertEqual(code, 1, stderr)
        self.assertIn("notebook_schema_error", self.diagnostic_codes(report))

    def test_notebook_cells_are_mapped_and_limited(self) -> None:
        """Notebook code-cell IDs and line numbers stay deterministic under a cap."""
        notebook = {
            "cells": [
                {"cell_type": "markdown", "source": ["ignored"]},
                {"cell_type": "code", "source": [SPARK_PREAMBLE, "df.rdd.isEmpty()\n"]},
                {"cell_type": "code", "source": ["df.collect()\n"]},
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "NOTEBOOK.IPYNB").write_text(json.dumps(notebook), encoding="utf-8")
            code, report, stderr = self.run_audit(root, "--max-notebook-cells", "1")
        self.assertEqual(code, 1, stderr)
        finding = report["findings"][0]
        self.assertEqual(finding["file"], "NOTEBOOK.IPYNB")
        self.assertEqual(finding["cell"], 1)
        self.assertEqual(finding["line"], 4)
        self.assertEqual(finding["pattern"], "serverless_rdd_is_empty")
        self.assertIn("notebook_cells_cap_reached", self.diagnostic_codes(report))

    def test_resource_caps_are_explicit_and_incomplete(self) -> None:
        """File, total-byte, finding, and diagnostic caps stop or skip safely."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text(SPARK_PREAMBLE + "df.collect()\ndf.count()\n", encoding="utf-8")
            (root / "b.py").write_text(SPARK_PREAMBLE + "df.cache()\n", encoding="utf-8")
            file_code, file_report, _ = self.run_audit(root, "--max-files", "1")
            finding_code, finding_report, _ = self.run_audit(root / "a.py", "--max-findings", "1")
            byte_code, byte_report, _ = self.run_audit(root, "--max-total-bytes", "1")
            diagnostic_code, diagnostic_report, _ = self.run_audit(
                root,
                "--max-file-bytes",
                "1",
                "--max-diagnostics",
                "1",
            )
        self.assertEqual(file_code, 1)
        self.assertIn("file_count_cap_reached", self.diagnostic_codes(file_report))
        self.assertEqual(finding_code, 1)
        self.assertIn("findings_cap_reached", self.diagnostic_codes(finding_report))
        self.assertEqual(byte_code, 1)
        self.assertIn("total_bytes_cap_reached", self.diagnostic_codes(byte_report))
        self.assertEqual(diagnostic_code, 1)
        self.assertEqual(len(diagnostic_report["diagnostics"]), 1)
        self.assertIn("diagnostics_cap_reached", self.diagnostic_codes(diagnostic_report))

    def test_symlink_is_skipped_and_direct_symlink_is_rejected(self) -> None:
        """Traversal never follows links, junctions, or other reparse points."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root.parent / "outside-spark-audit.py"
            outside.write_text("df.collect()\n", encoding="utf-8")
            link = root / "linked.py"
            try:
                os.symlink(outside, link)
            except (NotImplementedError, OSError):
                self.skipTest("symlink creation is unavailable on this host")
            directory_code, directory_report, _ = self.run_audit(root)
            direct_code, direct_report, _ = self.run_audit(link)
            outside.unlink(missing_ok=True)
        self.assertEqual(directory_code, 1)
        self.assertEqual(directory_report["summary"]["findings"], 0)
        self.assertIn("reparse_or_symlink_rejected", self.diagnostic_codes(directory_report))
        self.assertEqual(direct_code, 2)
        self.assertIn("invalid_target_reparse_or_symlink", self.diagnostic_codes(direct_report))

    def test_excerpts_are_opt_in_bounded_and_redacted(self) -> None:
        """Source sentinels never appear by default or after recursive excerpt redaction."""
        sentinel = "unit-test-private-sentinel"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "job.py"
            source.write_text(f"df.collect()  # token={sentinel}\n", encoding="utf-8")
            default_code, default_report, _ = self.run_audit(source)
            excerpt_code, excerpt_report, _ = self.run_audit(source, "--excerpts", "--max-excerpt-chars", "30")
        self.assertEqual(default_code, 0)
        self.assertNotIn(sentinel, json.dumps(default_report))
        self.assertEqual(excerpt_code, 0)
        self.assertNotIn(sentinel, json.dumps(excerpt_report))
        excerpt = excerpt_report["findings"][0]["excerpt"]
        self.assertIn("[REDACTED]", excerpt)
        self.assertLessEqual(len(excerpt), 30)

    def test_creation_order_does_not_change_report_order(self) -> None:
        """Traversal and output are path, cell, line, and pattern deterministic."""
        contents = {"z.py": SPARK_PREAMBLE + "df.cache()\n", "a.py": SPARK_PREAMBLE + "df.collect()\ndf.count()\n"}
        reports: list[dict[str, object]] = []
        for order in (list(contents.items()), list(reversed(list(contents.items())))):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name, text in order:
                    (root / name).write_text(text, encoding="utf-8")
                code, report, stderr = self.run_audit(root)
                self.assertEqual(code, 0, stderr)
                reports.append(report)
        self.assertEqual(reports[0], reports[1])

    def test_comments_strings_fences_and_sql_literals_are_not_findings(self) -> None:
        """Lexical masking prevents documentation and literal examples becoming evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "examples.py").write_text(
                '# df.collect()\nexample = "df.cache()"\nliteral = """```python\ndf.rdd\n```"""\n',
                encoding="utf-8",
            )
            (root / "examples.sql").write_text("-- CACHE TABLE x\nSELECT 'REFRESH TABLE x';\n/* CLEAR CACHE */\nPERSIST TABLE legacy;\n", encoding="utf-8")
            (root / "examples.yaml").write_text("notes: |\n  num_workers: 0\n  node_type_id: g5.4xlarge\n", encoding="utf-8")
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(report["findings"], [])

    def test_current_serverless_cache_rdd_and_sql_cache_positives(self) -> None:
        """Current unsupported serverless cache and RDD APIs are detected."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "job.py").write_text(
                SPARK_PREAMBLE + "df.cache()\ndf.persist()\ndf.unpersist()\ndf.checkpoint()\nspark.catalog.cacheTable('x')\ndf.rdd\n",
                encoding="utf-8",
            )
            (root / "cache.sql").write_text("CACHE TABLE x;\nUNCACHE TABLE x;\nREFRESH TABLE x;\nCLEAR CACHE;\n", encoding="utf-8")
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(
            {
                "serverless_df_cache",
                "serverless_df_persist",
                "serverless_df_unpersist",
                "serverless_df_checkpoint",
                "serverless_catalog_cache_table",
                "serverless_rdd_access",
                "serverless_sql_cache_table",
                "serverless_sql_uncache_table",
                "serverless_sql_refresh_table",
                "serverless_sql_clear_cache",
            }.issubset(self.patterns(report))
        )

    def test_dbfs_reserves_are_not_reported(self) -> None:
        """Only DBFS root and mounts, never documented reserved prefixes, are findings."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "paths.py"
            source.write_text(
                "df.write.option('path', 'dbfs:/tmp/output').parquet('x')\n"
                "df.write.parquet('dbfs:/Volumes/main/default/vol')\n"
                "df.write.parquet('dbfs:/databricks-datasets/demo')\n"
                "df.write.parquet('dbfs:/databricks/mlflow-tracking/run')\n",
                encoding="utf-8",
            )
            code, report, stderr = self.run_audit(source)
        self.assertEqual(code, 0, stderr)
        dbfs = [item for item in report["findings"] if item["pattern"] == "dbfs_root_path"]
        self.assertEqual(len(dbfs), 1)
        self.assertEqual(dbfs[0]["line"], 1)

    def test_actions_broadcast_and_configuration_severity_are_calibrated(self) -> None:
        """Action, output, broadcast, GPU, and worker checks preserve their evidence limits."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "job.py").write_text(
                "import sys\n" + SPARK_PREAMBLE + "sc = spark.sparkContext\ndf.count()\nsys.exit(1)\nsc.broadcast(value)\ndf.show()\ndf.limit(3).show()\ndf.show(5000)\n",
                encoding="utf-8",
            )
            (root / "unknown.yaml").write_text(
                "resources:\n  jobs:\n    unknown:\n      tasks:\n        - python_wheel_task: {}\n      job_clusters:\n        - new_cluster:\n            num_workers: 0\n            node_type_id: g5.4xlarge\n",
                encoding="utf-8",
            )
            (root / "spark.yaml").write_text(
                "resources:\n  jobs:\n    spark_job:\n      tasks:\n        - spark_python_task: {}\n      job_clusters:\n        - new_cluster:\n            num_workers: 0\n            node_type_id: g5.12xlarge\n        - new_cluster:\n            num_workers: 1\n            node_type_id: g5.12xlarge\n",
                encoding="utf-8",
            )
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 0, stderr)
        self.assertTrue({"count_action", "sys_exit", "broadcast_usage", "show_high_output"}.issubset(self.patterns(report)))
        self.assertEqual(sum(1 for item in report["findings"] if item["pattern"] == "show_high_output"), 1)
        broadcast = next(item for item in report["findings"] if item["pattern"] == "broadcast_usage")
        self.assertNotIn("length", broadcast["message"].casefold())
        workers = [item for item in report["findings"] if item["pattern"] == "dab_num_workers_zero"]
        self.assertEqual({item["severity"] for item in workers}, {"low", "medium"})
        gpus = [item for item in report["findings"] if item["pattern"] == "dab_gpu_node_type"]
        self.assertEqual({item["severity"] for item in gpus}, {"low", "medium"})

    def test_invalid_target_and_arguments_exit_two(self) -> None:
        """Invalid direct targets and invalid caps use the contract exit code."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsupported = root / "readme.txt"
            unsupported.write_text("not source", encoding="utf-8")
            target_code, target_report, _ = self.run_audit(unsupported)
            cap_code, cap_report, cap_stderr = self.run_audit(unsupported, "--max-files", "0")
        self.assertEqual(target_code, 2)
        self.assertIn("unsupported_direct_target", self.diagnostic_codes(target_report))
        self.assertEqual(cap_code, 2)
        self.assertIn("invalid_arguments", self.diagnostic_codes(cap_report))
        self.assertEqual(cap_stderr, "")

    def test_notebook_event_parser_caps_120k_cells_below_peak_budget(self) -> None:
        """A huge cells array stops at the code-cell cap without JSON-object expansion."""
        with tempfile.TemporaryDirectory() as temporary:
            notebook = Path(temporary) / "many.ipynb"
            with notebook.open("wb") as handle:
                handle.write(b'{"cells":[')
                for index in range(120_000):
                    if index:
                        handle.write(b",")
                    handle.write(b'{"cell_type":"code","source":"x = 1\\n"}')
                handle.write(b"]}")
            audit = AUDIT.Audit(
                AUDIT.Caps(2, 12 * 1024 * 1024, 12 * 1024 * 1024, 1, 1024, 1_000_000, 10, 10, 10, 80),
                include_excerpts=False,
            )
            tracemalloc.start()
            report, code = AUDIT.scan_target(notebook, audit)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        self.assertEqual(code, 1)
        self.assertIn("notebook_cells_cap_reached", self.diagnostic_codes(report))
        self.assertLess(peak, 24 * 1024 * 1024)

    def test_notebook_node_and_cell_byte_caps(self) -> None:
        """Node and cell-byte budgets fail before retaining an oversized cell object."""
        notebook = {"cells": [{"cell_type": "code", "source": "x = '" + ("a" * 256) + "'\n"}]}
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bounded.ipynb"
            source.write_text(json.dumps(notebook), encoding="utf-8")
            node_code, node_report, _ = self.run_audit(source, "--max-notebook-nodes", "1")
            byte_code, byte_report, _ = self.run_audit(source, "--max-notebook-cell-bytes", "32")
        self.assertEqual(node_code, 1)
        self.assertIn("notebook_nodes_cap_reached", self.diagnostic_codes(node_report))
        self.assertEqual(byte_code, 1)
        self.assertIn("notebook_cell_bytes_cap_reached", self.diagnostic_codes(byte_report))

    def test_notebook_parser_validates_tail_members_and_eof(self) -> None:
        """Cells-array completion is not acceptance until the whole top-level JSON object closes."""
        cases = {
            "trailing.ipynb": b'{"cells":[] trailing}',
            "unterminated.ipynb": b'{"cells":[]',
            "garbage.ipynb": b'{"cells":[]} garbage',
            "bad-member.ipynb": b'{"cells":[],"metadata":tru}',
            "duplicate.ipynb": b'{"cells":[],"cells":[]}',
            "extra.ipynb": b'{"cells":[],"metadata":{"kernelspec":{"name":"python"}}}',
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in cases.items():
                (root / name).write_bytes(content)
            code, report, stderr = self.run_audit(root)
        self.assertEqual(code, 1, stderr)
        by_file = {item["file"]: item["code"] for item in report["diagnostics"]}
        self.assertEqual(by_file["duplicate.ipynb"], "notebook_duplicate_cells")
        for name in ("trailing.ipynb", "unterminated.ipynb", "garbage.ipynb", "bad-member.ipynb"):
            self.assertEqual(by_file[name], "notebook_json_error")
        self.assertNotIn("extra.ipynb", by_file)

    def test_notebook_malformed_cell_stays_schema_safe(self) -> None:
        """A syntactically loose event walk cannot let a malformed cell escape the JSON contract."""
        malformed = b'{"cells":[{"x":}]}'
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "malformed-cell.ipynb"
            source.write_bytes(malformed)
            audit = AUDIT.Audit(AUDIT.Caps(10, 1024 * 1024, 1024 * 1024, 10, 1024, 1024, 10, 10, 10, 80), False)
            units = list(AUDIT.notebook_units(malformed, "malformed-cell.ipynb", audit))
            code, report, stderr = self.run_audit(source)
        self.assertEqual(units, [])
        self.assertIn("notebook_json_error", self.diagnostic_codes(audit.report()))
        self.assertEqual(code, 1, stderr)
        self.assertIn("notebook_json_error", self.diagnostic_codes(report))

    def test_member_findings_require_provenance(self) -> None:
        """Unrelated methods become low-confidence leads while evidenced DataFrames stay authoritative."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "provenance.py"
            source.write_text(
                "http_response.cache()\ncustom_result.collect()\n" + SPARK_PREAMBLE + "df.cache()\ndf.collect()\n",
                encoding="utf-8",
            )
            code, report, stderr = self.run_audit(source)
        self.assertEqual(code, 0, stderr)
        findings = report["findings"]
        unknown = [item for item in findings if item["line"] in {1, 2}]
        self.assertEqual({item["severity"] for item in unknown}, {"low"})
        self.assertEqual({item["pattern"] for item in unknown}, {"possible_serverless_df_cache", "possible_collect_call"})
        asserted = [item for item in findings if item["line"] in {6, 7}]
        self.assertEqual({item["pattern"] for item in asserted}, {"serverless_df_cache", "collect_call"})
        self.assertEqual({item["severity"] for item in asserted}, {"high"})

    def test_notebook_sql_magics_and_unsupported_magic(self) -> None:
        """Databricks SQL magics use SQL lexical rules and non-SQL magics diagnose safely."""
        notebook = {
            "cells": [
                {"cell_type": "code", "source": ["%%sql\nCACHE TABLE stage;\n"]},
                {"cell_type": "code", "source": ["%sql SELECT current_date();\n"]},
                {"cell_type": "code", "source": ["%%sh\necho unsafe\n"]},
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "magic.ipynb"
            source.write_text(json.dumps(notebook), encoding="utf-8")
            code, report, stderr = self.run_audit(source)
        self.assertEqual(code, 1, stderr)
        self.assertTrue({"serverless_sql_cache_table", "sql_wall_clock_window"}.issubset(self.patterns(report)))
        self.assertIn("unsupported_notebook_magic", self.diagnostic_codes(report))
        self.assertNotIn("python_tokenize_error", self.diagnostic_codes(report))

    def test_yaml_configuration_does_not_cross_job_boundaries(self) -> None:
        """Spark context in one job does not increase another job cluster's severity."""
        config = """resources:
  jobs:
    spark_job:
      tasks:
        - spark_python_task: {}
    application_job:
      tasks:
        - python_wheel_task: {}
      job_clusters:
        - new_cluster:
            num_workers: 0
            node_type_id: g5.4xlarge
"""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "jobs.yaml"
            source.write_text(config, encoding="utf-8")
            code, report, stderr = self.run_audit(source)
        self.assertEqual(code, 0, stderr)
        scoped = [item for item in report["findings"] if item["pattern"] in {"dab_num_workers_zero", "dab_gpu_node_type"}]
        self.assertEqual({item["severity"] for item in scoped}, {"low"})

    def test_nofollow_race_and_capability_fail_closed(self) -> None:
        """Open failures after discovery are classified as races or unavailable safety capability."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "race.py"
            source.write_text(SPARK_PREAMBLE + "df.collect()\n", encoding="utf-8")
            race_audit = AUDIT.Audit(AUDIT.Caps(10, 1024, 4096, 10, 1024, 1024, 10, 10, 10, 80), False)
            with patch.object(AUDIT, "_open_nofollow", side_effect=FileNotFoundError):
                AUDIT.process_file(source, root, race_audit)
            capability_audit = AUDIT.Audit(AUDIT.Caps(10, 1024, 4096, 10, 1024, 1024, 10, 10, 10, 80), False)
            with patch.object(AUDIT, "_open_nofollow", side_effect=AUDIT.NofollowCapabilityError):
                AUDIT.process_file(source, root, capability_audit)
        self.assertIn("source_race_rejected", self.diagnostic_codes(race_audit.report()))
        self.assertIn("nofollow_capability_unavailable", self.diagnostic_codes(capability_audit.report()))

    def test_nofollow_reader_fstats_the_open_handle(self) -> None:
        """The production source reader validates the descriptor it actually opened."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "regular.py"
            source.write_text("x = 1\n", encoding="utf-8")
            descriptor = AUDIT._open_nofollow(source)
            try:
                opened = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        self.assertTrue(AUDIT.stat.S_ISREG(opened.st_mode))

    @unittest.skipUnless(os.name != "nt" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "requires POSIX openat")
    def test_descriptor_anchored_read_survives_parent_swap(self) -> None:
        """An already-open child directory cannot be redirected by replacing its path with a symlink."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inside = root / "inside"
            outside = root / "outside"
            moved = root / "moved"
            inside.mkdir()
            outside.mkdir()
            (inside / "job.py").write_text(SPARK_PREAMBLE + "df.collect()\n", encoding="utf-8")
            (outside / "job.py").write_text(SPARK_PREAMBLE + "df.cache()\n", encoding="utf-8")
            root_fd = AUDIT._open_directory_nofollow(root)
            child_fd = AUDIT._open_directory_nofollow("inside", root_fd)
            try:
                os.rename(inside, moved)
                os.symlink(outside, inside)
                audit = AUDIT.Audit(AUDIT.Caps(10, 1024 * 1024, 1024 * 1024, 10, 1024, 1024, 10, 10, 10, 80), False)
                AUDIT.process_file("job.py", root, audit, child_fd, "inside/job.py")
            finally:
                os.close(child_fd)
                os.close(root_fd)
        patterns = {finding.pattern for finding in audit.findings}
        self.assertIn("collect_call", patterns)
        self.assertNotIn("serverless_df_cache", patterns)

    @unittest.skipUnless(os.name == "nt", "requires Windows final-handle paths")
    def test_windows_final_handle_escape_is_rejected(self) -> None:
        """Windows content is rejected if its opened final-handle path escapes the canonical root."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "job.py"
            source.write_text(SPARK_PREAMBLE + "df.collect()\n", encoding="utf-8")
            audit = AUDIT.Audit(AUDIT.Caps(10, 1024 * 1024, 1024 * 1024, 10, 1024, 1024, 10, 10, 10, 80), False)
            audit.canonical_root = str(root)
            with patch.object(AUDIT, "_windows_final_handle_path", return_value=AUDIT._normalize_windows_handle_path(str(root.parent))):
                AUDIT.process_file(source, root, audit)
        self.assertIn("handle_containment_rejected", self.diagnostic_codes(audit.report()))

    def test_excerpts_redact_modern_credential_shapes(self) -> None:
        """Explicit excerpts redact provider, cloud, JWT, auth, and URL credential forms."""
        openai_key = "sk-" + "redactionexample123"
        aws_key = "AKIA" + "ABCDEFGHIJKLMNOP"
        databricks_token = "dapi" + "redactionexample123"
        jwt = "eyJ" + "abcdefghi" + "." + "abcdefghijk" + "." + "abcdefghijkl"
        bearer = "bearer-" + "redaction-example"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "secrets.py"
            source.write_text(
                SPARK_PREAMBLE + f"df.collect()  # {openai_key} {aws_key} {databricks_token} {jwt} Authorization: Bearer {bearer} https://alice:password@example.test\n",
                encoding="utf-8",
            )
            code, report, stderr = self.run_audit(source, "--excerpts", "--max-excerpt-chars", "400")
        self.assertEqual(code, 0, stderr)
        rendered = json.dumps(report)
        for value in (openai_key, aws_key, databricks_token, jwt, bearer, "alice:password"):
            self.assertNotIn(value, rendered)
        self.assertIn("[REDACTED]", rendered)


if __name__ == "__main__":
    unittest.main()
