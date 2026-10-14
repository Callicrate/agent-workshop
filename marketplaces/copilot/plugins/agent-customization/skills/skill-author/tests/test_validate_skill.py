from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_skill  # noqa: E402


def skill_text(
    name: str,
    description: str,
    *,
    short_description: str | None = "Test focused skills.",
) -> str:
    """Return a minimally valid skill document for discovery-contract tests."""
    metadata = ""
    if short_description is not None:
        metadata = f"metadata:\n  short-description: {short_description}\n"
    return f"""---
name: {name}
description: \"{description}\"
{metadata}---

# Test Skill

## When to Use

- Test the validator.

## When NOT to Use

- Test unrelated workflows.

## Workflow

1. Validate the skill.

## Deterministic Tools

| Tool | Use When | Outcome |
|---|---|---|
| none | Never | No operation |

## References
"""


class ValidateDiscoveryContractTests(unittest.TestCase):
    def test_valid_use_when_and_trigger_only_when_descriptions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            use_skill = root / "use-skill"
            trigger_skill = root / "trigger-skill"
            use_skill.mkdir()
            trigger_skill.mkdir()
            (use_skill / "SKILL.md").write_text(
                skill_text(
                    "use-skill",
                    "Use when validating description openers. Do not trigger for unrelated work.",
                ),
                encoding="utf-8",
            )
            (trigger_skill / "SKILL.md").write_text(
                skill_text(
                    "trigger-skill",
                    "Trigger only when validating description openers. Do not trigger for unrelated work.",
                ),
                encoding="utf-8",
            )

            self.assertEqual(validate_skill.validate_skill(str(use_skill)), [])
            self.assertEqual(validate_skill.validate_skill(str(trigger_skill)), [])

    def test_discovery_contract_failures_are_errors_in_strict_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "invalid-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "invalid-skill",
                    "Validate descriptions with forbidden Keywords: marker.",
                    short_description=None,
                ),
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))
            messages = {issue.message for issue in issues}

            self.assertIn("description must start with Use when or Trigger only when", messages)
            self.assertIn("description missing explicit Do not trigger exclusion", messages)
            self.assertIn("description contains prohibited Keywords: marker", messages)
            self.assertIn("missing frontmatter field: metadata.short-description", messages)
            self.assertTrue(all(issue.severity == "error" for issue in issues))

            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPTS / "validate_skill.py"), str(skill_dir), "--strict"],
                capture_output=True,
                check=False,
                encoding="utf-8",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("ERROR: description must start", result.stdout)

    def test_short_description_cannot_exceed_ten_words(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "long-short-description"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text(
                    "long-short-description",
                    "Use when validating short descriptions. Do not trigger for unrelated work.",
                    short_description="one two three four five six seven eight nine ten eleven",
                ),
                encoding="utf-8",
            )

            messages = {issue.message for issue in validate_skill.validate_skill(str(skill_dir))}

            self.assertIn("metadata.short-description is too long (11 words > 10)", messages)

    def test_invalid_frontmatter_syntax_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "invalid-frontmatter"
            skill_dir.mkdir()
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                skill_text(
                    "invalid-frontmatter",
                    "Use when testing invalid frontmatter. Do not trigger for unrelated work.",
                ).replace(
                    'description: "Use when testing invalid frontmatter. Do not trigger for unrelated work."',
                    "description: |\n  Use when multiline.",
                ),
                encoding="utf-8",
            )

            messages = {issue.message for issue in validate_skill.validate_skill(str(skill_dir))}

            self.assertTrue(any("block scalars are unsupported" in message for message in messages))


class ValidateShellScriptsTests(unittest.TestCase):
    def test_bash_syntax_check_reads_source_from_stdin(self) -> None:
        source = "#!/usr/bin/env bash\nset -euo pipefail\necho ok\n"

        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "check.sh").write_bytes(source.encode("utf-8"))

            completed = subprocess.CompletedProcess(["bash", "-n"], 0, b"", b"")
            with (
                patch.object(validate_skill.shutil, "which", return_value="bash"),
                patch.object(validate_skill.subprocess, "run", return_value=completed) as run,
            ):
                issues = validate_skill.validate_shell_scripts(skill_dir)

        self.assertEqual(issues, [])
        run.assert_called_once_with(
            ["bash", "-n"],
            input=source.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=10,
        )


class ValidatePythonScriptsTests(unittest.TestCase):
    def test_python_validation_does_not_create_bytecode_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "helper.py").write_text("def helper() -> int:\n    return 1\n", encoding="utf-8")

            issues = validate_skill.validate_python_scripts(skill_dir)

            self.assertEqual(issues, [])
            self.assertFalse((scripts_dir / "__pycache__").exists())


