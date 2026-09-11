from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
INVENTORY_SCRIPT = SCRIPTS / "inventory_skills.py"
OVERLAP_SCRIPT = SCRIPTS / "audit_skill_overlap.py"

sys.path.insert(0, str(SCRIPTS))

import audit_skill_overlap  # noqa: E402
import inventory_skills  # noqa: E402


def skill_text(
    name: str,
    description: str,
    *,
    short_description: str | None = "Test skill inventory.",
    filler_lines: int = 0,
) -> str:
    """Return a compact skill document fixture."""
    metadata = ""
    if short_description is not None:
        metadata = f"metadata:\n  short-description: {short_description}\n"
    filler = "\n".join("filler" for _ in range(filler_lines))
    return f"""---
name: {name}
description: \"{description}\"
{metadata}---

# Test Skill
{filler}
"""


class InventorySkillsTests(unittest.TestCase):
    def test_inventory_ignores_generated_cache_files_and_retains_authored_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "example-skill"
            scripts_dir = skill_dir / "scripts"
            tests_dir = scripts_dir / "tests"
            cache_dir = scripts_dir / "__PYCACHE__"
            tests_dir.mkdir(parents=True)
            cache_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "example-skill",
                    "Use when testing inventory resources. Do not trigger for unrelated work.",
                    short_description="Inspect skill resources.",
                ),
                encoding="utf-8",
            )
            (scripts_dir / "authored.py").write_text("pass\n", encoding="utf-8")
            (tests_dir / "test_authored.py").write_text("pass\n", encoding="utf-8")
            (scripts_dir / "compiled.pyc").write_bytes(b"compiled")
            (scripts_dir / "optimized.PYO").write_bytes(b"optimized")
            (cache_dir / "authored.cpython-313.pyc").write_bytes(b"cache")

            item = inventory_skills.inventory_skill(skill_dir)

            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item["resources"]["scripts"], ["scripts/authored.py", "scripts/tests/test_authored.py"])
            self.assertEqual(item["resource_counts"]["scripts"], 2)

    def test_inventory_exposes_nested_short_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "example-skill",
                    "Use when testing nested metadata. Do not trigger for unrelated work.",
                    short_description="Nested metadata fallback.",
                ),
                encoding="utf-8",
            )

            item = inventory_skills.inventory_skill(skill_dir)

            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item["short_description"], "Nested metadata fallback.")

    def test_cache_only_resources_do_not_create_a_heavy_skill_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "cache-only-skill"
            cache_dir = skill_dir / "scripts" / "__pycache__"
            cache_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "cache-only-skill",
                    "Use when testing cache-only resources. Do not trigger for unrelated work.",
                    filler_lines=151,
                ),
                encoding="utf-8",
            )
            (cache_dir / "only.pyc").write_bytes(b"cache")

            findings = audit_skill_overlap.audit(inventory_skills.inventory(root), threshold=0.22)["coverage_findings"]

            self.assertFalse(
                any(
                    finding["skill"] == "cache-only-skill" and "heavy" in finding["issue"]
                    for finding in findings
                )
            )


class CatalogCliPortabilityTests(unittest.TestCase):
    def write_alternate_root(self, directory: Path) -> Path:
        root = directory / "alternate-skills"
        skill_dir = root / "alternate-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            skill_text(
                "alternate-skill",
                "Use when checking portable catalog commands. Do not trigger for unrelated work.",
                short_description="Check portable catalogs.",
            ),
            encoding="utf-8",
        )
        return root

    def run_cli(self, script: Path, arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(script), *arguments],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_catalog_commands_default_to_packaged_root_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            for script in (INVENTORY_SCRIPT, OVERLAP_SCRIPT):
                with self.subTest(script=script.name):
                    result = self.run_cli(script, [], cwd)

                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, "")
                    payload = json.loads(result.stdout)
                    self.assertEqual(
                        payload["skills_root"],
                        str((SCRIPTS.parent.parent).resolve()),
                    )
                    self.assertGreater(payload["skill_count"], 0)

    def test_catalog_commands_accept_an_explicit_alternate_root_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory) / "unrelated"
            cwd.mkdir()
            alternate_root = self.write_alternate_root(Path(directory))
            for script in (INVENTORY_SCRIPT, OVERLAP_SCRIPT):
                with self.subTest(script=script.name):
                    result = self.run_cli(script, ["--skills-root", str(alternate_root)], cwd)

                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, "")
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["skills_root"], str(alternate_root.resolve()))
                    self.assertEqual(payload["skill_count"], 1)

    def test_catalog_commands_report_invalid_roots_without_echoing_values_or_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            secret = "UNTRUSTED-SKILLS-ROOT-MUST-NOT-ECHO"
            missing_root = cwd / secret
            for script in (INVENTORY_SCRIPT, OVERLAP_SCRIPT):
                with self.subTest(script=script.name):
                    result = self.run_cli(script, ["--skills-root", str(missing_root)], cwd)

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "error: skills root unavailable\n")
                    self.assertNotIn(secret, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_catalog_command_help_is_available_from_an_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            for script in (INVENTORY_SCRIPT, OVERLAP_SCRIPT):
                with self.subTest(script=script.name):
                    result = self.run_cli(script, ["--help"], cwd)

                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, "")
                    self.assertIn("--skills-root", result.stdout)


