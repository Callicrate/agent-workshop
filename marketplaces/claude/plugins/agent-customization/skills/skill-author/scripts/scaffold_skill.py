"""Create a new skill directory from the canonical minimal template."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from string import Template

from inventory_skills import (
    DESCRIPTION_OPENER_RE,
    discovery_contract_issues,
    positive_description,
    unsupported_scalar_character,
)
from project_specific import (
    MarkerMissingError,
    MarkerReadError,
    PROJECT_MARKER_LINK,
    PROJECT_MARKER_NAME,
    parse_marker_bytes,
    read_marker_bytes,
    resolve_marker_project_path,
    same_project_identity,
)

AGENTS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SKILLS_ROOT = AGENTS_ROOT / "skills"
TEMPLATE_PATH = AGENTS_ROOT / "skills" / "skill-author" / "templates" / "skill-minimal.md"
OPENAI_YAML_TEMPLATE_PATH = AGENTS_ROOT / "skills" / "skill-author" / "templates" / "openai-agent-metadata.yaml"
NAME_MAX_LENGTH = 64
SHORT_DESCRIPTION_MAX_WORDS = 10
FRONTMATTER_END_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def slugify(name: str) -> str:
    """Convert a free-form skill name to a canonical folder slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ValueError("skill name must contain at least one alphanumeric character")
    return slug


def title_case(name: str) -> str:
    """Convert a slug to a title suitable for a markdown heading."""
    return " ".join(part.capitalize() for part in name.split("-"))


def derive_short_description(description: str, skill_name: str) -> str:
    """Derive a bounded local compact summary from the positive description clause."""
    positive_clause = DESCRIPTION_OPENER_RE.sub("", positive_description(description), count=1).strip()
    words = positive_clause.split()
    if not words:
        words = title_case(skill_name).split()
    return " ".join(words[:SHORT_DESCRIPTION_MAX_WORDS])


def validate_scaffold_description(description: str, skill_name: str) -> str:
    """Return a safe derived short description or reject invalid scaffold input early."""
    unsupported_character = unsupported_scalar_character(description)
    if unsupported_character:
        raise ValueError(
            "description must be a single line without control or line-separator characters "
            f"(U+{ord(unsupported_character):04X})"
        )
    short_description = derive_short_description(description, skill_name)
    issues = discovery_contract_issues(
        {
            "description": description,
            "metadata.short-description": short_description,
        }
    )
    if issues:
        raise ValueError(f"invalid description: {'; '.join(issues)}")
    return short_description


def resolve_project_root(value: str) -> tuple[Path, str]:
    """Validate and resolve a local project directory plus its folder slug."""
    resolved_project_root, project_slug, project_error = resolve_marker_project_path(value)
    if project_error:
        raise ValueError(f"invalid project root: {project_error}")
    if resolved_project_root is None or project_slug is None:
        raise ValueError("invalid project root")
    return resolved_project_root, project_slug


def marker_matches(marker_bytes: bytes, expected: Path) -> bool:
    """Return whether a valid existing marker preserves the same project identity."""
    project_path_text, marker_error = parse_marker_bytes(marker_bytes)
    if marker_error:
        raise ValueError(f"invalid existing {PROJECT_MARKER_NAME}: {marker_error}")
    if project_path_text is None:
        raise ValueError(f"invalid existing {PROJECT_MARKER_NAME}")

    project_path, _, project_error = resolve_marker_project_path(project_path_text)
    if project_error:
        raise ValueError(f"invalid existing {PROJECT_MARKER_NAME}: {project_error}")
    if project_path is None:
        raise ValueError(f"invalid existing {PROJECT_MARKER_NAME}")
    return same_project_identity(project_path, expected)


