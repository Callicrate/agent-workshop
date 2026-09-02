"""Regression coverage for bounded local DAB path validation."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from subprocess import run
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_bundle.py"
DOCTOR = Path(__file__).resolve().parents[1] / "scripts" / "dab_doctor.mjs"
SPEC = importlib.util.spec_from_file_location("validate_bundle", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class PathSafetyTests(unittest.TestCase):
    """Exercise the no-probe classifier and source-root containment boundary."""

    def make_roots(self, root: Path, *extra: Path) -> object:
        lexical = tuple(Path(os.path.abspath(path)) for path in (root, *extra))
        canonical = tuple(path.resolve(strict=False) for path in (root, *extra))
        return VALIDATOR.LocalSourceRoots(lexical, canonical)

    def write_bundle(self, root: Path, body: str) -> Path:
        bundle = root / "databricks.yml"
        bundle.write_text(body, encoding="utf-8")
        return bundle

    def test_local_file_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file_path = root / "notebooks" / "ok.ipynb"
            file_path.parent.mkdir()
            file_path.write_text("{}", encoding="utf-8")
            roots = self.make_roots(root)
            errors, resolved = VALIDATOR.validate_task_file_reference(
                root, roots, "notebooks/ok.ipynb", "field", "Notebook", VALIDATOR.load_local_path_policy(), "databricks.yml"
            )
            self.assertEqual([], errors)
            self.assertEqual(file_path, resolved)
            errors, _ = VALIDATOR.validate_task_file_reference(
                root, roots, "notebooks/missing.ipynb", "field", "Notebook", VALIDATOR.load_local_path_policy(), "databricks.yml"
            )
            self.assertTrue(any("missing or not a regular file" in error.message for error in errors))

    def test_unsynced_escape_and_host_paths_do_not_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir()
            roots = self.make_roots(root)
            policy = VALIDATOR.load_local_path_policy()
            with patch.object(Path, "exists", side_effect=AssertionError("must not probe")):
                errors, _ = VALIDATOR.validate_task_file_reference(
                    root, roots, "../outside/secret.ipynb", "field", "Notebook", policy, "databricks.yml"
                )
                self.assertTrue(any("outside the bundle root" in error.message for error in errors))
                errors, _ = VALIDATOR.validate_task_file_reference(
                    root, roots, r"C:\\host\\secret.ipynb", "field", "Notebook", policy, "databricks.yml"
                )
                self.assertTrue(any("host-specific" in error.message for error in errors))
                for unsafe_reference in ("CON", "AUX.txt", "COM1.log", "LPT9.", "file.py:ads"):
                    errors, _ = VALIDATOR.validate_task_file_reference(
                        root, roots, unsafe_reference, "field", "Notebook", policy, "databricks.yml"
                    )
                    self.assertTrue(any("host-specific" in error.message for error in errors), unsafe_reference)

    def test_declared_parent_sync_root_allows_relative_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "bundle"
            shared = workspace / "shared"
            root.mkdir()
            shared.mkdir()
            (shared / "task.py").write_text("print('ok')", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\nsync:\n  paths:\n    - ../shared\ntargets:\n  dev:\n    default: true\nresources:\n  jobs:\n    task:\n      name: task\n      tasks:\n        - task_key: task\n          spark_python_task:\n            python_file: ../shared/task.py\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            self.assertFalse(any("outside the bundle root" in message for message in messages))

    def test_reparse_escape_rejected_and_declared_root_symlink_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "bundle"
            outside = workspace / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "task.py").write_text("print('ok')", encoding="utf-8")
            escape = root / "escape"
            shared = root / "shared"
            try:
                os.symlink(outside, escape, target_is_directory=True)
                os.symlink(outside, shared, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            policy = VALIDATOR.load_local_path_policy()
            errors, _ = VALIDATOR.validate_task_file_reference(
                root, self.make_roots(root), "escape/task.py", "field", "Python file", policy, "databricks.yml"
            )
            self.assertTrue(any("reparse point" in error.message for error in errors))
            errors, resolved = VALIDATOR.validate_task_file_reference(
                root, self.make_roots(root, shared), "shared/task.py", "field", "Python file", policy, "databricks.yml"
            )
            self.assertEqual([], errors)
            self.assertEqual(outside / "task.py", resolved)

    def test_include_and_yaml_guards_redact_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - ../outside.yml\n  - https://token.example.invalid/config.yml\ntargets:\n  dev:\n    default: true\n""",
            )
            success, messages = VALIDATOR.validate_bundle(bundle)
            self.assertFalse(success)
            joined = "\n".join(messages)
            self.assertNotIn("token.example", joined)
            self.assertIn("cannot escape", joined)
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertNotIn("token.example", doctor.stdout)

            self.write_bundle(root, "bundle: &x\n  name: test\ntargets:\n  dev: *x\n")
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("anchors are not allowed", "\n".join(messages))

            self.write_bundle(root, "bundle:\n  name: one\n  name: two\ntargets:\n  dev: {}\n")
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("duplicate keys", "\n".join(messages))

            self.write_bundle(root, "<<: {name: test}\ntargets:\n  dev: {}\n")
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("merge keys", "\n".join(messages))

    def test_include_match_and_yaml_depth_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "resources"
            resources.mkdir()
            for index in range(65):
                (resources / f"resource-{index:03d}.yml").write_text("variables: {}\n", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - resources/*.yml\ntargets:\n  dev:\n    default: true\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("match limit", "\n".join(messages))

            nested = "value: final\n"
            for index in range(65):
                nested = f"level_{index}:\n  " + nested.replace("\n", "\n  ").rstrip() + "\n"
            self.write_bundle(root, nested)
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("nesting exceeds", "\n".join(messages))

    def test_yaml_file_and_aggregate_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root_file = self.write_bundle(root, "bundle:\n  name: test\ntargets:\n  dev: {}\n")
            policy = VALIDATOR.load_local_path_policy()
            tiny_file_policy = VALIDATOR.LocalPathPolicy(
                {**policy.limits, "max_yaml_file_bytes": 8}, policy.dynamic_markers, policy.remote_prefixes, policy.windows_reserved_device_components
            )
            with self.assertRaisesRegex(ValueError, "per-file size limit"):
                VALIDATOR.load_yaml_mapping(root_file, tiny_file_policy, "databricks.yml")

            (root / "part.yml").write_text("variables: {}\n", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - part.yml\ntargets:\n  dev: {}\n""",
            )
            tiny_aggregate_policy = VALIDATOR.LocalPathPolicy(
                {**policy.limits, "max_yaml_aggregate_bytes": 1}, policy.dynamic_markers, policy.remote_prefixes, policy.windows_reserved_device_components
            )
            with patch.object(VALIDATOR, "load_local_path_policy", return_value=tiny_aggregate_policy):
                success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("aggregate size limit", "\n".join(messages))

    def test_doctor_rejects_aliases_and_include_match_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_bundle(root, "bundle: &x\n  name: test\ntargets:\n  dev: *x\n")
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertIn("aliases", json.loads(doctor.stdout)["findings"][0]["message"])

            resources = root / "resources"
            resources.mkdir()
            for index in range(65):
                (resources / f"resource-{index:03d}.yml").write_text("variables: {}\n", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - resources/*.yml\ntargets:\n  dev:\n    default: true\n""",
            )
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertIn("match limit", "\n".join(item["message"] for item in json.loads(doctor.stdout)["findings"]))

    def test_root_target_pipeline_and_for_each_paths_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notebooks").mkdir()
            (root / "src").mkdir()
            (root / "notebooks" / "root.ipynb").write_text("{}", encoding="utf-8")
            (root / "notebooks" / "target.ipynb").write_text("{}", encoding="utf-8")
            (root / "notebooks" / "pipeline.ipynb").write_text("{}", encoding="utf-8")
            (root / "src" / "nested.py").write_text("print('ok')", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\n    resources:\n      jobs:\n        target_job:\n          name: target\n          tasks:\n            - task_key: outer\n              for_each_task:\n                inputs: '[1]'\n                task:\n                  spark_python_task:\n                    python_file: ./src/nested.py\n            - task_key: target\n              notebook_task:\n                notebook_path: ./notebooks/target.ipynb\nresources:\n  jobs:\n    root_job:\n      name: root\n      tasks:\n        - task_key: root\n          notebook_task:\n            notebook_path: ./notebooks/root.ipynb\n  pipelines:\n    pipe:\n      name: pipe\n      target: '${var.catalog}.${var.schema}'\n      libraries:\n        - notebook:\n            path: ./notebooks/pipeline.ipynb\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)

            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertEqual(0, json.loads(doctor.stdout)["errorCount"])

    def test_included_resource_uses_its_declaring_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "resources").mkdir()
            (root / "notebooks").mkdir()
            (root / "notebooks" / "included.ipynb").write_text("{}", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - resources/*.yml\ntargets:\n  dev:\n    default: true\n""",
            )
            (root / "resources" / "job.yml").write_text(
                """resources:\n  jobs:\n    included:\n      name: included\n      tasks:\n        - task_key: included\n          notebook_task:\n            notebook_path: ../notebooks/included.ipynb\n""",
                encoding="utf-8",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertEqual(0, json.loads(doctor.stdout)["errorCount"])

    def test_pipeline_file_and_glob_references_are_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notebooks").mkdir()
            (root / "src").mkdir()
            (root / "notebooks" / "pipeline.ipynb").write_text("{}", encoding="utf-8")
            (root / "src" / "pipeline.py").write_text("print('ok')", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\nresources:\n  pipelines:\n    pipe:\n      name: pipe\n      target: '${var.catalog}.${var.schema}'\n      libraries:\n        - notebook:\n            path: ./notebooks/pipeline.ipynb\n        - file:\n            path: ./src/pipeline.py\n        - glob:\n            include:\n              - ./src/**/*.py\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)

            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\nresources:\n  pipelines:\n    pipe:\n      name: pipe\n      target: '${var.catalog}.${var.schema}'\n      libraries:\n        - glob:\n            include: ../outside/**/*.py\n        - file:\n            path: NUL.txt\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("cannot use parent-directory traversal", "\n".join(messages))
            self.assertIn("host-specific", "\n".join(messages))

    def test_pipeline_glob_parent_traversal_rejects_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roots = self.make_roots(root)
            policy = VALIDATOR.load_local_path_policy()
            with patch.object(Path, "resolve", side_effect=AssertionError("must not resolve glob")):
                for pattern in ("foo*/../../outside/**/*.py", "foo/**/../../outside"):
                    errors = VALIDATOR.validate_glob_reference(root, roots, pattern, "field", policy, "databricks.yml")
                    self.assertTrue(any("cannot use parent-directory traversal" in error.message for error in errors), pattern)

            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\nresources:\n  pipelines:\n    pipe:\n      name: pipe\n      target: '${var.catalog}.${var.schema}'\n      libraries:\n        - glob:\n            include: foo*/../../outside/**/*.py\n""",
            )
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertIn("cannot use parent-directory traversal", "\n".join(item["message"] for item in json.loads(doctor.stdout)["findings"]))

    def test_included_target_pipeline_library_uses_fragment_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "bundle"
            shared = workspace / "shared"
            root.mkdir()
            shared.mkdir()
            (root / "resources").mkdir()
            (root / "root.py").write_text("print('root')", encoding="utf-8")
            (shared / "target.py").write_text("print('target')", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - resources/*.yml\nsync:\n  paths:\n    - .\ntargets:\n  prod:\n    sync:\n      paths:\n        - ../shared\nresources:\n  pipelines:\n    pipe:\n      name: pipe\n      target: '${var.catalog}.${var.schema}'\n      libraries:\n        - file:\n            path: ./root.py\n""",
            )
            (root / "resources" / "target.yml").write_text(
                """targets:\n  prod:\n    resources:\n      pipelines:\n        pipe:\n          libraries:\n            - file:\n                path: ../../shared/target.py\n""",
                encoding="utf-8",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            self.assertNotIn("targets.prod.resources.pipelines.pipe.libraries[0].file.path", "\n".join(messages))
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertEqual(0, json.loads(doctor.stdout)["errorCount"])

            (root / "resources" / "target.yml").write_text(
                """targets:\n  prod:\n    resources:\n      pipelines:\n        pipe:\n          libraries:\n            - file:\n                path: ../../shared/missing.py\n""",
                encoding="utf-8",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            self.assertIn("[resources/target.yml]", "\n".join(messages))
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertTrue(any(item["source"] == "resources/target.yml" for item in json.loads(doctor.stdout)["findings"]))

    def test_target_sync_paths_are_isolated_and_overlays_preserve_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "bundle"
            dev_source = workspace / "dev-source"
            prod_source = workspace / "prod-source"
            root.mkdir()
            dev_source.mkdir()
            prod_source.mkdir()
            (root / "root.py").write_text("print('root')", encoding="utf-8")
            (dev_source / "task.py").write_text("print('dev')", encoding="utf-8")
            (prod_source / "task.py").write_text("print('prod')", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\n    sync:\n      paths:\n        - ../dev-source\n    resources:\n      jobs:\n        task:\n          schedule:\n            pause_status: PAUSED\n          tasks:\n            - task_key: task\n              spark_python_task:\n                python_file: ../prod-source/task.py\n  prod:\n    sync:\n      paths:\n        - ../prod-source\n    resources:\n      jobs:\n        task:\n          schedule:\n            pause_status: UNPAUSED\n          tasks:\n            - task_key: task\n              spark_python_task:\n                python_file: ../prod-source/task.py\nresources:\n  jobs:\n    task:\n      name: task\n      schedule:\n        pause_status: PAUSED\n      tasks:\n        - task_key: task\n          spark_python_task:\n            python_file: ./root.py\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertFalse(success)
            joined = "\n".join(messages)
            self.assertIn("targets.dev.resources.jobs.task.tasks[0].spark_python_task.python_file", joined)
            self.assertNotIn("targets.prod.resources.jobs.task.tasks[0].spark_python_task.python_file': Python file local file", joined)
            self.assertNotIn("targets.prod.resources.jobs.task': Job 'tasks' must", joined)

            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(2, doctor.returncode, doctor.stderr + doctor.stdout)
            doctor_messages = "\n".join(item["message"] for item in json.loads(doctor.stdout)["findings"])
            self.assertIn("outside the bundle root", doctor_messages)

    def test_only_root_include_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "resources").mkdir()
            (root / "notebook.ipynb").write_text("{}", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ninclude:\n  - resources/one.yml\ntargets:\n  dev:\n    default: true\n""",
            )
            (root / "resources" / "one.yml").write_text(
                """include:\n  - nested.yml\nresources:\n  jobs:\n    task:\n      name: task\n      tasks:\n        - task_key: task\n          notebook_task:\n            notebook_path: ../notebook.ipynb\n""",
                encoding="utf-8",
            )
            (root / "resources" / "nested.yml").write_text("not: [valid\n", encoding="utf-8")
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            self.assertIn("included fragments are ignored", "\n".join(messages))
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertIn("included fragments are ignored", "\n".join(item["message"] for item in json.loads(doctor.stdout)["findings"]))

    def test_schedule_only_target_overlay_keeps_root_job_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notebook.ipynb").write_text("{}", encoding="utf-8")
            self.write_bundle(
                root,
                """bundle:\n  name: test\ntargets:\n  dev:\n    default: true\n    resources:\n      jobs:\n        task:\n          schedule:\n            pause_status: PAUSED\n  prod:\n    resources:\n      jobs:\n        task:\n          schedule:\n            pause_status: UNPAUSED\nresources:\n  jobs:\n    task:\n      name: task\n      schedule:\n        pause_status: PAUSED\n      tasks:\n        - task_key: task\n          notebook_task:\n            notebook_path: ./notebook.ipynb\n""",
            )
            success, messages = VALIDATOR.validate_bundle(root)
            self.assertTrue(success, messages)
            self.assertNotIn("Job missing 'tasks' field", "\n".join(messages))
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertNotIn("Job 'tasks' must", "\n".join(item["message"] for item in json.loads(doctor.stdout)["findings"]))

    def test_python_and_doctor_share_classifier_fixture(self) -> None:
        policy = VALIDATOR.load_local_path_policy()
        references = [
            "./src/task.py",
            "${var.notebook}",
            "/Workspace/Users/example/notebook",
            "/Volumes/catalog/schema/volume/file.py",
            "dbfs:/tmp/file.py",
            "s3://bucket/file.py",
            "abfss://container@account/file.py",
            "wasbs://container@account/file.py",
            "gs://bucket/file.py",
            r"C:\\host\\file.py",
            r"\\server\\share\\file.py",
            "CON",
            "aux.txt",
            "COM9.log",
            "lPt1.",
            "notebook.py:stream",
            "file:///host/file.py",
        ]
        for reference in references:
            doctor = run(
                ["bun", str(DOCTOR), "--classify-path", reference, "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, doctor.returncode, doctor.stderr + doctor.stdout)
            self.assertEqual(
                VALIDATOR.classify_path_reference(reference, policy),
                json.loads(doctor.stdout)["classification"],
                reference,
            )


if __name__ == "__main__":
    unittest.main()