class ValidateMarkdownLinksTests(unittest.TestCase):
    def make_resource(self, root: Path, relative_path: str, content: str = "resource") -> Path:
        """Create one bundled regular file for a link-validation fixture."""
        resource = root / relative_path
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_text(content, encoding="utf-8")
        return resource

    def test_contained_resource_markdown_may_walk_up_without_escaping_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            self.make_resource(skill_dir, "references/guide.md", "[target](../assets/target.txt)\n")
            self.make_resource(skill_dir, "assets/target.txt")

            self.assertEqual(validate_skill.validate_markdown_resource_links(skill_dir), [])

    def test_unsafe_local_syntax_is_rejected_before_any_target_probe(self) -> None:
        unsafe_targets = (
            "../outside.md",
            "/absolute.md",
            "//server/share.md",
            r"\\server\share\file.md",
            r"\\?\C:\device\file.md",
            "C:/drive.md",
            "C:drive.md",
            "file.txt:stream",
            "encoded%2fseparator.md",
            "encoded%2E%2Eescape.md",
            "nul\x00target.md",
        )
        nested_label_links = (
            "[outer [inner]](../nested-escape.md)",
            "[outer [inner]](/nested-absolute.md)",
        )
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            skill_dir.mkdir()
            text = "\n".join(
                [*(f"[link]({target})" for target in unsafe_targets), *nested_label_links]
            )
            with (
                patch.object(validate_skill.os, "scandir", side_effect=AssertionError("unsafe target was probed")),
                patch.object(validate_skill.os, "lstat", side_effect=AssertionError("unsafe target was probed")),
                patch.object(validate_skill.Path, "resolve", side_effect=AssertionError("unsafe target was resolved")),
                patch.object(validate_skill.Path, "exists", side_effect=AssertionError("unsafe target was checked")),
            ):
                issues = validate_skill.validate_relative_links(skill_dir, text)

        self.assertEqual(len(issues), len(unsafe_targets) + len(nested_label_links))
        self.assertTrue(all("unsafe linked resource" in issue.message for issue in issues))

    def test_exact_case_and_regular_file_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            self.make_resource(skill_dir, "references/Guide.md")
            (skill_dir / "templates").mkdir()
            self.make_resource(skill_dir, "normal.txt")

            case_issues = validate_skill.validate_relative_links(skill_dir, "[guide](references/guide.md)")
            directory_issues = validate_skill.validate_relative_links(skill_dir, "[templates](templates)")
            special_target = validate_skill.classify_local_target("normal.txt", ())
            self.assertIsInstance(special_target, validate_skill.LocalTarget)
            with patch.object(
                validate_skill.os,
                "lstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0),
            ):
                special_issue = validate_skill._contained_regular_file_issue(
                    skill_dir,
                    special_target,
                    "normal.txt",
                    "linked resource",
                )

        self.assertIn("case mismatch", case_issues[0].message)
        self.assertIn("not a regular file", directory_issues[0].message)
        self.assertIsNotNone(special_issue)
        self.assertIn("not a regular file", special_issue.message)

    def test_internal_reparse_is_rejected_but_outer_skill_symlink_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "example-skill"
            self.make_resource(skill_dir, "assets/target.txt")
            target = validate_skill.classify_local_target("assets/target.txt", ())
            self.assertIsInstance(target, validate_skill.LocalTarget)
            with patch.object(
                validate_skill.os,
                "lstat",
                return_value=SimpleNamespace(
                    st_mode=stat.S_IFREG,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
                ),
            ):
                reparse_issue = validate_skill._contained_regular_file_issue(
                    skill_dir,
                    target,
                    "assets/target.txt",
                    "linked resource",
                )

            outer_target = fixture_root / "outer-target"
            (outer_target / "references").mkdir(parents=True)
            (outer_target / "references" / "guide.md").write_text("guide", encoding="utf-8")
            (outer_target / "SKILL.md").write_text(
                skill_text(
                    "outer-target",
                    "Use when testing an outer symlink. Do not trigger for unrelated work.",
                ).replace("## References\n", "## References\n\n[guide](references/guide.md)\n"),
                encoding="utf-8",
            )
            outer_alias = fixture_root / "outer-alias"
            try:
                outer_alias.symlink_to(outer_target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            outer_issues = validate_skill.validate_skill(str(outer_alias))

        self.assertIsNotNone(reparse_issue)
        self.assertIn("symlink or reparse", reparse_issue.message)
        self.assertEqual(outer_issues, [])

    def test_internal_skill_entry_symlink_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "example-skill"
            skill_dir.mkdir()
            external_skill = fixture_root / "external-skill.md"
            external_skill.write_text(
                skill_text(
                    "external-skill",
                    "Use when testing a linked entrypoint. Do not trigger for unrelated work.",
                ),
                encoding="utf-8",
            )
            internal_entrypoint = skill_dir / "SKILL.md"
            try:
                internal_entrypoint.symlink_to(external_skill)
            except OSError as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            directory_issues = validate_skill.validate_skill(str(skill_dir))
            file_issues = validate_skill.validate_skill(str(internal_entrypoint))

        self.assertEqual(len(directory_issues), 1)
        self.assertEqual(len(file_issues), 1)
        self.assertTrue(all("regular file" in issue.message for issue in (*directory_issues, *file_issues)))

    def test_internal_skill_entry_reparse_is_rejected_without_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            skill_dir.mkdir()
            skill_entrypoint = skill_dir / "SKILL.md"
            skill_entrypoint.write_text("unreadable", encoding="utf-8")
            reparse_status = SimpleNamespace(
                st_mode=stat.S_IFREG,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
            with (
                patch.object(validate_skill.os, "lstat", return_value=reparse_status),
                patch.object(validate_skill.Path, "read_text", side_effect=AssertionError("reparse entry was read")),
            ):
                issues = validate_skill.validate_skill(str(skill_dir))

        self.assertEqual(len(issues), 1)
        self.assertIn("regular file", issues[0].message)

    def test_fragments_inline_reference_and_image_links_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            self.make_resource(skill_dir, "references/guide.md")
            self.make_resource(skill_dir, "assets/image.png")
            self.make_resource(skill_dir, "references/other.md")
            text = "\n".join(
                (
                    "[fragment](#heading)",
                    "[guide](references/guide.md#details)",
                    "![image](assets/image.png)",
                    "[reference]: <references/other.md>",
                )
            )

            issues = validate_skill.validate_relative_links(skill_dir, text)

        self.assertEqual(issues, [])

    def test_fences_inline_code_and_escaped_examples_are_not_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            skill_dir.mkdir()
            text = """```markdown
[missing](outside.md)
```
~~~markdown
![missing](outside.png)
~~~
`[missing](outside.md)`
\\[missing](outside.md)
"""

            issues = validate_skill.validate_relative_links(skill_dir, text)

        self.assertEqual(issues, [])

    def test_titles_and_malformed_destinations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "example-skill"
            self.make_resource(skill_dir, "references/guide.md")
            text = "\n".join(
                (
                    '[title](references/guide.md "not supported")',
                    '[angle-title](<references/guide.md> "not supported")',
                    "[unterminated](references/guide.md",
                    "[empty]:",
                )
            )

            issues = validate_skill.validate_relative_links(skill_dir, text)

        self.assertEqual(len(issues), 4)
        self.assertTrue(any("titles are unsupported" in issue.message for issue in issues))
        self.assertTrue(any("unterminated" in issue.message for issue in issues))
        self.assertTrue(any("empty Markdown reference" in issue.message for issue in issues))

    def test_project_marker_remains_a_normal_contained_file_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = fixture_root / "project-skill"
            skill_dir.mkdir()
            (skill_dir / "project-specific-skill").write_text(str(project_dir.resolve()), encoding="utf-8")
            text = skill_text(
                "project-skill",
                "Use when testing a marked skill. Do not trigger for unrelated work.",
            ).replace(
                "\n# Test Skill",
                "\n[project-specific-skill](project-specific-skill)\n\n# Test Skill",
                1,
            )
            (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_copied_package_has_no_sibling_skill_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "isolated-skill"
            self.make_resource(skill_dir, "references/guide.md")
            copied_root = fixture_root / "copied"
            copied_root.mkdir()
            copied_skill = copied_root / "isolated-skill"
            shutil.copytree(skill_dir, copied_skill)
            contained_issues = validate_skill.validate_relative_links(copied_skill, "[guide](references/guide.md)")
            sibling_issues = validate_skill.validate_relative_links(copied_skill, "[other](../other-skill/SKILL.md)")

        self.assertEqual(contained_issues, [])
        self.assertEqual(len(sibling_issues), 1)
        self.assertIn("lexical escape", sibling_issues[0].message)


if __name__ == "__main__":
    unittest.main()