class DiscoveryBudgetTests(unittest.TestCase):
    def test_budget_metrics_report_exact_counts_for_each_path_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skills_root = Path(directory) / "skills"
            skill_dir = skills_root / "example-skill"
            skill_dir.mkdir(parents=True)
            path = skill_dir.resolve().as_posix()
            fixed_common_characters = len("example-skill")
            fixed_relative_characters = fixed_common_characters + len("skills/example-skill/SKILL.md")
            fixed_absolute_characters = fixed_common_characters + len(f"{path}/SKILL.md")
            cases = (
                ("relative", 7_999, "at_or_below_fallback"),
                ("relative", 8_001, "above_fallback"),
                ("absolute", 7_999, "at_or_below_fallback"),
                ("absolute", 8_001, "above_fallback"),
            )

            for path_model, expected_total, expected_result in cases:
                with self.subTest(path_model=path_model, expected_total=expected_total):
                    fixed_characters = (
                        fixed_relative_characters if path_model == "relative" else fixed_absolute_characters
                    )
                    description = "x" * (expected_total - fixed_characters)
                    metrics = inventory_skills.discovery_budget_metrics(
                        skills_root,
                        [{"name": "example-skill", "description": description, "path": path}],
                    )

                    self.assertEqual(metrics["status"], "advisory")
                    self.assertEqual(metrics["basis"]["fallback_characters"], 8_000)
                    self.assertEqual(metrics["basis"]["official_url"], "https://learn.chatgpt.com/docs/build-skills")
                    model_total = metrics["exact_totals"][f"{path_model}_path_model_total"]
                    model_comparison = metrics["threshold_comparison"][f"{path_model}_path_model"]
                    self.assertEqual(model_total, expected_total)
                    self.assertEqual(model_comparison["result"], expected_result)
                    self.assertEqual(
                        metrics["modeled_field_values"]["relative_skill_md_paths"],
                        ["skills/example-skill/SKILL.md"],
                    )
                    self.assertEqual(
                        metrics["modeled_field_values"]["absolute_skill_md_paths"],
                        [f"{path}/SKILL.md"],
                    )
                    self.assertEqual(metrics["description_outliers_over_200_characters"][0]["characters"], len(description))
                    self.assertIn("not proof that this host omitted a skill", metrics["limitation"])

    def test_relative_path_model_uses_actual_nested_root_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skills_root = Path(directory) / "nested" / "custom-skills"
            skill_dir = skills_root / "physical-folder"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "frontmatter-name",
                    "Use when testing arbitrary skill roots. Do not trigger for unrelated work.",
                ),
                encoding="utf-8",
            )

            metrics = inventory_skills.inventory(skills_root)["discovery_budget"]

        self.assertEqual(
            metrics["modeled_field_values"]["relative_skill_md_paths"],
            ["custom-skills/physical-folder/SKILL.md"],
        )
        self.assertNotIn("skills/frontmatter-name/SKILL.md", metrics["modeled_field_values"]["relative_skill_md_paths"])

    def test_audit_keeps_discovery_budget_advisory_outside_coverage_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "example-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "example-skill",
                    "Use when testing discovery budgeting. Do not trigger for unrelated work.",
                ),
                encoding="utf-8",
            )

            report = audit_skill_overlap.audit(inventory_skills.inventory(root), threshold=0.22)

        self.assertEqual(report["discovery_budget"]["status"], "advisory")
        self.assertEqual(len(report["advisory_findings"]), 1)
        self.assertEqual(report["advisory_findings"][0]["status"], "advisory")
        self.assertIn("relative paths", report["advisory_findings"][0]["message"])
        self.assertIn("absolute paths", report["advisory_findings"][0]["message"])
        self.assertFalse(any("discovery" in finding["issue"] for finding in report["coverage_findings"]))


