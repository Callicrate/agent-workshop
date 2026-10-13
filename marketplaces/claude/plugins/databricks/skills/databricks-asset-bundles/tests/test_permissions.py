"""Parity coverage for DAB permission validation."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from subprocess import run


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "validate_bundle.py"
DOCTOR = SKILL_ROOT / "scripts" / "dab_doctor.mjs"
SPEC = importlib.util.spec_from_file_location("validate_bundle_permissions", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class PermissionParityTests(unittest.TestCase):
    """Run each permission fixture through both static validators."""

    def validate_with_both(self, body: str) -> tuple[bool, str, int, str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "databricks.yml").write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
            python_ok, python_messages = VALIDATOR.validate_bundle(root)
            doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
            self.assertTrue(doctor.stdout, doctor.stderr)
            doctor_payload = json.loads(doctor.stdout)
            doctor_messages = "\n".join(
                f"{item.get('path', '')} [{item.get('source', '')}]: {item['message']}"
                for item in doctor_payload.get("findings", [])
            )
            return python_ok, "\n".join(python_messages), doctor.returncode, doctor_messages

    def assert_rejected_by_both(self, body: str, expected: tuple[str, ...]) -> None:
        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(body)
        self.assertFalse(python_ok, python_messages)
        self.assertEqual(2, doctor_code, doctor_messages)
        for fragment in expected:
            self.assertIn(fragment, python_messages)
            self.assertIn(fragment, doctor_messages)

    def test_invalid_level_principal_count_and_blank_fields(self) -> None:
        self.assert_rejected_by_both(
            """
            bundle:
              name: permissions-invalid
            permissions:
              - user_name: user@example.com
                level: CAN_FLY
              - user_name: " "
                level: " "
              - level: CAN_VIEW
            targets:
              dev:
                default: true
                permissions:
                  - user_name: user@example.com
                    group_name: users
                    level: CAN_VIEW
            resources: {}
            """,
            (
                "permissions[0].level",
                "CAN_FLY",
                "permissions[1].user_name",
                "non-empty string",
                "permissions[2]",
                "targets.dev.permissions[0]",
                "Permission entry must define exactly one principal key",
            ),
        )

    def test_job_and_pipeline_level_sets_are_not_interchangeable(self) -> None:
        self.assert_rejected_by_both(
            """
            bundle:
              name: resource-levels-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              jobs:
                task:
                  name: task
                  permissions:
                    - group_name: users
                      level: CAN_RUN
                  tasks:
                    - task_key: task
                      sql_task:
                        query:
                          query_id: abc
              pipelines:
                pipe:
                  name: pipe
                  permissions:
                    - group_name: users
                      level: CAN_MANAGE_RUN
                  libraries: []
                  target: '${var.catalog}.${var.schema}'
            """,
            (
                "resources.jobs.task.permissions[0].level",
                "resources.pipelines.pipe.permissions[0].level",
                "not allowed here",
            ),
        )

    def test_permission_sections_must_be_lists(self) -> None:
        self.assert_rejected_by_both(
            """
            bundle:
              name: permission-list-shapes
            permissions: null
            targets:
              dev:
                default: true
                permissions: {}
            resources:
              jobs:
                task:
                  name: task
                  permissions: {}
                  tasks:
                    - task_key: task
                      sql_task:
                        query:
                          query_id: abc
              pipelines:
                pipe:
                  name: pipe
                  permissions: {}
                  libraries: []
                  target: '${var.catalog}.${var.schema}'
            """,
            (
                "'permissions' must be a list",
                "targets.dev.permissions",
                "resources.jobs.task.permissions",
                "resources.pipelines.pipe.permissions",
            ),
        )

    def test_dashboard_levels_are_closed_and_current(self) -> None:
        self.assert_rejected_by_both(
            """
            bundle:
              name: dashboard-permissions-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              dashboards:
                report:
                  display_name: report
                  permissions:
                    - group_name: users
                      level: CAN_FLY
            """,
            ("resources.dashboards.report.permissions[0].level", "CAN_FLY", "not allowed here"),
        )

        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(
            """
            bundle:
              name: dashboard-permissions-valid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              dashboards:
                report:
                  display_name: report
                  permissions:
                    - user_name: reader@example.com
                      level: CAN_READ
                    - group_name: editors
                      level: CAN_EDIT
                    - service_principal_name: 123456-abcdef
                      level: CAN_RUN
                    - user_name: manager@example.com
                      level: CAN_MANAGE
            """
        )
        self.assertTrue(python_ok, python_messages)
        self.assertEqual(0, doctor_code, doctor_messages)

    def test_cluster_policy_and_vector_endpoint_sets_are_exact(self) -> None:
        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(
            """
            bundle:
              name: additional-resource-permissions-valid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              cluster_policies:
                policy:
                  permissions:
                    - group_name: users
                      level: CAN_USE
              vector_search_endpoints:
                endpoint:
                  permissions:
                    - service_principal_name: 123456-abcdef
                      level: CAN_CREATE
            """
        )
        self.assertTrue(python_ok, python_messages)
        self.assertEqual(0, doctor_code, doctor_messages)

        self.assert_rejected_by_both(
            """
            bundle:
              name: additional-resource-permissions-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              cluster_policies:
                policy:
                  permissions:
                    - group_name: users
                      level: CAN_MANAGE
              vector_search_endpoints:
                endpoint:
                  permissions:
                    - service_principal_name: 123456-abcdef
                      level: CAN_VIEW
            """,
            (
                "resources.cluster_policies.policy.permissions[0].level",
                "resources.vector_search_endpoints.endpoint.permissions[0].level",
                "not allowed here",
            ),
        )

    def test_unknown_resource_uses_documented_union_and_still_checks_principals(self) -> None:
        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(
            """
            bundle:
              name: generic-permissions-valid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              schemas:
                analytics:
                  name: analytics
              future_widgets:
                widget:
                  permissions:
                    - user_name: user@example.com
                      level: CAN_BIND
            """
        )
        self.assertTrue(python_ok, python_messages)
        self.assertEqual(0, doctor_code, doctor_messages)

        self.assert_rejected_by_both(
            """
            bundle:
              name: generic-permissions-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              future_widgets:
                widget:
                  permissions:
                    - level: CAN_VIEW
            """,
            ("resources.future_widgets.widget.permissions[0]", "exactly one principal key"),
        )

        self.assert_rejected_by_both(
            """
            bundle:
              name: generic-secret-level-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              future_widgets:
                widget:
                  permissions:
                    - user_name: user@example.com
                      level: READ
            """,
            ("resources.future_widgets.widget.permissions[0].level", "READ", "not allowed here"),
        )

        for unsupported_level in ("CAN_CREATE_APP", "CAN_MONITOR_ONLY"):
            with self.subTest(unsupported_level=unsupported_level):
                self.assert_rejected_by_both(
                    f"""
                    bundle:
                      name: generic-enum-invalid
                    permissions:
                      - group_name: users
                        level: CAN_MANAGE
                    targets:
                      dev:
                        default: true
                    resources:
                      future_widgets:
                        widget:
                          permissions:
                            - user_name: user@example.com
                              level: {unsupported_level}
                    """,
                    (
                        "resources.future_widgets.widget.permissions[0].level",
                        unsupported_level,
                        "not allowed here",
                    ),
                )

    def test_secret_scope_uses_standard_permission_sequence(self) -> None:
        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(
            """
            bundle:
              name: secret-scope-permissions-valid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              secret_scopes:
                application:
                  permissions:
                    - group_name: readers
                      level: READ
                    - service_principal_name: 123456-abcdef
                      level: WRITE
                    - user_name: manager@example.com
                      level: MANAGE
            """
        )
        self.assertTrue(python_ok, python_messages)
        self.assertEqual(0, doctor_code, doctor_messages)

        self.assert_rejected_by_both(
            """
            bundle:
              name: secret-scope-permissions-invalid
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
            resources:
              secret_scopes:
                application:
                  permissions:
                    acls:
                      - principal: " "
                        permission: CAN_FLY
            """,
            (
                "resources.secret_scopes.application.permissions",
                "'permissions' must be a list",
            ),
        )

        self.assert_rejected_by_both(
            """
            bundle:
              name: secret-scope-overlay-combination
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
                resources:
                  secret_scopes:
                    application:
                      permissions:
                        - service_principal_name: 123456-abcdef
                          level: WRITE
            resources:
              secret_scopes:
                application:
                  permissions:
                    - group_name: readers
                      level: CAN_FLY
            """,
            ("targets.dev.resources.secret_scopes.application.permissions[0].level", "CAN_FLY"),
        )

    def test_target_resource_permission_lists_combine(self) -> None:
        self.assert_rejected_by_both(
            """
            bundle:
              name: combined-resource-permissions
            permissions:
              - group_name: users
                level: CAN_MANAGE
            targets:
              dev:
                default: true
                resources:
                  jobs:
                    task:
                      permissions:
                        - service_principal_name: 123456-abcdef
                          level: CAN_MANAGE_RUN
            resources:
              jobs:
                task:
                  name: task
                  permissions:
                    - user_name: bad@example.com
                      level: CAN_FLY
                  tasks:
                    - task_key: task
                      sql_task:
                        query:
                          query_id: abc
            """,
            ("targets.dev.resources.jobs.task.permissions[0].level", "CAN_FLY"),
        )

    def test_included_scalar_permission_sources_are_preserved(self) -> None:
        cases = (
            (
                "root permissions",
                "permissions: null\n",
                "permissions",
            ),
            (
                "root resource permissions",
                "resources:\n  dashboards:\n    report:\n      permissions: null\n",
                "resources.dashboards.report.permissions",
            ),
            (
                "target overlay permissions",
                "targets:\n  dev:\n    resources:\n      jobs:\n        task:\n          permissions: null\n",
                "targets.dev.resources.jobs.task.permissions",
            ),
        )
        for label, fragment, expected_path in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                resources = root / "resources"
                resources.mkdir()
                (root / "databricks.yml").write_text(
                    textwrap.dedent(
                        """
                        bundle:
                          name: included-permission-source
                        include:
                          - resources/permissions.yml
                        permissions:
                          - group_name: users
                            level: CAN_MANAGE
                        targets:
                          dev:
                            default: true
                        resources:
                          jobs:
                            task:
                              name: task
                              permissions:
                                - group_name: users
                                  level: CAN_VIEW
                              tasks:
                                - task_key: task
                                  sql_task:
                                    query:
                                      query_id: abc
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )
                (resources / "permissions.yml").write_text(fragment, encoding="utf-8")
                python_ok, python_messages = VALIDATOR.validate_bundle(root)
                doctor = run(["bun", str(DOCTOR), str(root), "--json"], capture_output=True, text=True, check=False)
                doctor_payload = json.loads(doctor.stdout)
                doctor_messages = "\n".join(
                    f"{item.get('path', '')} [{item.get('source', '')}]: {item['message']}"
                    for item in doctor_payload.get("findings", [])
                )
                self.assertFalse(python_ok, python_messages)
                self.assertEqual(2, doctor.returncode, doctor_messages)
                self.assertIn(expected_path, "\n".join(python_messages))
                self.assertIn(expected_path, doctor_messages)
                self.assertIn("[resources/permissions.yml]", "\n".join(python_messages))
                self.assertIn("[resources/permissions.yml]", doctor_messages)

    def test_valid_root_target_resource_and_overlay_permissions(self) -> None:
        python_ok, python_messages, doctor_code, doctor_messages = self.validate_with_both(
            """
            bundle:
              name: permissions-valid
            permissions:
              - user_name: user@example.com
                level: CAN_VIEW
              - group_name: users
                level: CAN_MANAGE
              - service_principal_name: 123456-abcdef
                level: CAN_RUN
            targets:
              dev:
                default: true
                permissions:
                  - group_name: users
                    level: CAN_VIEW
                resources:
                  jobs:
                    task:
                      permissions:
                        - service_principal_name: 123456-abcdef
                          level: CAN_MANAGE_RUN
                  pipelines:
                    pipe:
                      permissions:
                        - service_principal_name: 123456-abcdef
                          level: CAN_RUN
            resources:
              jobs:
                task:
                  name: task
                  permissions:
                    - user_name: owner@example.com
                      level: IS_OWNER
                    - group_name: users
                      level: CAN_VIEW
                    - service_principal_name: 123456-abcdef
                      level: CAN_MANAGE_RUN
                    - user_name: manager@example.com
                      level: CAN_MANAGE
                  tasks:
                    - task_key: task
                      sql_task:
                        query:
                          query_id: abc
              pipelines:
                pipe:
                  name: pipe
                  permissions:
                    - user_name: owner@example.com
                      level: IS_OWNER
                    - group_name: users
                      level: CAN_VIEW
                    - service_principal_name: 123456-abcdef
                      level: CAN_RUN
                    - user_name: manager@example.com
                      level: CAN_MANAGE
                  libraries: []
                  target: '${var.catalog}.${var.schema}'
            """
        )
        self.assertTrue(python_ok, python_messages)
        self.assertEqual(0, doctor_code, doctor_messages)
        self.assertNotIn("Permission", python_messages)
        self.assertNotIn("Permission", doctor_messages)


if __name__ == "__main__":
    unittest.main()
