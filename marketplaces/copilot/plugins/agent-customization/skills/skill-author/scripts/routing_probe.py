#!/usr/bin/env python3
"""Validate and compare external fresh-context skill-routing observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


CASES_SCHEMA = "skill-routing-cases/v1"
OBSERVATION_SCHEMA = "skill-routing-observation/v1"
PLAN_SCHEMA = "skill-routing-plan/v1"
SCORE_SCHEMA = "skill-routing-score/v1"
COMPARISON_SCHEMA = "skill-routing-comparison/v1"
MAX_CASES = 15
MAX_PROMPT_CHARACTERS = 500
MAX_IDENTIFIER_CHARACTERS = 96
MAX_LABEL_CHARACTERS = 128
MAX_SELECTED_SKILLS = 32
MAX_JSON_BYTES = 65_536
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 2_048
MAX_JSON_STRING_CHARACTERS = 2_048
MAX_JSON_LIST_ITEMS = 64
MAX_JSON_OBJECT_ITEMS = 32
MAX_SKILL_FILE_BYTES = 65_536
MAX_DISCOVERY_DESCRIPTION_CHARACTERS = 1_024
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
PLACEHOLDER_PATTERN = re.compile(r"^<[a-z0-9][a-z0-9-]{0,127}>$")
ALLOWED_CASE_KINDS = frozenset({"positive", "adjacent-negative", "boundary"})
ALLOWED_UNCERTAINTY = frozenset({"none", "reported", "unknown", "ambiguous"})
ALLOWED_EVIDENCE_METHODS = frozenset(
    {"external-fresh-context-capture", "external-isolated-context-capture"}
)
DOCUMENTED_PLACEHOLDERS = frozenset(
    {
        "<client>",
        "<version>",
        "<surface>",
        "<provider>",
        "<model>",
        "<reasoning>",
        "<mode-or-unknown>",
        "<freshness-or-unknown>",
        "<visibility-or-unknown>",
        "<profile-or-unknown>",
        "<revision-label>",
        "<64-lowercase-hex>",
        "<unique-64-lowercase-hex>",
        "<known-skill-name>",
        "<observed-skill-name>",
    }
)
REQUIRED_GROUPS = frozenset(
    {
        "consult-tend",
        "python-debugging-databricks-runtime-doctor",
        "skill-author-agents-md-make-documentation",
        "graphiti-durable-transient",
        "spark-diagnostics-databricks-spark-etl",
    }
)
DEFAULT_CASES = Path(__file__).resolve().parents[1] / "assets" / "routing-cases.json"
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2]


class ContractError(ValueError):
    """A bounded input contract failure with an intentionally value-free code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DuplicateKeyError(ValueError):
    """A JSON object repeated a member name."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed CLI input without reflecting an untrusted argument."""

    def error(self, message: str) -> None:
        del message
        self.exit(
            2,
            json.dumps({"error": "invalid-command-line"}, separators=(",", ":")) + "\n",
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON value deterministically for a content binding."""
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    """Return the SHA-256 content binding for a JSON value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError
        value[key] = item
    return value


def _reject_nonstandard_json_constant(value: str) -> None:
    del value
    raise ValueError


def _validate_decoded_json_limits(value: Any) -> None:
    nodes = 0
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ContractError("invalid-json-input")
        if isinstance(item, str):
            if len(item) > MAX_JSON_STRING_CHARACTERS:
                raise ContractError("invalid-json-input")
        elif isinstance(item, list):
            if len(item) > MAX_JSON_LIST_ITEMS:
                raise ContractError("invalid-json-input")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if len(item) > MAX_JSON_OBJECT_ITEMS:
                raise ContractError("invalid-json-input")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > MAX_JSON_STRING_CHARACTERS:
                    raise ContractError("invalid-json-input")
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ContractError("invalid-json-input")
        with path.open("rb") as stream:
            encoded = stream.read(MAX_JSON_BYTES + 1)
        if len(encoded) > MAX_JSON_BYTES:
            raise ContractError("invalid-json-input")
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonstandard_json_constant,
        )
        _validate_decoded_json_limits(value)
        return value
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        DuplicateKeyError,
        ValueError,
        RecursionError,
        MemoryError,
        ContractError,
    ) as error:
        raise ContractError("invalid-json-input") from error


def _require_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(code)
    return value