def insert_project_marker_link(content: str) -> str:
    """Place the project marker link at the required deterministic body position."""
    frontmatter_match = FRONTMATTER_END_RE.match(content)
    if not frontmatter_match:
        raise ValueError("skill template is missing a complete YAML frontmatter delimiter")
    return f"{content[:frontmatter_match.end()]}{PROJECT_MARKER_LINK}\n{content[frontmatter_match.end():]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new skill from the canonical template")
    parser.add_argument("--name", required=True, help="Skill name or slug")
    parser.add_argument("--description", required=True, help="Frontmatter description")
    parser.add_argument(
        "--root",
        default=str(DEFAULT_SKILLS_ROOT),
        help="Skills root directory (defaults to the shared skills library)",
    )
    parser.add_argument(
        "--project-root",
        help="Absolute existing local project directory for a project-specific skill",
    )
    parser.add_argument("--with-assets", action="store_true", help="Create an assets/ directory")
    parser.add_argument("--with-references", action="store_true", help="Create a references/ directory")
    parser.add_argument("--with-scripts", action="store_true", help="Create a scripts/ directory")
    parser.add_argument("--with-templates", action="store_true", help="Create a templates/ directory")
    parser.add_argument("--with-openai-yaml", action="store_true", help="Create agents/openai.yaml from the starter template")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing SKILL.md")
    args = parser.parse_args()

    try:
        skill_name = slugify(args.name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if len(skill_name) > NAME_MAX_LENGTH:
        print(
            f"skill name is too long ({len(skill_name)} chars > {NAME_MAX_LENGTH})",
            file=sys.stderr,
        )
        return 1

    try:
        short_description = validate_scaffold_description(args.description, skill_name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    project_root: Path | None = None
    project_marker: bytes | None = None
    if args.project_root is not None:
        try:
            project_root, project_slug = resolve_project_root(args.project_root)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

        if skill_name == project_slug or skill_name.startswith(f"{project_slug}-"):
            print(
                "skill name is already prefixed with the project slug; provide only the skill name",
                file=sys.stderr,
            )
            return 1

        skill_name = f"{project_slug}-{skill_name}"
        if len(skill_name) > NAME_MAX_LENGTH:
            print(
                f"project-specific skill name is too long ({len(skill_name)} chars > {NAME_MAX_LENGTH})",
                file=sys.stderr,
            )
            return 1
        project_marker = str(project_root).encode("utf-8")

    skills_root = Path(args.root).resolve()
    skill_dir = skills_root / skill_name
    skill_md = skill_dir / "SKILL.md"
    marker_path = skill_dir / PROJECT_MARKER_NAME

    try:
        existing_marker = read_marker_bytes(marker_path)
    except MarkerMissingError:
        existing_marker = None
    except MarkerReadError as exc:
        print(exc, file=sys.stderr)
        return 1
    marker_exists = existing_marker is not None

    if marker_exists:
        if project_root is None:
            print(
                "refusing to overwrite a project-specific skill without its matching --project-root",
                file=sys.stderr,
            )
            return 1
        try:
            matches_project = marker_matches(existing_marker, project_root)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        if not matches_project:
            print(
                "refusing to rebind an existing project-specific-skill marker",
                file=sys.stderr,
            )
            return 1
    elif project_marker is not None and skill_dir.exists():
        print(
            "refusing to add a project marker to an existing unmarked skill directory",
            file=sys.stderr,
        )
        return 1

    if skill_md.exists() and not args.force:
        print(f"SKILL.md already exists: {skill_md}", file=sys.stderr)
        print("Use --force to overwrite it.", file=sys.stderr)
        return 1
    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if args.with_openai_yaml and openai_yaml.exists() and not args.force:
        print(f"agents/openai.yaml already exists: {openai_yaml}", file=sys.stderr)
        print("Use --force to overwrite it.", file=sys.stderr)
        return 1

    try:
        content = Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(
            skill_name=skill_name,
            title=title_case(skill_name),
            description=json.dumps(args.description, ensure_ascii=False),
            short_description=json.dumps(short_description, ensure_ascii=False),
        )
    except (KeyError, ValueError) as exc:
        print(f"invalid skill template: {exc}", file=sys.stderr)
        return 1
    if project_marker is not None:
        try:
            content = insert_project_marker_link(content)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

    resource_directories = []
    if args.with_assets:
        resource_directories.append("assets")
    if args.with_references:
        resource_directories.append("references")
    if args.with_scripts:
        resource_directories.append("scripts")
    if args.with_templates:
        resource_directories.append("templates")
    if args.with_openai_yaml:
        resource_directories.append("agents")

    skill_dir.mkdir(parents=True, exist_ok=True)
    if project_marker is not None and not marker_exists:
        try:
            with marker_path.open("xb") as marker_file:
                marker_file.write(project_marker)
        except FileExistsError:
            try:
                existing_marker = read_marker_bytes(marker_path)
                matches_project = marker_matches(existing_marker, project_root)
            except MarkerMissingError:
                print(f"unable to create {PROJECT_MARKER_NAME}", file=sys.stderr)
                return 1
            except (MarkerReadError, ValueError) as exc:
                print(exc, file=sys.stderr)
                return 1
            if not matches_project:
                print(
                    "refusing to rebind an existing project-specific-skill marker",
                    file=sys.stderr,
                )
                return 1
    for folder_name in resource_directories:
        (skill_dir / folder_name).mkdir(parents=True, exist_ok=True)

    skill_md.write_text(content, encoding="utf-8")
    if args.with_openai_yaml:
        openai_yaml.write_text(OPENAI_YAML_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Created skill scaffold: {skill_dir}")
    print(f"- {skill_md}")
    if project_marker is not None:
        print(f"- {marker_path}")
    for folder_name in resource_directories:
        print(f"- {skill_dir / folder_name}")
    if args.with_openai_yaml:
        print(f"- {openai_yaml}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
