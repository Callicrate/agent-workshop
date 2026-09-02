"""Inventory skill metadata, resources, trigger terms, and line counts."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESOURCE_FOLDERS = ("references", "scripts", "assets", "templates", "agents")
DESCRIPTION_OPENER_RE = re.compile(r"\A(?:use when|trigger only when)\b", re.IGNORECASE)
DESCRIPTION_EXCLUSION_RE = re.compile(r"(?:\A|(?<=[.!?;]))\s*do not trigger\b", re.IGNORECASE)
KEYWORDS_MARKER_RE = re.compile(r"\bkeywords\s*:", re.IGNORECASE)
GENERATED_RESOURCE_SUFFIXES = {".pyc", ".pyo"}
FRONTMATTER_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")
AMBIGUOUS_PLAIN_SCALAR_RE = re.compile(
    r"(?:[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\d{4}-\d{2}-\d{2}|0[xob][0-9a-f]+)",
    re.IGNORECASE,
)
AMBIGUOUS_PLAIN_SCALARS = {
    "null",
    "~",
    "true",
    "false",
    "yes",
    "no",
    "on",
    "off",
    ".nan",
    ".inf",
    "+.inf",
    "-.inf",
}
NON_STANDARD_LINE_SEPARATORS = frozenset("\v\f\u001c\u001d\u001e\u0085\u2028\u2029")
DESCRIPTION_MAX_LENGTH = 1024
SHORT_DESCRIPTION_MAX_WORDS = 10
DISCOVERY_BUDGET_FALLBACK_CHARACTERS = 8_000
DISCOVERY_BUDGET_OFFICIAL_URL = "https://learn.chatgpt.com/docs/build-skills"
DISCOVERY_DESCRIPTION_OUTLIER_CHARACTERS = 200
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT_UNAVAILABLE_ERROR = "skills root unavailable"


@dataclass(frozen=True)
class ParsedFrontmatter(Mapping[str, str]):
    """A supported frontmatter mapping plus deterministic parse diagnostics."""

    data: dict[str, str]
    diagnostics: tuple[str, ...]

    def __getitem__(self, key: str) -> str:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


def unsupported_scalar_character(value: str) -> str | None:
    """Return the first control or Unicode line separator forbidden in a scalar."""
    for character in value:
        if unicodedata.category(character) in {"Cc", "Zl", "Zp"}:
            return character
    return None


def parse_scalar(value: str, line_number: int, *, allow_plain: bool) -> tuple[str | None, str | None]:
    """Decode one safe single-line YAML scalar without a YAML dependency."""
    if not value:
        return None, f"frontmatter line {line_number}: scalar value is required"
    if value.startswith(("|", ">")):
        return None, f"frontmatter line {line_number}: block scalars are unsupported"
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            return None, f"frontmatter line {line_number}: invalid double-quoted scalar: {exc.msg}"
        if not isinstance(decoded, str):
            return None, f"frontmatter line {line_number}: scalar must decode to a string"
        unsupported_character = unsupported_scalar_character(decoded)
        if unsupported_character:
            return (
                None,
                "frontmatter line "
                f"{line_number}: scalar contains unsupported control or line-separator character U+{ord(unsupported_character):04X}",
            )
        return decoded, None
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            return None, f"frontmatter line {line_number}: invalid single-quoted scalar"
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            return None, f"frontmatter line {line_number}: invalid single-quoted scalar"
        decoded = inner.replace("''", "'")
        unsupported_character = unsupported_scalar_character(decoded)
        if unsupported_character:
            return (
                None,
                "frontmatter line "
                f"{line_number}: scalar contains unsupported control or line-separator character U+{ord(unsupported_character):04X}",
            )
        return decoded, None
    if not allow_plain:
        return None, f"frontmatter line {line_number}: plain scalar requires quoting"
    if value != value.strip():
        return None, f"frontmatter line {line_number}: plain scalars cannot have surrounding whitespace"
    if "#" in value:
        return None, f"frontmatter line {line_number}: comment markers require quoting"
    if value.startswith(("- ", "? ", ": ", "[", "{", "&", "*", "!", "@", "`")):
        return None, f"frontmatter line {line_number}: ambiguous plain scalar requires quoting"
    if ": " in value or value.endswith(":"):
        return None, f"frontmatter line {line_number}: ambiguous plain scalar requires quoting"
    if value.casefold() in AMBIGUOUS_PLAIN_SCALARS or AMBIGUOUS_PLAIN_SCALAR_RE.fullmatch(value):
        return None, f"frontmatter line {line_number}: ambiguous plain scalar requires quoting"
    if not value[0].isalpha():
        return None, f"frontmatter line {line_number}: plain scalars must start with an alphabetic character"
    unsupported_character = unsupported_scalar_character(value)
    if unsupported_character:
        return (
            None,
            "frontmatter line "
            f"{line_number}: scalar contains unsupported control or line-separator character U+{ord(unsupported_character):04X}",
        )
    return value, None


def parse_frontmatter(text: str) -> ParsedFrontmatter:
    """Parse the supported single-line frontmatter subset with diagnostics."""
    for character in text:
        if character in NON_STANDARD_LINE_SEPARATORS:
            return ParsedFrontmatter(
                {},
                (f"frontmatter contains unsupported line separator U+{ord(character):04X}",),
            )
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return ParsedFrontmatter({}, ("frontmatter must start with an exact --- delimiter",))

    closing_index = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing_index is None:
        return ParsedFrontmatter({}, ("frontmatter is missing an exact closing --- delimiter",))

    values: dict[str, str] = {}
    diagnostics: list[str] = []
    seen_keys: set[str] = set()
    parent_key: str | None = None
    for line_number, line in enumerate(lines[1:closing_index], start=2):
        if not line:
            continue
        if "\t" in line:
            diagnostics.append(f"frontmatter line {line_number}: tabs are unsupported")
            continue
        if line.startswith("  "):
            if len(line) == 2 or line[2].isspace():
                diagnostics.append(f"frontmatter line {line_number}: nested scalars require exactly two spaces")
                continue
            if parent_key != "metadata":
                diagnostics.append(f"frontmatter line {line_number}: nested scalars are only allowed under metadata")
                continue
            if ":" not in line[2:]:
                diagnostics.append(f"frontmatter line {line_number}: invalid nested scalar")
                continue
            key, raw_value = line[2:].split(":", 1)
            if not FRONTMATTER_KEY_RE.fullmatch(key):
                diagnostics.append(f"frontmatter line {line_number}: invalid nested key")
                continue
            if not raw_value.startswith(" "):
                diagnostics.append(f"frontmatter line {line_number}: scalar values require one space after :")
                continue
            full_key = f"{parent_key}.{key}"
            if full_key in seen_keys:
                diagnostics.append(f"frontmatter line {line_number}: duplicate key: {full_key}")
                continue
            decoded, scalar_error = parse_scalar(raw_value[1:], line_number, allow_plain=True)
            if scalar_error:
                diagnostics.append(scalar_error)
                continue
            if decoded is not None:
                values[full_key] = decoded
                seen_keys.add(full_key)
            continue
        if line[:1].isspace():
            diagnostics.append(f"frontmatter line {line_number}: invalid indentation")
            continue
        if ":" not in line:
            diagnostics.append(f"frontmatter line {line_number}: unsupported continuation or comment")
            continue
        key, raw_value = line.split(":", 1)
        if not FRONTMATTER_KEY_RE.fullmatch(key):
            diagnostics.append(f"frontmatter line {line_number}: invalid top-level key")
            continue
        if key in seen_keys:
            diagnostics.append(f"frontmatter line {line_number}: duplicate key: {key}")
            continue
        if not raw_value:
            if key != "metadata":
                diagnostics.append(f"frontmatter line {line_number}: top-level scalar value is required")
                continue
            parent_key = key
            seen_keys.add(key)
            continue
        if not raw_value.startswith(" "):
            diagnostics.append(f"frontmatter line {line_number}: scalar values require one space after :")
            continue
        if key == "metadata":
            diagnostics.append(f"frontmatter line {line_number}: metadata must be a nested mapping")
            continue
        decoded, scalar_error = parse_scalar(
            raw_value[1:],
            line_number,
            allow_plain=key in {"name", "license"},
        )
        if scalar_error:
            diagnostics.append(scalar_error)
            continue
        if decoded is not None:
            values[key] = decoded
            seen_keys.add(key)
        parent_key = None
    return ParsedFrontmatter(values, tuple(diagnostics))


def keyword_terms(description: str) -> list[str]:
    """Extract keyword terms from the description when present."""
    marker = "keywords:"
    lowered = description.casefold()
    if marker not in lowered:
        return []
    start = lowered.index(marker) + len(marker)
    return [term.strip(" .") for term in description[start:].split(",") if term.strip(" .")]


def has_valid_description_opener(description: str) -> bool:
    """Return whether a description starts with an allowed discovery opener."""
    return bool(DESCRIPTION_OPENER_RE.match(description))


def position_is_inside_inline_quote(text: str, position: int) -> bool:
    """Return whether a position sits inside a paired quote or backtick literal span."""
    quote: str | None = None
    escaped_delimiter = False
    index = 0
    while index < position:
        character = text[index]
        if quote:
            if quote.startswith("`") and text.startswith(quote, index):
                quote_length = len(quote)
                quote = None
                escaped_delimiter = False
                index += quote_length
                continue
            if escaped_delimiter and character == "\\" and index + 1 < len(text) and text[index + 1] == quote:
                quote = None
                escaped_delimiter = False
                index += 2
                continue
            if character == quote:
                quote = None
                escaped_delimiter = False
            elif quote == '"' and character == "\\":
                index += 2
                continue
            index += 1
            continue
        if character == "\\" and index + 1 < len(text) and text[index + 1] in ('"', "'"):
            quote = text[index + 1]
            escaped_delimiter = True
            index += 2
            continue
        if character == "`":
            run_end = index
            while run_end < len(text) and text[run_end] == "`":
                run_end += 1
            quote = text[index:run_end]
            index = run_end
            continue
        if character == '"':
            quote = character
        elif character == "“":
            quote = "”"
        elif character == "‘":
            quote = "’"
        elif character == "'" and (index == 0 or not text[index - 1].isalnum()):
            quote = character
        index += 1
    return quote is not None


def find_description_exclusion(description: str) -> re.Match[str] | None:
    """Find the first real exclusion clause outside quoted or literal text."""
    for match in DESCRIPTION_EXCLUSION_RE.finditer(description):
        if not position_is_inside_inline_quote(description, match.start()):
            return match
    return None


def has_description_exclusion(description: str) -> bool:
    """Return whether a description has the required negative trigger boundary."""
    return find_description_exclusion(description) is not None


def has_prohibited_keywords_marker(description: str) -> bool:
    """Return whether a description uses the prohibited keyword-list marker."""
    return bool(KEYWORDS_MARKER_RE.search(description))


def positive_description(description: str) -> str:
    """Return description text before its first explicit exclusion boundary."""
    match = find_description_exclusion(description)
    if not match:
        return description
    return description[: match.start()].rstrip()


def discovery_contract_issues(frontmatter: Mapping[str, object]) -> list[str]:
    """Return shared discovery-contract findings for decoded frontmatter fields."""
    issues: list[str] = []
    description = frontmatter.get("description")
    if description is None:
        issues.append("missing frontmatter field: description")
    elif not isinstance(description, str):
        issues.append("frontmatter description must be a string")
    elif not description.strip():
        issues.append("frontmatter description must not be empty")
    else:
        if len(description) > DESCRIPTION_MAX_LENGTH:
            issues.append(
                f"frontmatter description is too long ({len(description)} chars > {DESCRIPTION_MAX_LENGTH})"
            )
        if not has_valid_description_opener(description):
            issues.append("description must start with Use when or Trigger only when")
        if not has_description_exclusion(description):
            issues.append("description missing explicit Do not trigger exclusion")
        if has_prohibited_keywords_marker(description):
            issues.append("description contains prohibited Keywords: marker")

    short_description = frontmatter.get("metadata.short-description")
    if short_description is None:
        issues.append("missing frontmatter field: metadata.short-description")
    elif not isinstance(short_description, str):
        issues.append("metadata.short-description must be a string")
    elif not short_description.strip():
        issues.append("metadata.short-description must not be empty")
    elif len(short_description.split()) > SHORT_DESCRIPTION_MAX_WORDS:
        issues.append(
            "metadata.short-description is too long "
            f"({len(short_description.split())} words > {SHORT_DESCRIPTION_MAX_WORDS})"
        )
    return issues


def is_generated_resource(path: Path) -> bool:
    """Return whether a path is Python cache output rather than authored content."""
    return any(part.casefold() == "__pycache__" for part in path.parts) or path.suffix.casefold() in GENERATED_RESOURCE_SUFFIXES


def resource_files(skill_dir: Path, folder_name: str) -> list[str]:
    """List resource files in a skill folder."""
    folder = skill_dir / folder_name
    if not folder.exists():
        return []
    return sorted(
        path.relative_to(skill_dir).as_posix()
        for path in folder.rglob("*")
        if path.is_file() and not is_generated_resource(path.relative_to(skill_dir))
    )


def inventory_skill(skill_dir: Path) -> dict[str, Any] | None:
    """Return inventory data for one skill directory."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    text = skill_md.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    description = frontmatter.get("description", "")
    resource_counts = {folder: len(resource_files(skill_dir, folder)) for folder in RESOURCE_FOLDERS}
    return {
        "name": frontmatter.get("name", skill_dir.name),
        "path": skill_dir.resolve().as_posix(),
        "description": description,
        "short_description": frontmatter.get("metadata.short-description", ""),
        "keywords": keyword_terms(description),
        "frontmatter_diagnostics": list(frontmatter.diagnostics),
        "line_count": len(text.splitlines()),
        "resource_counts": resource_counts,
        "resources": {folder: resource_files(skill_dir, folder) for folder in RESOURCE_FOLDERS},
    }