def _require_string(
    value: Any, code: str, *, maximum: int = MAX_LABEL_CHARACTERS
) -> str:
    if isinstance(value, str) and (
        value in DOCUMENTED_PLACEHOLDERS or PLACEHOLDER_PATTERN.fullmatch(value)
    ):
        raise ContractError("unpopulated-template-placeholder")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ContractError(code)
    return value


def _require_identifier(value: Any, code: str) -> str:
    identifier = _require_string(value, code, maximum=MAX_IDENTIFIER_CHARACTERS)
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ContractError(code)
    return identifier


def _require_hash(value: Any, code: str) -> str:
    digest = _require_string(value, code, maximum=64)
    if not HASH_PATTERN.fullmatch(digest):
        raise ContractError(code)
    return digest


def _require_string_list(
    value: Any,
    code: str,
    *,
    maximum: int,
    identifier: bool = False,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(code)
    values = [
        _require_identifier(item, code) if identifier else _require_string(item, code)
        for item in value
    ]
    if len(values) != len(set(values)) or values != sorted(values):
        raise ContractError(code)
    return values


def _known_skills(skills_root: Path) -> set[str]:
    if not skills_root.is_dir():
        raise ContractError("skills-root-unavailable")
    names = {
        child.name
        for child in skills_root.iterdir()
        if child.is_dir()
        and (child / "SKILL.md").is_file()
        and IDENTIFIER_PATTERN.fullmatch(child.name)
    }
    if not names:
        raise ContractError("skills-root-empty")
    return names


def _read_bounded_utf8(path: Path, maximum: int, code: str) -> str:
    try:
        if path.stat().st_size > maximum:
            raise ContractError(code)
        with path.open("rb") as stream:
            encoded = stream.read(maximum + 1)
        if len(encoded) > maximum:
            raise ContractError(code)
        return encoded.decode("utf-8")
    except (OSError, UnicodeDecodeError, MemoryError, ContractError) as error:
        raise ContractError(code) from error


def _parse_frontmatter_scalar(value: str) -> str:
    if not value or value != value.strip():
        raise ContractError("invalid-discovery-snapshot")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ContractError("invalid-discovery-snapshot") from error
        if not isinstance(decoded, str):
            raise ContractError("invalid-discovery-snapshot")
        return decoded
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ContractError("invalid-discovery-snapshot")
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            raise ContractError("invalid-discovery-snapshot")
        return inner.replace("''", "'")
    return value


def _discovery_record(skill_dir: Path, skills_root: Path) -> dict[str, str]:
    text = _read_bounded_utf8(
        skill_dir / "SKILL.md", MAX_SKILL_FILE_BYTES, "invalid-discovery-snapshot"
    )
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ContractError("invalid-discovery-snapshot")
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line == "---"), None
    )
    if closing_index is None:
        raise ContractError("invalid-discovery-snapshot")
    values: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        if key not in {"name", "description"}:
            continue
        if key in values or not raw_value.startswith(" "):
            raise ContractError("invalid-discovery-snapshot")
        values[key] = _parse_frontmatter_scalar(raw_value[1:])
    name = values.get("name")
    description = values.get("description")
    if (
        name is None
        or description is None
        or name != skill_dir.name
        or not IDENTIFIER_PATTERN.fullmatch(name)
        or not description
        or len(description) > MAX_DISCOVERY_DESCRIPTION_CHARACTERS
        or any(
            ord(character) < 32 or ord(character) == 127 for character in description
        )
    ):
        raise ContractError("invalid-discovery-snapshot")
    return {
        "path": (skill_dir / "SKILL.md").relative_to(skills_root.parent).as_posix(),
        "name": name,
        "description": description,
    }


def discovery_snapshot_hash(
    skills_root: Path, changed_skills: Sequence[str] = ()
) -> str:
    """Hash the canonical initial-list fields without emitting their raw values."""
    root = skills_root.resolve()
    known_skills = _known_skills(root)
    normalized_changed_skills = list(changed_skills)
    if len(normalized_changed_skills) != len(
        set(normalized_changed_skills)
    ) or normalized_changed_skills != sorted(normalized_changed_skills):
        raise ContractError("invalid-changed-skills")
    excluded = set(normalized_changed_skills)
    if not excluded.issubset(known_skills):
        raise ContractError("invalid-changed-skills")
    records = [
        _discovery_record(skill_dir, root)
        for skill_dir in sorted(root.iterdir(), key=lambda path: path.name)
        if skill_dir.is_dir()
        and skill_dir.name in known_skills
        and skill_dir.name not in excluded
    ]
    return sha256_json(records)


