from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_prompt_structure.py"
SPEC = importlib.util.spec_from_file_location("audit_prompt_structure", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load audit script from {SCRIPT_PATH}")
audit_prompt_structure = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_prompt_structure
SPEC.loader.exec_module(audit_prompt_structure)


def issue_codes(issues: list[object]) -> set[str]:
    return {issue.code for issue in issues}


def role_prompt(identity: str) -> str:
    return f"""# Schema Reviewer

## Identity
{identity}

## Capabilities
May review schema diffs and propose validation checks.

## Boundaries
Must not approve production migrations.

## Inputs and Outputs
Inputs are schema files. Outputs are findings and validation notes.

## Escalation
Ask when ownership or migration approval is unclear.
"""


class AuditPromptStructureTests(unittest.TestCase):
    def test_detect_kind_low_confidence_defaults_to_system(self) -> None:
        text = """# Minimal Prompt

## Identity
You are the reviewer.
"""
        kind_result = audit_prompt_structure.detect_kind(text, audit_prompt_structure.collect_headings(text))
        self.assertEqual(kind_result.kind, "system")
        self.assertEqual(kind_result.confidence, "low")

    def test_cli_warns_when_auto_kind_confidence_is_low(self) -> None:
        text = """# System Prompt

## Identity
You are the reviewer.

## Environment
Use repository files.

## Security
Never expose secrets.

## Role
Own review only.

## Instructions
Follow the ordered workflow.

## Memory
Load repository guidance.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "prompt.md"
            target.write_text(text, encoding="utf-8")
            original_argv = sys.argv
            output = io.StringIO()
            try:
                sys.argv = ["audit_prompt_structure.py", str(target)]
                with contextlib.redirect_stdout(output):
                    exit_code = audit_prompt_structure.main()
            finally:
                sys.argv = original_argv

        self.assertEqual(exit_code, 0)
        self.assertIn("Auto-detection is uncertain", output.getvalue())

    def test_system_contract_golden_prompt_has_no_errors(self) -> None:
        text = """# Release Review Agent

## Identity
You are the release reviewer.

## Environment
You may read repository files and run documented tests.

## Security
Never publish releases without explicit confirmation.

## Role
Own release-readiness review and not release publication.

## Instructions
1. Inspect the diff.
2. Run focused validation.

## Memory
Load repository guidance before review.
"""
        issues = audit_prompt_structure.audit_text(text, "system")
        self.assertFalse([issue for issue in issues if issue.severity == "error"])

    def test_role_contract_golden_prompt_has_no_errors(self) -> None:
        text = role_prompt(
            "Name the schema reviewer and data platform audience.\n"
            "Primary objective: determine whether schema changes preserve the data contract."
        )
        issues = audit_prompt_structure.audit_text(text, "role")
        self.assertFalse([issue for issue in issues if issue.severity == "error"])
        self.assertNotIn("missing_primary_objective", issue_codes(issues))

    def test_role_contract_accepts_structured_objective_synonyms(self) -> None:
        for identity in (
            "- Primary objective: determine whether schema changes preserve the data contract.",
            "Primary job: determine whether schema changes preserve the data contract.",
            "Primary responsibility: determine whether schema changes preserve the data contract.",
            "Objective: determine whether schema changes preserve the data contract.",
            "- **Purpose:** determine whether schema changes preserve the data contract.",
            "Job:\n  Determine whether schema changes preserve the data contract.",
            "### Sole responsibility\nDetermine whether schema changes preserve the data contract.",
        ):
            with self.subTest(identity=identity):
                issues = audit_prompt_structure.audit_text(role_prompt(identity), "role")
                self.assertNotIn("missing_primary_objective", issue_codes(issues))

    def test_role_contract_accepts_identity_scoped_objective_statements(self) -> None:
        for identity in (
            "The primary objective is review schema compatibility.",
            "Primary objective shall be review schema compatibility.",
            "Primary objective will be review schema compatibility.",
            "Primary job is review schema compatibility.",
            "Primary responsibility is review schema compatibility.",
            "Sole responsibility is review schema compatibility.",
            "Solely responsible for reviewing schema compatibility.",
        ):
            with self.subTest(identity=identity):
                issues = audit_prompt_structure.audit_text(role_prompt(identity), "role")
                self.assertNotIn("missing_primary_objective", issue_codes(issues))

    def test_role_contract_without_primary_objective_gets_advisory_warning(self) -> None:
        issues = audit_prompt_structure.audit_text(role_prompt("Name: Schema reviewer"), "role")
        objective_issues = [issue for issue in issues if issue.code == "missing_primary_objective"]

        self.assertEqual(len(objective_issues), 1)
        self.assertEqual(objective_issues[0].severity, "warning")
        self.assertEqual(
            objective_issues[0].message,
            "No non-empty primary objective found in Identity. Add - Primary objective: <single outcome>.",
        )
        self.assertFalse([issue for issue in issues if issue.severity == "error"])

    def test_empty_bold_objective_label_gets_advisory_warning(self) -> None:
        for identity in (
            "- **Primary objective:**",
            "Primary responsibility:",
            "Primary objective is **",
            "Sole responsibility is",
            "Solely responsible for",
        ):
            with self.subTest(identity=identity):
                issues = audit_prompt_structure.audit_text(role_prompt(identity), "role")
                self.assertIn("missing_primary_objective", issue_codes(issues))

    def test_objective_outside_identity_or_inside_example_does_not_suppress_warning(self) -> None:
        misplaced = role_prompt("Name: Schema reviewer").replace(
            "## Capabilities\n",
            "## Capabilities\n- Objective: review schema compatibility.\n",
        )
        example_only = role_prompt(
            "Name: Schema reviewer\n\n### Example\n- Objective: review schema compatibility."
        )
        nested_capabilities = role_prompt(
            "Name: Schema reviewer\n\n### Capabilities\n- Objective: review schema compatibility."
        )
        tilde_fence_only = role_prompt(
            "Name: Schema reviewer\n\n~~~text\nObjective: example only.\n~~~"
        )
        for text in (misplaced, example_only, nested_capabilities, tilde_fence_only):
            with self.subTest(text=text):
                issues = audit_prompt_structure.audit_text(text, "role")
                self.assertIn("missing_primary_objective", issue_codes(issues))

    def test_fenced_or_subordinate_identity_headings_do_not_create_extra_roles(self) -> None:
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                text = role_prompt(
                    "- Objective: review schema compatibility.\n\n"
                    f"{marker}markdown\n## Identity\n- Objective:\n{marker}\n\n"
                    "### Identity and access\nDocument the account boundary."
                )
                issues = audit_prompt_structure.audit_text(text, "role")
                self.assertNotIn("missing_primary_objective", issue_codes(issues))

    def test_role_pack_requires_objective_in_each_identity(self) -> None:
        role_pack = """# Role Pack

## Reviewer Identity
- Objective: review schema compatibility.

## Reviewer Capabilities
May review schema diffs.

## Reviewer Boundaries
Must not approve migrations.

## Writer Identity
Name: Schema writer

## Writer Capabilities
May draft schema changes.

## Writer Boundaries
Must not deploy changes.

## Inputs and Outputs
Inputs are schema files. Outputs are findings or patches.

## Escalation
Ask when ownership is unclear.
"""
        issues = audit_prompt_structure.audit_text(role_pack, "role")
        objective_issues = [issue for issue in issues if issue.code == "missing_primary_objective"]
        self.assertEqual(len(objective_issues), 1)

        complete_pack = role_pack.replace(
            "## Writer Identity\nName: Schema writer",
            "## Writer Identity\n- Purpose: draft one compatible schema change.",
        )
        self.assertNotIn(
            "missing_primary_objective",
            issue_codes(audit_prompt_structure.audit_text(complete_pack, "role")),
        )

    def test_cli_does_not_block_role_without_primary_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "role.md"
            target.write_text(role_prompt("Name: Schema reviewer"), encoding="utf-8")
            original_argv = sys.argv
            output = io.StringIO()
            try:
                sys.argv = ["audit_prompt_structure.py", str(target), "--kind", "role", "--json"]
                with contextlib.redirect_stdout(output):
                    exit_code = audit_prompt_structure.main()
            finally:
                sys.argv = original_argv

        self.assertEqual(exit_code, 0)
        self.assertIn('"code": "missing_primary_objective"', output.getvalue())

    def test_cli_auto_detects_role_contract_heading_aliases(self) -> None:
        text = """# Schema Reviewer

## Identity
- Objective: review schema compatibility.

## May Do
Review schema diffs.

## Requires Confirmation
Production migration approval.

## Inputs and Outputs
Inputs are schema files. Outputs are findings.

## Ask When
Ownership is unclear.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "role.md"
            target.write_text(text, encoding="utf-8")
            original_argv = sys.argv
            output = io.StringIO()
            try:
                sys.argv = ["audit_prompt_structure.py", str(target), "--json"]
                with contextlib.redirect_stdout(output):
                    exit_code = audit_prompt_structure.main()
            finally:
                sys.argv = original_argv

        self.assertEqual(exit_code, 0)
        self.assertIn('"kind": "role"', output.getvalue())
        self.assertIn('"kind_confidence": "high"', output.getvalue())

    def test_empty_role_template_baseline_does_not_warn_about_objective(self) -> None:
        template_path = SCRIPT_PATH.parents[1] / "templates" / "agent-role-template.md"
        issues = audit_prompt_structure.audit_text(
            template_path.read_text(encoding="utf-8"),
            "role",
            template_baseline=True,
        )

        self.assertIn("template_baseline", issue_codes(issues))
        self.assertNotIn("missing_primary_objective", issue_codes(issues))

        normal_issues = audit_prompt_structure.audit_text(
            template_path.read_text(encoding="utf-8"),
            "role",
        )
        self.assertIn("missing_primary_objective", issue_codes(normal_issues))

    def test_multi_agent_contract_golden_prompt_has_no_errors(self) -> None:
        text = """# Review Swarm Contract

## Participants
Coordinator owns routing. Reviewer owns findings.

## Shared Artifacts
The status file is the source of truth.

## Handoffs
Coordinator accepts a finding report with validation notes.

## Conflict Resolution
The architecture document is the tie-breaker.

## Completion
Coordinator writes the final deliverable after validation.
"""
        issues = audit_prompt_structure.audit_text(text, "multi-agent")
        self.assertFalse([issue for issue in issues if issue.severity == "error"])

    def test_vague_phrase_detection_covers_common_weak_rules(self) -> None:
        text = """# Weak Prompt

## Identity
You are the reviewer.

## Environment
Use repository files.

## Security
Never expose secrets.

## Role
Own review only.

## Instructions
Use good judgment and respond appropriately.

## Memory
Load repository guidance.
"""
        issues = audit_prompt_structure.audit_text(text, "system")
        vague_messages = [issue.message for issue in issues if issue.code == "vague_phrase"]
        self.assertTrue(any("use good judgment" in message for message in vague_messages))
        self.assertTrue(any("respond appropriately" in message for message in vague_messages))

    def test_fenced_examples_are_ignored_for_vague_phrase_detection(self) -> None:
        text = """# Prompt Guide

## Identity
You are the reviewer.

## Environment
Use repository files.

## Security
Never expose secrets.

## Role
Own review only.

## Instructions
Follow the ordered workflow.

## Memory
Load repository guidance.

## Example
```text
Wrong: You are a helpful assistant who will do your best.
```
"""
        issues = audit_prompt_structure.audit_text(text, "system")
        self.assertNotIn("vague_phrase", issue_codes(issues))

    def test_plain_example_prose_is_still_audited(self) -> None:
        text = """# Prompt Guide

## Identity
You are the reviewer.

## Environment
Use repository files.

## Security
Never expose secrets.

## Role
Own review only.

## Instructions
Follow the ordered workflow.

## Memory
Load repository guidance.

## Example Usage
Use good judgment when changing files.
"""
        issues = audit_prompt_structure.audit_text(text, "system")
        self.assertIn("vague_phrase", issue_codes(issues))

    def test_template_baseline_skips_vague_phrase_audit(self) -> None:
        text = """# System Prompt Template

## Identity
- Name:

## Environment
- Tools:

## Security
- Never:

## Role
- Owns:

## Instructions
- Use good judgment:

## Memory
- Load at start:
"""
        issues = audit_prompt_structure.audit_text(text, "system", template_baseline=True)
        self.assertIn("template_baseline", issue_codes(issues))
        self.assertNotIn("vague_phrase", issue_codes(issues))


if __name__ == "__main__":
    unittest.main()