def discovery_budget_metrics(skills_root: Path, skills: list[Mapping[str, object]]) -> dict[str, Any]:
    """Model initial Codex discovery-list field values without claiming host behavior."""
    relative_path_base = skills_root.resolve().parent
    names: list[str] = []
    descriptions: list[str] = []
    relative_skill_md_paths: list[str] = []
    absolute_skill_md_paths: list[str] = []
    description_outliers: list[dict[str, object]] = []

    for skill in skills:
        name = skill.get("name")
        description = skill.get("description")
        path = skill.get("path")
        name_value = name if isinstance(name, str) else ""
        description_value = description if isinstance(description, str) else ""
        path_value = path if isinstance(path, str) else ""
        skill_md_path = (Path(path_value) / "SKILL.md").resolve()

        names.append(name_value)
        descriptions.append(description_value)
        relative_skill_md_paths.append(skill_md_path.relative_to(relative_path_base).as_posix())
        absolute_skill_md_paths.append(skill_md_path.as_posix())
        if len(description_value) > DISCOVERY_DESCRIPTION_OUTLIER_CHARACTERS:
            description_outliers.append(
                {
                    "name": name_value,
                    "characters": len(description_value),
                    "description": description_value,
                }
            )

    modeled_field_values = {
        "names": names,
        "descriptions": descriptions,
        "relative_skill_md_paths": relative_skill_md_paths,
        "absolute_skill_md_paths": absolute_skill_md_paths,
    }
    field_character_totals = {
        field_name: sum(len(value) for value in values)
        for field_name, values in modeled_field_values.items()
    }
    common_field_total = field_character_totals["names"] + field_character_totals["descriptions"]
    relative_path_model_total = common_field_total + field_character_totals["relative_skill_md_paths"]
    absolute_path_model_total = common_field_total + field_character_totals["absolute_skill_md_paths"]

    def threshold_comparison(total: int) -> dict[str, int | str]:
        """Return the advisory threshold comparison for one alternative path model."""
        return {
            "result": "at_or_below_fallback" if total <= DISCOVERY_BUDGET_FALLBACK_CHARACTERS else "above_fallback",
            "difference_from_fallback_characters": total - DISCOVERY_BUDGET_FALLBACK_CHARACTERS,
        }

    return {
        "status": "advisory",
        "basis": {
            "fallback_characters": DISCOVERY_BUDGET_FALLBACK_CHARACTERS,
            "official_url": DISCOVERY_BUDGET_OFFICIAL_URL,
            "summary": "Codex uses 8,000 characters for the initial skill list when the context window is unknown.",
        },
        "modeled_field_values": modeled_field_values,
        "exact_totals": {
            "field_characters": field_character_totals,
            "relative_path_model_total": relative_path_model_total,
            "absolute_path_model_total": absolute_path_model_total,
        },
        "threshold_comparison": {
            "relative_path_model": threshold_comparison(relative_path_model_total),
            "absolute_path_model": threshold_comparison(absolute_path_model_total),
        },
        "description_outliers_over_200_characters": description_outliers,
        "limitation": "This modeled result is advisory and is not proof that this host omitted a skill.",
    }


