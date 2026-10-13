"""Synthetic coverage for DAB job-parameter static validation."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "validate_bundle.py"
SPEC = importlib.util.spec_from_file_location("validate_bundle_job_parameters", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class JobParameterTests(unittest.TestCase):
    """Validate job parameter contracts using only local temporary bundles."""

    def validate(self, body: str) -> tuple[bool, list[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "notebook.ipynb").write_text("{}", encoding="utf-8")
        (root / "databricks.yml").write_text(
            textwrap.dedent(body).lstrip(), encoding="utf-8"
        )
        success, messages = VALIDATOR.validate_bundle(root)
        return success, messages, root

    def test_parameter_entries_require_list_mapping_name_and_default(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: parameter-shape
            targets:
              dev:
                default: true
            resources:
              jobs:
                invalid:
                  name: invalid
                  parameters:
                    - not-a-mapping
                    - name: " "
                      default: value
                    - name: missing_default
                    - name: duplicate
                      default: first
                    - name: duplicate
                      default: second
                    - name: bad/name
                      default: value
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
            """
        )
        self.assertFalse(success)
        joined = "\n".join(messages)
        for expected in (
            "Job parameter entry must be a mapping",
            "Job parameter name must be a non-empty string",
            "Job parameter must define a 'default' value",
            "Job parameter names must be unique",
            "Job parameter name may contain only",
        ):
            self.assertIn(expected, joined)

        shape_success, shape_messages, _ = self.validate(
            """
            bundle:
              name: parameter-list-shape
            targets:
              dev:
                default: true
            resources:
              jobs:
                invalid:
                  name: invalid
                  parameters:
                    name: not-a-list
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
            """
        )
        self.assertFalse(shape_success)
        self.assertIn("Job 'parameters' must be a list", "\n".join(shape_messages))

    def test_task_only_base_parameters_and_explicit_forwarding_are_valid(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: explicit-forwarding
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: explicit forwarding
                  parameters:
                    - name: run_date
                      default: "{{job.start_time.iso_date}}"
                    - name: processing-date
                      default: "2026-03-13"
                    - name: processing.date
                      default: "2026-03-13"
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          task_only: local-default
                          run_date: "{{ job.parameters.run_date }}"
                          processing-date: "{{ job.parameters.`processing-date` }}"
                          processing.date: "{{ job.parameters.`processing.date` }}"
            """
        )
        self.assertTrue(success, messages)
        joined = "\n".join(messages)
        self.assertNotIn("undeclared job parameter", joined)
        self.assertNotIn("literal default that job parameter pushdown overrides", joined)

    def test_known_namespace_requires_valid_job_parameter_reference_syntax(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: invalid-reference-syntax
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: invalid reference syntax
                  parameters:
                    - name: processing-date
                      default: value
                    - name: processing.date
                      default: value
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          unescaped_hyphen: "{{job.parameters.processing-date}}"
                          unescaped_dot: "{{job.parameters.processing.date}}"
                          malformed_open_quote: "{{job.parameters.`processing-date}}"
                          malformed_close_quote: "{{job.parameters.processing-date`}}"
                          invalid_identifier: "{{job.parameters.processing/date}}"
                          internal_space: "{{ job.parameters.processing date }}"
            """
        )
        self.assertFalse(success)
        self.assertGreaterEqual(
            "\n".join(messages).count("Job parameter reference must use an alphanumeric"),
            6,
        )

    def test_dangling_job_parameter_template_attempts_are_rejected(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: dangling-reference-syntax
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: dangling reference syntax
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          no_close: "{{job.parameters.processing-date"
                          single_close: "{{job.parameters.processing-date}"
                          no_open: "job.parameters.processing-date}}"
            """
        )
        self.assertFalse(success)
        self.assertGreaterEqual(
            "\n".join(messages).count("Job parameter reference must use an alphanumeric"),
            3,
        )

    def test_valid_mixed_text_reference_and_non_reference_prose_are_preserved(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: mixed-reference-text
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: mixed reference text
                  parameters:
                    - name: declared
                      default: value
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          mixed: "prefix {{ job.parameters.declared }} suffix"
                          prose: "job.parameters.processing-date is documentation"
                          other_namespace: "{{job.start_time.iso_date"
            """
        )
        self.assertTrue(success, messages)
        self.assertNotIn("Job parameter reference must use an alphanumeric", "\n".join(messages))

    def test_unrelated_brace_literal_is_not_a_job_parameter_reference(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: unrelated-brace-literal
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: unrelated literal
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          note: "keep {{other.namespace.value}} as text"
            """
        )
        self.assertTrue(success, messages)
        self.assertNotIn("Job parameter reference", "\n".join(messages))

    def test_missing_job_parameter_reference_is_rejected(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: missing-reference
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: missing reference
                  parameters:
                    - name: declared
                      default: value
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          value: "{{ job.parameters.missing }}"
            """
        )
        self.assertFalse(success)
        self.assertIn("Task references undeclared job parameter 'missing'", "\n".join(messages))

    def test_same_key_literal_notebook_default_warns_about_pushdown(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: pushdown-warning
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: pushdown warning
                  parameters:
                    - name: run_date
                      default: "{{job.start_time.iso_date}}"
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          run_date: "2026-03-13"
            """
        )
        self.assertTrue(success, messages)
        self.assertIn(
            "Notebook base parameter 'run_date' is a literal default that job parameter pushdown overrides",
            "\n".join(messages),
        )

    def test_regular_schedules_warn_for_fixed_temporal_defaults_but_not_dynamic_ones(self) -> None:
        fixed_success, fixed_messages, _ = self.validate(
            """
            bundle:
              name: fixed-schedule
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: regular job
                  parameters:
                    - name: at_timestamp
                      default: "2026-03-13T00:00:00Z"
                  schedule:
                    quartz_cron_expression: "0 0 0 * * ?"
                    timezone_id: UTC
                    pause_status: PAUSED
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
            """
        )
        self.assertTrue(fixed_success, fixed_messages)
        self.assertIn("Regularly scheduled job has a literal ISO temporal default", "\n".join(fixed_messages))

        dynamic_success, dynamic_messages, _ = self.validate(
            """
            bundle:
              name: dynamic-schedule
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: regular job
                  parameters:
                    - name: at_timestamp
                      default: "{{job.start_time.iso_datetime}}"
                  schedule:
                    quartz_cron_expression: "0 0 0 * * ?"
                    timezone_id: UTC
                    pause_status: PAUSED
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
            """
        )
        self.assertTrue(dynamic_success, dynamic_messages)
        self.assertNotIn("Regularly scheduled job has a literal ISO temporal default", "\n".join(dynamic_messages))

    def test_manual_and_documented_historical_fixed_dates_are_preserved(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: fixed-history
            targets:
              dev:
                default: true
            resources:
              jobs:
                manual:
                  name: manual run
                  parameters:
                    - name: processing_date
                      default: "2026-03-13"
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                historical:
                  name: historical backfill
                  description: Replay the March training window.
                  parameters:
                    - name: processing_date
                      default: "2026-03-13"
                  schedule:
                    quartz_cron_expression: "0 0 0 * * ?"
                    timezone_id: UTC
                    pause_status: PAUSED
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
            """
        )
        self.assertTrue(success, messages)
        self.assertNotIn("Regularly scheduled job has a literal ISO temporal default", "\n".join(messages))
        self.assertTrue(
            VALIDATOR.job_has_documented_historical_intent(
                {"name": "regular job"},
                "resources.jobs.repair_training_2026_03_13",
            )
        )

    def test_target_job_parameter_override_merges_by_name(self) -> None:
        success, messages, root = self.validate(
            """
            bundle:
              name: target-parameter-override
            targets:
              dev:
                default: true
              prod:
                resources:
                  jobs:
                    job:
                      parameters:
                        - name: mode
                          default: prod
            resources:
              jobs:
                job:
                  name: target parameter override
                  parameters:
                    - name: run_date
                      default: "{{job.start_time.iso_date}}"
                    - name: mode
                      default: dev
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
                          run_date: "{{job.parameters.run_date}}"
            """
        )
        self.assertTrue(success, messages)
        self.assertNotIn("Job parameter names must be unique", "\n".join(messages))
        config, _, _, context, _ = VALIDATOR.load_bundle_with_files(root)
        effective = VALIDATOR.effective_resources(
            config["resources"],
            config["targets"]["prod"]["resources"],
            root,
            context,
        )
        self.assertEqual(
            effective["jobs"]["job"]["parameters"],
            [
                {"name": "run_date", "default": "{{job.start_time.iso_date}}"},
                {"name": "mode", "default": "prod"},
            ],
        )

    def test_for_each_task_references_are_checked_recursively(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: nested-parameter-reference
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: nested references
                  parameters:
                    - name: items
                      default: "[1]"
                    - name: run_date
                      default: "{{job.start_time.iso_date}}"
                  tasks:
                    - task_key: outer
                      for_each_task:
                        inputs: "{{job.parameters.items}}"
                        task:
                          notebook_task:
                            notebook_path: ./notebook.ipynb
                            base_parameters:
                              run_date: "{{job.parameters.missing}}"
            """
        )
        self.assertFalse(success)
        joined = "\n".join(messages)
        self.assertIn("Task references undeclared job parameter 'missing'", joined)
        self.assertIn("for_each_task.task.notebook_task.base_parameters.run_date", joined)

    def test_directly_nested_for_each_is_rejected_but_its_nested_references_are_checked(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: nested-for-each
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: nested for each
                  parameters:
                    - name: items
                      default: "[1]"
                  tasks:
                    - task_key: outer
                      for_each_task:
                        inputs: "{{job.parameters.items}}"
                        task:
                          for_each_task:
                            inputs: "[1]"
                            task:
                              notebook_task:
                                notebook_path: ./notebook.ipynb
                                base_parameters:
                                  missing: "{{job.parameters.missing}}"
            """
        )
        self.assertFalse(success)
        joined = "\n".join(messages)
        self.assertIn("For each task cannot use another For each task as its nested task", joined)
        self.assertIn("Task references undeclared job parameter 'missing'", joined)

    def test_normal_for_each_task_remains_valid(self) -> None:
        success, messages, _ = self.validate(
            """
            bundle:
              name: normal-for-each
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: normal for each
                  parameters:
                    - name: items
                      default: "[1]"
                    - name: run_date
                      default: "{{job.start_time.iso_date}}"
                  tasks:
                    - task_key: outer
                      for_each_task:
                        inputs: "{{job.parameters.items}}"
                        task:
                          notebook_task:
                            notebook_path: ./notebook.ipynb
                            base_parameters:
                              run_date: "{{job.parameters.run_date}}"
            """
        )
        self.assertTrue(success, messages)
        self.assertNotIn("cannot use another For each", "\n".join(messages))

    def test_reference_scan_warns_when_bounded_inspection_is_incomplete(self) -> None:
        base_parameters = "\n".join(
            f"item_{index}: \"{{{{job.parameters.declared}}}}\""
            for index in range(600)
        )
        body = textwrap.dedent(
            """
            bundle:
              name: bounded-reference-scan
            targets:
              dev:
                default: true
            resources:
              jobs:
                job:
                  name: bounded scan
                  parameters:
                    - name: declared
                      default: value
                  tasks:
                    - task_key: task
                      notebook_task:
                        notebook_path: ./notebook.ipynb
                        base_parameters:
            """
        )
        body += textwrap.indent(base_parameters, " " * 14) + "\n"
        success, messages, _ = self.validate(body)
        self.assertTrue(success, messages)
        self.assertIn("reference inspection is incomplete", "\n".join(messages))

    def test_for_each_task_validation_stops_on_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task: dict[str, object] = {"for_each_task": {}}
            task["for_each_task"]["task"] = task
            errors = VALIDATOR.validate_task(
                task,
                "resources.jobs.job.tasks[0]",
                root,
                VALIDATOR.LocalSourceRoots((root,), (root,)),
                VALIDATOR.DEFAULT_RUNTIME_PREFIXES,
                VALIDATOR.SourceContext(),
                VALIDATOR.load_local_path_policy(),
                {},
                require_task_key=False,
            )
        self.assertIn("For each task cannot contain a cycle", "\n".join(map(str, errors)))


if __name__ == "__main__":
    unittest.main()