class FrontmatterParsingTests(unittest.TestCase):
    def test_parser_decodes_quoted_scalars_and_windows_backslashes(self) -> None:
        description = 'Use when checking a "quoted" C:\\Users\\agent path. Do not trigger for unrelated work.'
        text = f"""---
name: example-skill
description: {json.dumps(description)}
metadata:
  short-description: Write ready-to-send prose in the user's voice.
  author: 'Annie''s'
---
"""

        frontmatter = inventory_skills.parse_frontmatter(text)

        self.assertEqual(frontmatter.diagnostics, ())
        self.assertEqual(frontmatter["description"], description)
        self.assertEqual(frontmatter["metadata.short-description"], "Write ready-to-send prose in the user's voice.")
        self.assertEqual(frontmatter["metadata.author"], "Annie's")

    def test_parser_rejects_unsupported_yaml_forms_with_diagnostics(self) -> None:
        cases = {
            "block": "description: |\n  Use when multiline.\n",
            "comment": "description: Use when testing. # comment\n",
            "invalid-escape": 'description: "Use when checking C:\\work. Do not trigger for unrelated work."\n',
            "duplicate": "description: \"Use when testing. Do not trigger for unrelated work.\"\ndescription: \"Use when testing again. Do not trigger for unrelated work.\"\n",
            "tab": "metadata:\n\tshort-description: bad\n",
            "plain-version": "version: v1\n",
            "numeric-metadata": "metadata:\n  short-description: 1.\n",
            "underscored-number": "metadata:\n  short-description: 1_000\n",
            "sexagesimal": "metadata:\n  short-description: 1:20\n",
            "fraction": "metadata:\n  short-description: .5\n",
            "timestamp": "metadata:\n  short-description: 2026-08-28\n",
        }

        for label, body in cases.items():
            with self.subTest(case=label):
                frontmatter = inventory_skills.parse_frontmatter(f"---\nname: example-skill\n{body}---\n")

                self.assertTrue(frontmatter.diagnostics)

    def test_parser_rejects_decoded_control_and_line_separator_characters(self) -> None:
        separators = ("\r", "\n", "\v", "\f", "\u001c", "\u001d", "\u001e", "\u0085", "\u2028", "\u2029")
        for separator in separators:
            with self.subTest(separator=f"U+{ord(separator):04X}"):
                description = f"Use when checking{separator}separators. Do not trigger for unrelated work."
                frontmatter = inventory_skills.parse_frontmatter(
                    f"---\nname: example-skill\ndescription: {json.dumps(description)}\nmetadata:\n"
                    "  short-description: Check separators.\n---\n"
                )

                self.assertTrue(frontmatter.diagnostics)