def validate_cases(
    value: Any, skills_root: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate a bounded v1 corpus and return its indexed cases."""
    corpus = dict(_require_mapping(value, "invalid-corpus-shape"))
    if set(corpus) != {"schema_version", "skill_revision", "cases"}:
        raise ContractError("invalid-corpus-fields")
    if corpus.get("schema_version") != CASES_SCHEMA:
        raise ContractError("unsupported-corpus-schema")
    _require_string(corpus.get("skill_revision"), "invalid-corpus-revision")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise ContractError("invalid-corpus-case-count")
    known_skills = _known_skills(skills_root)
    indexed: dict[str, dict[str, Any]] = {}
    groups: dict[str, set[str]] = {}
    for item in cases:
        case = dict(_require_mapping(item, "invalid-case-shape"))
        expected_fields = {
            "id",
            "group",
            "kind",
            "prompt",
            "must_select",
            "must_not_select",
        }
        if set(case) != expected_fields:
            raise ContractError("invalid-case-fields")
        case_id = _require_identifier(case["id"], "invalid-case-id")
        if case_id in indexed:
            raise ContractError("duplicate-case-id")
        group = _require_identifier(case["group"], "invalid-case-group")
        kind = _require_string(case["kind"], "invalid-case-kind")
        if kind not in ALLOWED_CASE_KINDS:
            raise ContractError("invalid-case-kind")
        prompt = case["prompt"]
        if (
            not isinstance(prompt, str)
            or not prompt
            or prompt != prompt.strip()
            or len(prompt) > MAX_PROMPT_CHARACTERS
            or "$" in prompt
            or any(ord(character) < 32 or ord(character) == 127 for character in prompt)
        ):
            raise ContractError("invalid-case-prompt")
        must_select = _require_string_list(
            case["must_select"], "invalid-must-select", maximum=8, identifier=True
        )
        must_not_select = _require_string_list(
            case["must_not_select"],
            "invalid-must-not-select",
            maximum=8,
            identifier=True,
        )
        if not must_select and not must_not_select:
            raise ContractError("vacuous-case")
        if set(must_select).intersection(must_not_select):
            raise ContractError("overlapping-case-oracle")
        if not set(must_select).union(must_not_select).issubset(known_skills):
            raise ContractError("unknown-skill-reference")
        indexed[case_id] = {
            "id": case_id,
            "group": group,
            "kind": kind,
            "prompt": prompt,
            "must_select": must_select,
            "must_not_select": must_not_select,
        }
        groups.setdefault(group, set()).add(kind)
    if set(groups) != REQUIRED_GROUPS or any(
        kinds != ALLOWED_CASE_KINDS for kinds in groups.values()
    ):
        raise ContractError("invalid-case-groups")
    return corpus, indexed


def load_cases(
    cases_path: Path, skills_root: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    """Load and bind a valid canonical case corpus."""
    corpus, indexed = validate_cases(_read_json(cases_path), skills_root)
    return corpus, indexed, sha256_json(corpus)


def _opaque_trial_ids(cases: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    return {
        f"trial-{index:03d}": case_id for index, case_id in enumerate(cases, start=1)
    }


def _validate_capture_labels(value: Any, code: str) -> dict[str, str]:
    labels = dict(_require_mapping(value, code))
    if set(labels) != {"mode", "freshness", "path_visibility", "instruction_profile"}:
        raise ContractError(code)
    return {key: _require_string(item, code) for key, item in labels.items()}


def _validate_client(value: Any) -> dict[str, str]:
    client = dict(_require_mapping(value, "invalid-client"))
    if set(client) != {"name", "version", "surface"}:
        raise ContractError("invalid-client")
    return {
        key: _require_string(item, "invalid-client") for key, item in client.items()
    }


def _validate_model(value: Any) -> dict[str, str]:
    model = dict(_require_mapping(value, "invalid-model"))
    if set(model) != {"provider", "id", "reasoning"}:
        raise ContractError("invalid-model")
    return {key: _require_string(item, "invalid-model") for key, item in model.items()}


def validate_observation(
    value: Any,
    *,
    cases: Mapping[str, Mapping[str, Any]],
    corpus_hash: str,
    known_skills: set[str],
) -> dict[str, Any]:
    """Validate an external capture without reading prompt-oracle data into output."""
    observation = dict(_require_mapping(value, "invalid-observation-shape"))
    required_fields = {
        "schema_version",
        "corpus_sha256",
        "full_discovery_sha256",
        "non_target_discovery_sha256",
        "changed_skills",
        "skill_revision",
        "client",
        "model",
        "context",
        "explicit_invocation",
        "evidence_method",
        "trials",
    }
    if set(observation) != required_fields:
        raise ContractError("invalid-observation-fields")
    if observation.get("schema_version") != OBSERVATION_SCHEMA:
        raise ContractError("unsupported-observation-schema")
    if (
        _require_hash(observation.get("corpus_sha256"), "invalid-corpus-hash")
        != corpus_hash
    ):
        raise ContractError("corpus-hash-mismatch")
    full_discovery_hash = _require_hash(
        observation.get("full_discovery_sha256"), "invalid-full-discovery-hash"
    )
    non_target_hash = _require_hash(
        observation.get("non_target_discovery_sha256"),
        "invalid-non-target-discovery-hash",
    )
    changed_skills = _require_string_list(
        observation.get("changed_skills"),
        "invalid-changed-skills",
        maximum=MAX_CASES,
        identifier=True,
    )
    if not changed_skills or not set(changed_skills).issubset(known_skills):
        raise ContractError("invalid-changed-skills")
    skill_revision = _require_string(
        observation.get("skill_revision"), "invalid-observation-revision"
    )
    client = _validate_client(observation.get("client"))
    model = _validate_model(observation.get("model"))
    context = _validate_capture_labels(observation.get("context"), "invalid-context")
    if observation.get("explicit_invocation") is not False:
        raise ContractError("explicit-invocation-not-allowed")
    evidence_method = _require_string(
        observation.get("evidence_method"), "invalid-evidence-method"
    )
    if evidence_method not in ALLOWED_EVIDENCE_METHODS:
        raise ContractError("invalid-evidence-method")
    trials = observation.get("trials")
    if not isinstance(trials, list) or len(trials) != len(cases):
        raise ContractError("invalid-observation-trial-count")
    indexed_trials: dict[str, dict[str, Any]] = {}
    transcript_hashes: set[str] = set()
    trial_cases = _opaque_trial_ids(cases)
    for item in trials:
        trial = dict(_require_mapping(item, "invalid-observation-trial"))
        if set(trial) != {
            "trial_id",
            "selected_skills",
            "transcript_sha256",
            "uncertainty",
        }:
            raise ContractError("invalid-observation-trial-fields")
        trial_id = _require_identifier(
            trial.get("trial_id"), "invalid-observation-trial-id"
        )
        case_id = trial_cases.get(trial_id)
        if case_id is None or case_id in indexed_trials:
            raise ContractError("unknown-or-duplicate-trial-id")
        selected_skills = _require_string_list(
            trial.get("selected_skills"),
            "invalid-selected-skills",
            maximum=MAX_SELECTED_SKILLS,
            identifier=True,
        )
        if not set(selected_skills).issubset(known_skills):
            raise ContractError("unknown-selected-skill")
        transcript_hash = _require_hash(
            trial.get("transcript_sha256"), "invalid-transcript-hash"
        )
        if transcript_hash in transcript_hashes:
            raise ContractError("duplicate-transcript-hash")
        transcript_hashes.add(transcript_hash)
        uncertainty = _require_string(trial.get("uncertainty"), "invalid-uncertainty")
        if uncertainty not in ALLOWED_UNCERTAINTY:
            raise ContractError("invalid-uncertainty")
        indexed_trials[case_id] = {
            "trial_id": trial_id,
            "selected_skills": selected_skills,
            "transcript_sha256": transcript_hash,
            "uncertainty": uncertainty,
        }
    return {
        "schema_version": OBSERVATION_SCHEMA,
        "corpus_sha256": corpus_hash,
        "full_discovery_sha256": full_discovery_hash,
        "non_target_discovery_sha256": non_target_hash,
        "changed_skills": changed_skills,
        "skill_revision": skill_revision,
        "client": client,
        "model": model,
        "context": context,
        "explicit_invocation": False,
        "evidence_method": evidence_method,
        "trials": indexed_trials,
    }


def make_plan(cases: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return prompt-only trial material, intentionally omitting the hidden oracle."""
    trial_ids = _opaque_trial_ids(cases)
    return {
        "schema_version": PLAN_SCHEMA,
        "trial_count": len(cases),
        "trials": [
            {"trial_id": trial_id, "prompt": cases[case_id]["prompt"]}
            for trial_id, case_id in trial_ids.items()
        ],
    }


def make_observation_template(
    cases: Mapping[str, Mapping[str, Any]],
    *,
    corpus_hash: str,
    skills_root: Path,
    changed_skills: list[str],
    skill_revision: str,
) -> dict[str, Any]:
    return {
        "schema_version": OBSERVATION_SCHEMA,
        "corpus_sha256": corpus_hash,
        "full_discovery_sha256": discovery_snapshot_hash(skills_root),
        "non_target_discovery_sha256": discovery_snapshot_hash(
            skills_root, changed_skills
        ),
        "changed_skills": changed_skills,
        "skill_revision": skill_revision,
        "client": {
            "name": "<client>",
            "version": "<version>",
            "surface": "<surface>",
        },
        "model": {
            "provider": "<provider>",
            "id": "<model>",
            "reasoning": "<reasoning>",
        },
        "context": {
            "mode": "unknown",
            "freshness": "unknown",
            "path_visibility": "unknown",
            "instruction_profile": "unknown",
        },
        "explicit_invocation": False,
        "evidence_method": "external-fresh-context-capture",
        "trials": [
            {
                "trial_id": trial_id,
                "selected_skills": [],
                "transcript_sha256": "<unique-64-lowercase-hex>",
                "uncertainty": "unknown",
            }
            for trial_id in _opaque_trial_ids(cases)
        ],
    }


def _observation_bindings(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_sha256": sha256_json(observation),
        "binding_hashes": {
            "corpus_sha256": observation["corpus_sha256"],
            "full_discovery_sha256": observation["full_discovery_sha256"],
            "non_target_discovery_sha256": observation["non_target_discovery_sha256"],
            "skill_revision_sha256": sha256_json(observation["skill_revision"]),
            "changed_skills_sha256": sha256_json(observation["changed_skills"]),
            "client_sha256": sha256_json(observation["client"]),
            "model_sha256": sha256_json(observation["model"]),
            "context_sha256": sha256_json(observation["context"]),
            "evidence_method_sha256": sha256_json(observation["evidence_method"]),
        },
    }


def score_observation(
    cases: Mapping[str, Mapping[str, Any]], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Score clear observations only; uncertainty is neither success nor a violation."""
    violations: list[dict[str, str]] = []
    clear_assertions = 0
    inconclusive_assertions = 0
    uncertain_trials = 0
    for case_id, case in cases.items():
        trial = observation["trials"][case_id]
        assertions = [("must_select", skill) for skill in case["must_select"]] + [
            ("must_not_select", skill) for skill in case["must_not_select"]
        ]
        if trial["uncertainty"] != "none":
            uncertain_trials += 1
            inconclusive_assertions += len(assertions)
            continue
        selected = set(trial["selected_skills"])
        for rule, skill in assertions:
            clear_assertions += 1
            violated = (rule == "must_select" and skill not in selected) or (
                rule == "must_not_select" and skill in selected
            )
            if violated:
                violations.append(
                    {"trial_id": trial["trial_id"], "rule": rule, "skill": skill}
                )
    return {
        "schema_version": SCORE_SCHEMA,
        "status": "observed",
        **_observation_bindings(observation),
        "observed_counts": {
            "trials": len(cases),
            "clear_assertions": clear_assertions,
            "inconclusive_assertions": inconclusive_assertions,
            "uncertain_trials": uncertain_trials,
            "observed_violations": len(violations),
        },
        "observed_violations": violations,
        "limitation": "Other skill loads are allowed. Uncertainty is not evidence of compliance or violation.",
    }


def compare_observations(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    cases: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare only evidence that remains compatible under the declared capture contract."""
    comparison_fields = (
        "corpus_sha256",
        "non_target_discovery_sha256",
        "changed_skills",
        "client",
        "model",
        "context",
        "evidence_method",
    )
    baseline_score = score_observation(cases, baseline)
    candidate_score = score_observation(cases, candidate)
    observation_bindings = {
        "baseline": {
            "observation_sha256": baseline_score["observation_sha256"],
            "binding_hashes": baseline_score["binding_hashes"],
        },
        "candidate": {
            "observation_sha256": candidate_score["observation_sha256"],
            "binding_hashes": candidate_score["binding_hashes"],
        },
    }
    reasons = [
        field for field in comparison_fields if baseline[field] != candidate[field]
    ]
    if reasons:
        return {
            "schema_version": COMPARISON_SCHEMA,
            "status": "incomparable",
            "reasons": reasons,
            "comparability_fields": list(comparison_fields),
            "observation_bindings": observation_bindings,
            "observed_counts": None,
            "new_observed_violations": [],
        }
    baseline_violations = {
        (item["trial_id"], item["rule"], item["skill"])
        for item in baseline_score["observed_violations"]
    }
    new_violations = [
        item
        for item in candidate_score["observed_violations"]
        if (item["trial_id"], item["rule"], item["skill"]) not in baseline_violations
    ]
    return {
        "schema_version": COMPARISON_SCHEMA,
        "status": "observed-comparison",
        "comparability_fields": list(comparison_fields),
        "observation_bindings": observation_bindings,
        "observed_counts": {
            "baseline": baseline_score["observed_counts"],
            "candidate": candidate_score["observed_counts"],
        },
        "new_observed_violations": new_violations,
        "limitation": "This compares one declared capture context and makes no deterministic or cross-client claim.",
    }


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        help="Canonical synthetic v1 corpus path.",
    )
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=DEFAULT_SKILLS_ROOT,
        help="Canonical skills directory.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=SafeArgumentParser
    )
    commands.add_parser(
        "plan", help="Validate the corpus and emit oracle-free prompts."
    )
    score = commands.add_parser(
        "score", help="Score one external observation against the hidden oracle."
    )
    score.add_argument(
        "--observation",
        type=Path,
        help="External observation JSON path.",
    )
    score.add_argument(
        "--template",
        action="store_true",
        help="Emit a trusted-operator observation template.",
    )
    score.add_argument(
        "--changed-skill",
        action="append",
        default=[],
        help="Changed canonical skill name; supply a sorted, unique list.",
    )
    score.add_argument(
        "--skill-revision",
        help="Bounded trusted-operator revision label for template output.",
    )
    score.add_argument(
        "--skills-root",
        dest="template_skills_root",
        type=Path,
        help="Canonical skills directory for trusted-template discovery bindings.",
    )
    compare = commands.add_parser(
        "compare", help="Compare compatible external observations only."
    )
    compare.add_argument(
        "--baseline", required=True, type=Path, help="Baseline observation JSON path."
    )
    compare.add_argument(
        "--candidate", required=True, type=Path, help="Candidate observation JSON path."
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run a bounded, read-only command and render exactly one JSON result."""
    parser = _parser()
    namespace = parser.parse_args(arguments)
    try:
        skills_root = (
            namespace.template_skills_root
            if namespace.command == "score"
            and namespace.template_skills_root is not None
            else namespace.skills_root
        )
        _, cases, corpus_hash = load_cases(namespace.cases, skills_root)
        known_skills = _known_skills(skills_root)
        if namespace.command == "plan":
            result = make_plan(cases)
        elif namespace.command == "score":
            if namespace.template:
                if namespace.observation is not None:
                    raise ContractError("invalid-template-arguments")
                changed_skills = _require_string_list(
                    namespace.changed_skill,
                    "invalid-changed-skills",
                    maximum=MAX_CASES,
                    identifier=True,
                )
                if not changed_skills or not set(changed_skills).issubset(known_skills):
                    raise ContractError("invalid-changed-skills")
                skill_revision = _require_string(
                    namespace.skill_revision, "invalid-observation-revision"
                )
                result = make_observation_template(
                    cases,
                    corpus_hash=corpus_hash,
                    skills_root=skills_root,
                    changed_skills=changed_skills,
                    skill_revision=skill_revision,
                )
            else:
                if (
                    namespace.observation is None
                    or namespace.changed_skill
                    or namespace.skill_revision is not None
                    or namespace.template_skills_root is not None
                ):
                    raise ContractError("invalid-score-arguments")
                observation = validate_observation(
                    _read_json(namespace.observation),
                    cases=cases,
                    corpus_hash=corpus_hash,
                    known_skills=known_skills,
                )
                result = score_observation(cases, observation)
        else:
            baseline = validate_observation(
                _read_json(namespace.baseline),
                cases=cases,
                corpus_hash=corpus_hash,
                known_skills=known_skills,
            )
            candidate = validate_observation(
                _read_json(namespace.candidate),
                cases=cases,
                corpus_hash=corpus_hash,
                known_skills=known_skills,
            )
            result = compare_observations(baseline, candidate, cases)
    except ContractError as error:
        print(json.dumps({"error": error.code}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
