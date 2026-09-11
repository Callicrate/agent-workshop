from __future__ import annotations

import json
import os
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
import project_specific  # noqa: E402
import scaffold_skill  # noqa: E402


def skill_text(name: str, *, marker_link: bool = False, fenced_marker_link: bool = False) -> str:
    """Return the smallest valid skill document for validator tests."""
    marker_line = ""
    if marker_link:
        marker_line = "\n[project-specific-skill](project-specific-skill)\n"
    if fenced_marker_link:
        marker_line = "\n```markdown\n[project-specific-skill](project-specific-skill)\n```\n"
    return f"""---
name: {name}
description: \"Use when testing a focused skill. Do not trigger for unrelated work.\"
metadata:
  short-description: Test a focused skill.
---
{marker_line}

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


def with_first_body_content(text: str, content: str) -> str:
    """Insert content before the standard fixture title after frontmatter."""
    return text.replace("---\n\n# Test Skill", f"---\n\n{content}\n# Test Skill", 1)


class ProjectSpecificValidationTests(unittest.TestCase):
    def make_skill(
        self,
        directory: Path,
        name: str,
        marker_content: str,
        *,
        marker_link: bool = True,
        fenced_marker_link: bool = False,
    ) -> Path:
        """Create one project-specific skill fixture."""
        skill_dir = directory / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            skill_text(name, marker_link=marker_link, fenced_marker_link=fenced_marker_link),
            encoding="utf-8",
        )
        (skill_dir / "project-specific-skill").write_bytes(marker_content.encode("utf-8"))
        return skill_dir

    def test_ordinary_skill_passes_without_a_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "ordinary-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(skill_text("ordinary-skill"), encoding="utf-8")

            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_valid_markers_allow_no_newline_lf_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Mixed Case Project"
            project_dir.mkdir()
            resolved_project = str(project_dir.resolve())

            for suffix in ("", "\n", "\r\n"):
                with self.subTest(suffix=repr(suffix)):
                    skill_dir = self.make_skill(
                        fixture_root,
                        f"mixed-case-project-skill-{len(suffix)}",
                        f"{resolved_project}{suffix}",
                    )

                    self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_invalid_markers_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            project_file = fixture_root / "project-file"
            project_file.write_text("not a directory", encoding="utf-8")
            project_path = str(project_dir.resolve())
            root_path = Path(project_path).anchor

            cases = {
                "relative": "relative/project",
                "missing": str((fixture_root / "missing").resolve()),
                "file": str(project_file.resolve()),
                "root": root_path,
                "unc": r"\\server\share\project",
                "device": r"\\?\C:\project",
                "bom": f"\ufeff{project_path}",
                "quotes": f'"{project_path}"',
                "leading-whitespace": f" {project_path}",
                "trailing-whitespace": f"{project_path} ",
                "blank": "",
                "two-paths": f"{project_path}\n{project_path}",
            }

            for label, marker_content in cases.items():
                with self.subTest(case=label):
                    skill_dir = self.make_skill(
                        fixture_root,
                        f"project-marker-{label}",
                        marker_content,
                    )
                    self.assertTrue(validate_skill.validate_skill(str(skill_dir)))

    def test_prefix_mismatch_and_missing_link_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project Name"
            project_dir.mkdir()
            marker_content = str(project_dir.resolve())

            mismatched_skill = self.make_skill(fixture_root, "other-skill", marker_content)
            missing_link_skill = self.make_skill(
                fixture_root,
                "project-name-skill",
                marker_content,
                marker_link=False,
            )

            self.assertTrue(validate_skill.validate_skill(str(mismatched_skill)))
            self.assertTrue(validate_skill.validate_skill(str(missing_link_skill)))

    def test_marker_link_inside_a_fence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-skill",
                str(project_dir.resolve()),
                marker_link=False,
                fenced_marker_link=True,
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("must link" in issue.message for issue in issues))

    def test_marker_link_must_be_rendered_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            marker_content = str(project_dir.resolve())
            non_rendered_links = {
                "tilde-fence": "~~~markdown\n[project-specific-skill](project-specific-skill)\n~~~\n",
                "indented": "    [project-specific-skill](project-specific-skill)\n",
                "escaped": "\\[project-specific-skill](project-specific-skill)\n",
                "inline-code": "`[project-specific-skill](project-specific-skill)`\n",
                "html-code": "<code>[project-specific-skill](project-specific-skill)</code>\n",
                "comment": "<!--[project-specific-skill](project-specific-skill)-->\n",
                "attributes": "[project-specific-skill](project-specific-skill \"title\")\n",
                "image": "![project-specific-skill](project-specific-skill)\n",
                "unmatched-backtick": "`[project-specific-skill](project-specific-skill)\n",
            }

            for label, reference_text in non_rendered_links.items():
                with self.subTest(case=label):
                    skill_dir = fixture_root / f"project-{label}"
                    skill_dir.mkdir()
                    (skill_dir / "SKILL.md").write_text(
                        with_first_body_content(
                            skill_text(f"project-{label}", marker_link=False),
                            reference_text,
                        ),
                        encoding="utf-8",
                    )
                    (skill_dir / "project-specific-skill").write_bytes(marker_content.encode("utf-8"))

                    issues = validate_skill.validate_skill(str(skill_dir))

                    self.assertTrue(any("must link" in issue.message for issue in issues))

    def test_marker_link_in_frontmatter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-frontmatter",
                str(project_dir.resolve()),
                marker_link=False,
            )
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8").replace(
                    "description: \"Use when testing a focused skill. Do not trigger for unrelated work.\"",
                    "description: \"Use when [project-specific-skill](project-specific-skill).\"",
                ),
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("must link" in issue.message for issue in issues))

    def test_marker_link_later_in_the_body_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-later-link",
                str(project_dir.resolve()),
                marker_link=False,
            )
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8")
                + "\n[project-specific-skill](project-specific-skill)\n",
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("must link" in issue.message for issue in issues))

    def test_blank_body_lines_before_the_marker_link_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-blank-lines",
                str(project_dir.resolve()),
            )
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8").replace(
                    "---\n\n[project-specific-skill](project-specific-skill)",
                    "---\n\n\n   \n[project-specific-skill](project-specific-skill)",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_visible_marker_link_without_a_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "project-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text("project-skill", marker_link=True),
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("reserved for a marked skill" in issue.message for issue in issues))

    def test_misplaced_marker_link_without_a_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "ordinary-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text("ordinary-skill")
                + "\n- [project-specific-skill](project-specific-skill)\n",
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("reserved" in issue.message for issue in issues))

    def test_duplicate_later_marker_link_fails_for_a_marked_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-duplicate-link",
                str(project_dir.resolve()),
            )
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8")
                + "\n- [project-specific-skill](project-specific-skill)\n",
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("exactly once" in issue.message for issue in issues))

    def test_single_fixed_marker_link_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-single-link",
                str(project_dir.resolve()),
            )

            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_non_rendered_marker_literal_without_a_marker_is_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skill_dir = fixture_root / "ordinary-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                skill_text("ordinary-skill")
                + "~~~markdown\n[project-specific-skill](project-specific-skill)\n~~~\n",
                encoding="utf-8",
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("reserved" in issue.message for issue in issues))

    def test_invalid_project_leaf_slug_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "项目"
            project_dir.mkdir()
            skill_dir = self.make_skill(
                fixture_root,
                "project-skill",
                str(project_dir.resolve()),
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("no valid ASCII slug" in issue.message for issue in issues))

    def test_unsafe_windows_paths_fail_before_filesystem_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            original_exists = Path.exists
            original_lstat = Path.lstat
            original_resolve = Path.resolve

            for label, marker_content in {
                "unc": r"\\server\share\project",
                "device": r"\\?\C:\project",
            }.items():
                with self.subTest(case=label):
                    skill_dir = self.make_skill(
                        fixture_root,
                        f"project-{label}-skill",
                        marker_content,
                    )

                    def guarded_exists(path: Path) -> bool:
                        self.assertNotEqual(str(path), marker_content)
                        return original_exists(path)

                    def guarded_lstat(path: Path) -> os.stat_result:
                        self.assertNotEqual(str(path), marker_content)
                        return original_lstat(path)

                    def guarded_resolve(path: Path, strict: bool = False) -> Path:
                        self.assertNotEqual(str(path), marker_content)
                        return original_resolve(path, strict=strict)

                    with (
                        patch.object(validate_skill.Path, "exists", new=guarded_exists),
                        patch.object(validate_skill.Path, "lstat", new=guarded_lstat),
                        patch.object(validate_skill.Path, "resolve", new=guarded_resolve),
                    ):
                        issues = validate_skill.validate_skill(str(skill_dir))

                    self.assertTrue(issues)

    def test_canonical_project_path_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            aliased_path = f"{project_dir}{os.sep}."
            skill_dir = self.make_skill(fixture_root, "project-skill", aliased_path)

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("canonical resolved" in issue.message for issue in issues))

    def test_symlink_and_reparse_detection_helper(self) -> None:
        symlink_entry = SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        reparse_entry = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=project_specific.REPARSE_POINT_ATTRIBUTE,
        )
        regular_entry = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0)

        self.assertTrue(project_specific.is_symlink_or_reparse(symlink_entry))
        self.assertTrue(project_specific.is_symlink_or_reparse(reparse_entry))
        self.assertFalse(project_specific.is_symlink_or_reparse(regular_entry))

    def test_reparse_marker_is_rejected_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(fixture_root, "project-skill", str(project_dir.resolve()))
            reparse_entry = SimpleNamespace(
                st_mode=stat.S_IFREG,
                st_file_attributes=project_specific.REPARSE_POINT_ATTRIBUTE,
            )

            with patch.object(validate_skill.Path, "lstat", return_value=reparse_entry):
                issues = validate_skill.validate_project_specific_skill(
                    skill_dir,
                    (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
                )

            self.assertTrue(any("symlink or reparse" in issue.message for issue in issues))

    def test_broken_symlink_marker_is_rejected_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skill_dir = self.make_skill(fixture_root, "project-skill", str(project_dir.resolve()))
            symlink_entry = SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)

            with patch.object(project_specific.Path, "lstat", return_value=symlink_entry):
                issues = validate_skill.validate_project_specific_skill(
                    skill_dir,
                    (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
                )

            self.assertTrue(any("symlink or reparse" in issue.message for issue in issues))

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture is covered by mocked Windows reparse tests")
    def test_parent_symlink_is_rejected_before_project_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            target_parent = fixture_root / "target"
            project_dir = target_parent / "Project"
            project_dir.mkdir(parents=True)
            linked_parent = fixture_root / "linked-parent"
            linked_parent.symlink_to(target_parent, target_is_directory=True)
            skill_dir = self.make_skill(
                fixture_root,
                "project-skill",
                str(linked_parent / "Project"),
            )

            issues = validate_skill.validate_skill(str(skill_dir))

            self.assertTrue(any("must not use a symlink or reparse" in issue.message for issue in issues))

    def test_reparse_parent_blocks_descendant_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            reparse_parent = fixture_root / "reparse-parent"
            descendant = reparse_parent / "Project"
            reparse_parent.mkdir()
            descendant.mkdir()
            original_exists = Path.exists
            original_lstat = Path.lstat
            original_is_dir = Path.is_dir
            original_resolve = Path.resolve
            reparse_entry = SimpleNamespace(
                st_mode=stat.S_IFREG,
                st_file_attributes=project_specific.REPARSE_POINT_ATTRIBUTE,
            )

            def guarded_lstat(path: Path) -> os.stat_result:
                if path == descendant:
                    self.fail("descendant lstat must not follow a reparse parent")
                if path == reparse_parent:
                    return reparse_entry
                return original_lstat(path)

            def guarded_exists(path: Path) -> bool:
                if path == descendant:
                    self.fail("descendant exists must not run after a reparse parent")
                return original_exists(path)

            def guarded_is_dir(path: Path) -> bool:
                if path == descendant:
                    self.fail("descendant is_dir must not run after a reparse parent")
                return original_is_dir(path)

            def guarded_resolve(path: Path, strict: bool = False) -> Path:
                if path == descendant:
                    self.fail("descendant resolve must not run after a reparse parent")
                return original_resolve(path, strict=strict)

            with (
                patch.object(project_specific.Path, "exists", new=guarded_exists),
                patch.object(project_specific.Path, "lstat", new=guarded_lstat),
                patch.object(project_specific.Path, "is_dir", new=guarded_is_dir),
                patch.object(project_specific.Path, "resolve", new=guarded_resolve),
            ):
                _, _, error = project_specific.resolve_marker_project_path(str(descendant))

            self.assertIn("must not use a symlink or reparse", error or "")

    def test_marker_reader_rejects_a_descriptor_swap_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            marker_path = fixture_root / "project-specific-skill"
            replacement_path = fixture_root / "replacement"
            marker_path.write_bytes(b"local marker")
            replacement_path.write_bytes(b"replacement marker")
            original_open = os.open

            def swapped_open(path: str | Path, flags: int) -> int:
                self.assertEqual(Path(path), marker_path)
                return original_open(replacement_path, flags)

            with (
                patch.object(project_specific.os, "open", side_effect=swapped_open),
                patch.object(project_specific.os, "read", side_effect=AssertionError("must not read swapped descriptor")),
            ):
                with self.assertRaises(project_specific.MarkerReadError):
                    project_specific.read_marker_bytes(marker_path)


class ProjectSpecificScaffoldTests(unittest.TestCase):
    scaffold_script = SCRIPTS / "scaffold_skill.py"

    def run_scaffold(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """Run the real scaffold CLI with captured output."""
        return subprocess.run(
            [sys.executable, "-B", str(self.scaffold_script), *arguments],
            capture_output=True,
            check=False,
            encoding="utf-8",
        )

    def test_ordinary_slugify_preserves_legacy_unicode_behavior(self) -> None:
        self.assertEqual(scaffold_skill.slugify("Café Skill"), "caf-skill")
        self.assertEqual(scaffold_skill.slugify("naïve helper"), "na-ve-helper")
        self.assertEqual(scaffold_skill.slugify("résumé"), "r-sum")

    def test_scaffold_derives_a_ten_word_short_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skills_root = fixture_root / "skills"
            description = (
                "Use when drafting alpha beta gamma delta epsilon zeta eta theta iota kappa lambda. "
                "Do not trigger for unrelated work."
            )

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--name",
                "Useful Skill",
                "--description",
                description,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_dir = skills_root / "useful-skill"
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            frontmatter = validate_skill.parse_frontmatter(skill_text)
            self.assertEqual(
                frontmatter["metadata.short-description"],
                "drafting alpha beta gamma delta epsilon zeta eta theta iota",
            )
            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_scaffold_json_quotes_the_full_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skills_root = fixture_root / "skills"
            description = 'Use when checking a "quoted" C:\\Users\\agent path. Do not trigger for unrelated work.'

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--name",
                "Quoted Skill",
                "--description",
                description,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_text = (skills_root / "quoted-skill" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"description: {json.dumps(description)}", skill_text)
            self.assertEqual(validate_skill.parse_frontmatter(skill_text)["description"], description)

    def test_scaffold_substitutes_template_values_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skills_root = fixture_root / "skills"
            description = (
                'Use when preserving $skill_name ${skill_name} $title ${title} $description ${description} '
                '$short_description ${short_description} and "$" literals. Do not trigger for unrelated work.'
            )

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--name",
                "Single Pass Skill",
                "--description",
                description,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            frontmatter = validate_skill.parse_frontmatter(
                (skills_root / "single-pass-skill" / "SKILL.md").read_text(encoding="utf-8")
            )
            self.assertEqual(frontmatter["description"], description)
            self.assertIn("${short_description}", frontmatter["metadata.short-description"])

    def test_scaffold_rejects_line_separators_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            separators = {
                "lf": "\n",
                "cr": "\r",
                "vertical-tab": "\v",
                "form-feed": "\f",
                "file-separator": "\u001c",
                "group-separator": "\u001d",
                "record-separator": "\u001e",
                "nel": "\u0085",
                "line-separator": "\u2028",
                "paragraph-separator": "\u2029",
            }
            for label, line_break in separators.items():
                with self.subTest(line_break=label):
                    skills_root = fixture_root / label / "skills"
                    result = self.run_scaffold(
                        "--root",
                        str(skills_root),
                        "--name",
                        "Invalid Skill",
                        "--description",
                        f"Use when testing invalid{line_break}input. Do not trigger for unrelated work.",
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("description must be a single line", result.stderr)
                    self.assertFalse(skills_root.exists())

    def test_scaffold_project_root_creates_exact_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Mixed Case Project"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "Useful Skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_dir = skills_root / "mixed-case-project-useful-skill"
            marker = skill_dir / "project-specific-skill"
            self.assertEqual(marker.read_bytes(), str(project_dir.resolve()).encode("utf-8"))
            self.assertIn("[project-specific-skill](project-specific-skill)", (skill_dir / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue(
                validate_skill.has_required_project_marker_link(
                    (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                )
            )
            self.assertEqual(validate_skill.validate_skill(str(skill_dir)), [])

    def test_invalid_project_roots_create_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_file = fixture_root / "project-file"
            project_file.write_text("not a directory", encoding="utf-8")
            invalid_roots = (
                str(fixture_root / "missing"),
                str(project_file),
                Path(fixture_root).anchor,
                "relative-project",
            )

            for index, project_root in enumerate(invalid_roots):
                with self.subTest(project_root=project_root):
                    skills_root = fixture_root / f"skills-{index}"
                    result = self.run_scaffold(
                        "--root",
                        str(skills_root),
                        "--project-root",
                        project_root,
                        "--name",
                        "skill",
                        "--description",
                        "Use when creating a test skill. Do not trigger for unrelated work.",
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(skills_root.exists())

    def test_empty_project_root_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            skills_root = fixture_root / "skills"

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--project-root",
                "",
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(skills_root.exists())

    def test_project_root_with_quotes_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project's Name"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(skills_root.exists())

    def test_already_prefixed_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project Alpha"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "project-alpha-skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(skills_root.exists())

    def test_force_cannot_rebind_an_existing_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            first_project = fixture_root / "first" / "Same Project"
            second_project = fixture_root / "second" / "Same Project"
            first_project.mkdir(parents=True)
            second_project.mkdir(parents=True)
            skills_root = fixture_root / "skills"
            arguments = (
                "--root",
                str(skills_root),
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            created = self.run_scaffold("--project-root", str(first_project), *arguments)
            rebound = self.run_scaffold("--project-root", str(second_project), "--force", *arguments)

            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertNotEqual(rebound.returncode, 0)
            marker = skills_root / "same-project-skill" / "project-specific-skill"
            self.assertEqual(marker.read_bytes(), str(first_project.resolve()).encode("utf-8"))

    def test_force_without_project_root_cannot_overwrite_marked_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"
            arguments = (
                "--root",
                str(skills_root),
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            created = self.run_scaffold("--project-root", str(project_dir), *arguments)
            skill_md = skills_root / "project-skill" / "SKILL.md"
            original_skill = skill_md.read_bytes()
            overwritten = self.run_scaffold(
                "--root",
                str(skills_root),
                "--name",
                "project-skill",
                "--description",
                "Use when creating a replacement test skill. Do not trigger for unrelated work.",
                "--force",
            )

            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertNotEqual(overwritten.returncode, 0)
            self.assertEqual(skill_md.read_bytes(), original_skill)

    def test_force_accepts_valid_marker_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"
            arguments = (
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            created = self.run_scaffold(*arguments)
            marker = skills_root / "project-skill" / "project-specific-skill"
            original_marker_path = str(project_dir.resolve())

            self.assertEqual(created.returncode, 0, created.stderr)
            for suffix in ("\n", "\r\n"):
                with self.subTest(suffix=repr(suffix)):
                    marker.write_bytes(f"{original_marker_path}{suffix}".encode("utf-8"))
                    forced = self.run_scaffold("--force", *arguments)
                    self.assertEqual(forced.returncode, 0, forced.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows case identity is case-insensitive")
    def test_windows_case_variant_marker_matches_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / "Project"
            project_dir.mkdir()
            skills_root = fixture_root / "skills"
            arguments = (
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "skill",
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            created = self.run_scaffold(*arguments)
            marker = skills_root / "project-skill" / "project-specific-skill"
            marker.write_bytes(str(project_dir.resolve()).lower().encode("utf-8"))
            forced = self.run_scaffold("--force", *arguments)

            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(validate_skill.validate_skill(str(marker.parent)), [])
            self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_long_project_specific_name_is_rejected_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            project_dir = fixture_root / ("p" * 50)
            project_dir.mkdir()
            skills_root = fixture_root / "skills"

            result = self.run_scaffold(
                "--root",
                str(skills_root),
                "--project-root",
                str(project_dir),
                "--name",
                "s" * 20,
                "--description",
                "Use when creating a test skill. Do not trigger for unrelated work.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(skills_root.exists())


if __name__ == "__main__":
    unittest.main()