class AuditSkillOverlapTests(unittest.TestCase):
    def test_both_description_openers_satisfy_trigger_coverage(self) -> None:
        data = {
            "skills_root": "fixtures",
            "skill_count": 2,
            "skills": [
                {
                    "name": "use-skill",
                    "description": "Use when checking an opener. Do not trigger for other work.",
                    "short_description": "Check opener.",
                    "line_count": 1,
                    "resource_counts": {},
                },
                {
                    "name": "trigger-skill",
                    "description": "Trigger only when checking another opener. Do not trigger for other work.",
                    "short_description": "Check another opener.",
                    "line_count": 1,
                    "resource_counts": {},
                },
            ],
        }

        findings = audit_skill_overlap.audit(data, threshold=0.22)["coverage_findings"]

        self.assertFalse(any("must start" in finding["issue"] for finding in findings))

    def test_coverage_reports_each_missing_discovery_contract_field(self) -> None:
        data = {
            "skills_root": "fixtures",
            "skill_count": 1,
            "skills": [
                {
                    "name": "incomplete-skill",
                    "description": "Use when checking incomplete discovery. Keywords: testing",
                    "short_description": "",
                    "line_count": 1,
                    "resource_counts": {},
                }
            ],
        }

        findings = audit_skill_overlap.audit(data, threshold=0.22)["coverage_findings"]
        messages = {finding["issue"] for finding in findings}

        self.assertIn("description missing explicit Do not trigger exclusion", messages)
        self.assertIn("description contains prohibited Keywords: marker", messages)
        self.assertIn("metadata.short-description must not be empty", messages)

    def test_audit_uses_the_same_description_length_contract_as_validation(self) -> None:
        description = f"Use when {'x' * 1000}. Do not trigger for unrelated work."
        data = {
            "skills_root": "fixtures",
            "skill_count": 1,
            "skills": [
                {
                    "name": "long-description",
                    "description": description,
                    "short_description": "Check description length.",
                    "line_count": 1,
                    "resource_counts": {},
                }
            ],
        }

        findings = audit_skill_overlap.audit(data, threshold=0.22)["coverage_findings"]
        messages = {finding["issue"] for finding in findings}

        self.assertIn(f"frontmatter description is too long ({len(description)} chars > 1024)", messages)

    def test_literal_exclusion_mentions_do_not_satisfy_or_split_the_contract(self) -> None:
        literal_descriptions = (
            'Use when documenting the quoted sentence "Stop. Do not trigger" for users.',
            "Use when documenting the curly quote “Stop. Do not trigger” for users.",
            "Use when documenting the curly apostrophe ‘Stop. Do not trigger’ for users.",
            "Use when documenting the double backtick ``Stop. Do not trigger`` for users.",
            "Use when documenting the triple backtick ```Stop. Do not trigger``` for users.",
            r'Use when documenting the escaped quote \"Stop. Do not trigger\" for users.',
        )
        for literal_only in literal_descriptions:
            with self.subTest(literal=literal_only):
                later_clause = f"{literal_only} Do not trigger for unrelated work."

                self.assertFalse(inventory_skills.has_description_exclusion(literal_only))
                self.assertEqual(inventory_skills.positive_description(literal_only), literal_only)
                self.assertTrue(inventory_skills.has_description_exclusion(later_clause))
                self.assertEqual(inventory_skills.positive_description(later_clause), literal_only)

    def test_exclusions_do_not_create_overlap(self) -> None:
        data = {
            "skills_root": "fixtures",
            "skill_count": 2,
            "skills": [
                {
                    "name": "bee-skill",
                    "description": "Use when building a bumblebee dashboard. Do not trigger for data analysis, code review, or docs.",
                    "short_description": "Build bee dashboards.",
                    "line_count": 1,
                    "resource_counts": {},
                },
                {
                    "name": "telemetry-skill",
                    "description": "Use when running telemetry ingestion. Do not trigger for data analysis, code review, or docs.",
                    "short_description": "Run telemetry ingestion.",
                    "line_count": 1,
                    "resource_counts": {},
                },
            ],
        }

        result = audit_skill_overlap.audit(data, threshold=0.01)

        self.assertEqual(result["overlaps"], [])

    def test_positive_description_overlap_is_retained(self) -> None:
        data = {
            "skills_root": "fixtures",
            "skill_count": 2,
            "skills": [
                {
                    "name": "metadata-audit",
                    "description": "Use when auditing skills and metadata. Do not trigger for unrelated docs.",
                    "short_description": "Audit skill metadata.",
                    "line_count": 1,
                    "resource_counts": {},
                },
                {
                    "name": "reference-audit",
                    "description": "Trigger only when auditing skills and references. Do not trigger for unrelated docs.",
                    "short_description": "Audit skill references.",
                    "line_count": 1,
                    "resource_counts": {},
                },
            ],
        }

        overlaps = audit_skill_overlap.audit(data, threshold=0.3)["overlaps"]

        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0]["shared_terms"], ["auditing", "skills"])


if __name__ == "__main__":
    unittest.main()