def inventory(skills_root: Path) -> dict[str, Any]:
    """Return an inventory for every skill under the root."""
    skills_root = skills_root.resolve()
    skills = []
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        item = inventory_skill(skill_dir)
        if item:
            skills.append(item)
    return {
        "skills_root": str(skills_root),
        "skill_count": len(skills),
        "skills": skills,
        "discovery_budget": discovery_budget_metrics(skills_root, skills),
    }


def resolve_skills_root(value: str | Path) -> Path:
    """Return an existing skills directory without exposing its supplied value."""
    try:
        skills_root = Path(value).resolve()
        if not skills_root.is_dir():
            raise ValueError
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(SKILLS_ROOT_UNAVAILABLE_ERROR) from error
    return skills_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory shared skill metadata and resources.")
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=DEFAULT_SKILLS_ROOT,
        help="Directory containing skill folders (defaults to the packaged canonical root).",
    )
    parser.add_argument("--output", help="Optional JSON output path. Prints to stdout when omitted.")
    args = parser.parse_args()

    try:
        data = inventory(resolve_skills_root(args.skills_root))
    except (OSError, ValueError):
        print(f"error: {SKILLS_ROOT_UNAVAILABLE_ERROR}", file=sys.stderr)
        return 2
    payload = json.dumps(data, indent=2) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
