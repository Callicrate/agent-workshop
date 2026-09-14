#!/usr/bin/env python3
"""Validate Databricks Asset Bundle configuration.

This script performs static validation of `databricks.yml` files without
requiring the Databricks CLI. It understands simple split-bundle layouts by
merging files referenced under the root `include` list.

Usage:
    python validate_bundle.py
    python validate_bundle.py path/to/bundle
    python validate_bundle.py path/to/databricks.yml --strict
    python validate_bundle.py path/to/bundle --allow-runtime-prefix 18.0.x-scala2.12
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PERMISSION_PRINCIPAL_KEYS = ("user_name", "group_name", "service_principal_name")
BUNDLE_PERMISSION_LEVELS = frozenset({"CAN_VIEW", "CAN_MANAGE", "CAN_RUN"})
RESOURCE_PERMISSION_LEVELS = {
    "alerts": frozenset({"CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"}),
    "apps": frozenset({"CAN_MANAGE", "CAN_USE"}),
    "cluster_policies": frozenset({"CAN_USE"}),
    "clusters": frozenset({"CAN_ATTACH_TO", "CAN_MANAGE", "CAN_RESTART"}),
    "dashboards": frozenset({"CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"}),
    "database_instances": frozenset({"CAN_CREATE", "CAN_MANAGE", "CAN_USE"}),
    "genie_spaces": frozenset({"CAN_EDIT", "CAN_MANAGE", "CAN_RUN", "CAN_VIEW"}),
    "experiments": frozenset({"CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"}),
    "jobs": frozenset({"CAN_MANAGE", "CAN_MANAGE_RUN", "CAN_VIEW", "IS_OWNER"}),
    "instance_pools": frozenset({"CAN_ATTACH_TO", "CAN_MANAGE"}),
    "model_serving_endpoints": frozenset({"CAN_MANAGE", "CAN_QUERY", "CAN_VIEW"}),
    "models": frozenset(
        {"CAN_EDIT", "CAN_MANAGE", "CAN_MANAGE_PRODUCTION_VERSIONS", "CAN_MANAGE_STAGING_VERSIONS", "CAN_READ"}
    ),
    "pipelines": frozenset({"CAN_MANAGE", "CAN_RUN", "CAN_VIEW", "IS_OWNER"}),
    "secret_scopes": frozenset({"MANAGE", "READ", "WRITE"}),
    "sql_warehouses": frozenset({"CAN_MANAGE", "CAN_MONITOR", "CAN_USE", "CAN_VIEW", "IS_OWNER"}),
    "vector_search_endpoints": frozenset({"CAN_CREATE", "CAN_MANAGE", "CAN_USE"}),
}
GENERIC_RESOURCE_PERMISSION_LEVELS = frozenset(
    {
        "CAN_ATTACH_TO",
        "CAN_BIND",
        "CAN_CREATE",
        "CAN_EDIT",
        "CAN_EDIT_METADATA",
        "CAN_MANAGE",
        "CAN_MANAGE_PRODUCTION_VERSIONS",
        "CAN_MANAGE_RUN",
        "CAN_MANAGE_STAGING_VERSIONS",
        "CAN_MONITOR",
        "CAN_QUERY",
        "CAN_READ",
        "CAN_RESTART",
        "CAN_RUN",
        "CAN_USE",
        "CAN_VIEW",
        "CAN_VIEW_METADATA",
        "IS_OWNER",
    }
)
TASK_TYPES = {
    "notebook_task",
    "python_wheel_task",
    "spark_python_task",
    "spark_jar_task",
    "sql_task",
    "dbt_task",
    "run_job_task",
    "pipeline_task",
    "for_each_task",
}
DEFAULT_RUNTIME_CONFIG = Path(__file__).resolve().parents[1] / "references" / "supported-runtimes.yml"
DEFAULT_RUNTIME_PREFIXES = (
    "17.3.x-scala2.12",
    "17.3.x-gpu-ml-scala2.12",
    "17.3.x-cpu-ml-scala2.12",
)
REQUIRED_TAGS = {
    "Team",
    "Project",
    "Owner",
    "DataClassification",
    "Environment",
    "ApplicationName",
    "ResourceOwner",
    "CiscoMailAlias",
    "DataTaxonomy",
    "IntendedPublic",
}
GPU_CLUSTER_HINT_PATTERN = re.compile(r"(gpu|g4|g5|p3|p4|p5|a10|a100|h100)", re.IGNORECASE)
ROOT_REPAIR_JOB_PATTERN = re.compile(r"(dev[_-]?only|one[_-]?off|one[_-]?time|repair)", re.IGNORECASE)
CURRENT_USER_PATTERN = "${workspace.current_user.userName}"
PIP_INSTALL_PATTERN = re.compile(r"(?m)^\s*%pip\s+install\b")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
UNSAFE_BASENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
SENSITIVE_NAME_PATTERN = re.compile(r"(?:secret|token|password|credential|bearer|api[_-]?key)", re.IGNORECASE)
DEFAULT_LOCAL_PATH_POLICY = Path(__file__).resolve().parents[1] / "assets" / "local-path-policy.json"


class ValidationError:
    """Represents a validation error or warning."""

    def __init__(self, message: str, path: str = "", is_warning: bool = False, source: str = "") -> None:
        self.message = message
        self.path = path
        self.is_warning = is_warning
        self.source = source

    def __str__(self) -> str:
        prefix = "WARNING" if self.is_warning else "ERROR"
        location = f" at '{self.path}'" if self.path else ""
        source = f" [{self.source}]" if self.source else ""
        return f"{prefix}{location}{source}: {self.message}"


@dataclass(frozen=True)
class LocalPathPolicy:
    """Shared path classification and resource-limit policy for both validators."""

    limits: dict[str, int]
    dynamic_markers: tuple[str, ...]
    remote_prefixes: tuple[str, ...]
    windows_reserved_device_components: tuple[str, ...]


@dataclass
class SourceContext:
    """Tracks the source directory for parsed YAML containers after merges."""

    directories: dict[int, Path] = field(default_factory=dict)
    sources: dict[int, str] = field(default_factory=dict)
    members: dict[tuple[int, str | int], tuple[Path, str]] = field(default_factory=dict)

    def mark_container(self, value: dict[str, Any] | list[Any], directory: Path, source: str) -> None:
        """Associate one parsed or synthesized container with its declaration source."""
        self.directories[id(value)] = directory
        self.sources[id(value)] = source

    def mark_member(self, container: dict[str, Any] | list[Any], key: str | int, directory: Path, source: str) -> None:
        """Associate a mapping field or list member with its declaration source."""
        self.members[(id(container), key)] = (directory, source)

    def mark(self, value: Any, directory: Path, source: str) -> None:
        """Associate parsed containers with the file that declared them."""
        if isinstance(value, dict):
            self.mark_container(value, directory, source)
            for key, child in value.items():
                self.mark_member(value, key, directory, source)
                self.mark(child, directory, source)
        elif isinstance(value, list):
            self.mark_container(value, directory, source)
            for index, child in enumerate(value):
                self.mark_member(value, index, directory, source)
                self.mark(child, directory, source)

    def directory_for(self, value: Any, fallback: Path) -> Path:
        """Return the declaring file directory for a parsed config value."""
        return self.directories.get(id(value), fallback)

    def source_for(self, value: Any) -> str:
        """Return source-relative context for a parsed config value."""
        return self.sources.get(id(value), "databricks.yml")

    def member_origin(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        fallback_directory: Path,
        fallback_source: str,
    ) -> tuple[Path, str]:
        """Return a field or list member's origin, including scalar and null values."""
        return self.members.get((id(container), key), (fallback_directory, fallback_source))

    def origin_for(self, value: Any, fallback_directory: Path, fallback_source: str) -> tuple[Path, str]:
        """Return a container's own origin, falling back to its parent context."""
        return self.directories.get(id(value), fallback_directory), self.sources.get(id(value), fallback_source)


@dataclass(frozen=True)
class LoadedYamlFile:
    """A bounded YAML source that participated in the merged configuration."""

    path: Path
    relative_path: str
    text: str


@dataclass(frozen=True)
class LocalSourceRoots:
    """Declared lexical roots and their canonical reparse-resolved destinations."""

    lexical: tuple[Path, ...]
    canonical: tuple[Path, ...]


def load_local_path_policy(policy_path: Path = DEFAULT_LOCAL_PATH_POLICY) -> LocalPathPolicy:
    """Load the policy fixture shared with the optional Bun doctor."""
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The local path policy fixture is unavailable or invalid") from exc

    limits = raw.get("limits")
    dynamic_markers = raw.get("dynamic_markers")
    remote_prefixes = raw.get("remote_prefixes")
    windows_reserved_device_components = raw.get("windows_reserved_device_components")
    if (
        not isinstance(limits, dict)
        or not all(isinstance(value, int) and value > 0 for value in limits.values())
        or not isinstance(dynamic_markers, list)
        or not all(isinstance(value, str) and value for value in dynamic_markers)
        or not isinstance(remote_prefixes, list)
        or not all(isinstance(value, str) and value for value in remote_prefixes)
        or not isinstance(windows_reserved_device_components, list)
        or not all(isinstance(value, str) and value for value in windows_reserved_device_components)
    ):
        raise RuntimeError("The local path policy fixture has an invalid shape")
    return LocalPathPolicy(
        limits=limits,
        dynamic_markers=tuple(dynamic_markers),
        remote_prefixes=tuple(remote_prefixes),
        windows_reserved_device_components=tuple(windows_reserved_device_components),
    )


def load_yaml_module() -> Any:
    """Import PyYAML lazily so `--help` works even when it is not installed."""
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PyYAML is required to validate bundle YAML. Install it in the active environment with: pip install -r skills/databricks-asset-bundles/scripts/requirements.txt"
        ) from exc
    return yaml


def resolve_bundle_file(bundle_path: Path) -> Path:
    """Resolve a bundle directory or direct YAML path to the root config file."""
    resolved = bundle_path.resolve()
    return resolved / "databricks.yml" if resolved.is_dir() else resolved


def safe_basename(file_reference: str) -> str:
    """Return a bounded non-sensitive basename for user-facing diagnostics."""
    basename = file_reference.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if (
        not basename
        or len(basename) > 96
        or UNSAFE_BASENAME_PATTERN.search(basename)
        or SENSITIVE_NAME_PATTERN.search(basename)
    ):
        return "local-file"
    return basename


def safe_relative_path(file_path: Path, bundle_root: Path) -> str:
    """Return bounded source-relative context without exposing host paths."""
    try:
        relative = file_path.resolve(strict=False).relative_to(bundle_root.resolve(strict=False)).as_posix()
    except ValueError:
        return "bundle-config"
    parts = relative.split("/")
    if not relative or len(relative) > 192 or any(UNSAFE_BASENAME_PATTERN.search(part) for part in parts):
        return "bundle-config"
    return relative


def has_unsafe_windows_component(value: str, policy: LocalPathPolicy) -> bool:
    """Reject Windows device names and ADS syntax before any filesystem operation."""
    for component in re.split(r"[\\/]", value):
        if not component or component in {".", ".."}:
            continue
        if ":" in component:
            return True
        device_base = component.rstrip(" .").split(".", maxsplit=1)[0].upper()
        if device_base in policy.windows_reserved_device_components:
            return True
    return False


def classify_path_reference(file_reference: str, policy: LocalPathPolicy) -> str:
    """Classify without touching the filesystem: dynamic, remote, local, or unsafe host path."""
    value = file_reference.strip()
    lowered = value.lower()
    if any(marker in value for marker in policy.dynamic_markers):
        return "dynamic"
    if any(lowered.startswith(prefix.lower()) for prefix in policy.remote_prefixes):
        return "remote"
    if (
        not value
        or value.startswith(("/", "\\", "~"))
        or WINDOWS_DRIVE_PATTERN.match(value)
        or lowered.startswith("file:")
        or value.startswith(("//", "\\\\", "\\?\\", "//?/", "//./"))
        or has_unsafe_windows_component(value, policy)
    ):
        return "unsupported-host"
    return "local-relative"


def yaml_event_preflight(text: str, policy: LocalPathPolicy, source: str) -> None:
    """Reject YAML aliases, duplicate keys, and oversized structures before object construction."""
    yaml = load_yaml_module()
    try:
        events = yaml.parse(text, Loader=yaml.SafeLoader)
        stack: list[dict[str, Any]] = []
        depth = 0
        nodes = 0
        scalars = 0
        for event in events:
            event_name = type(event).__name__
            if getattr(event, "anchor", None):
                raise ValueError(f"YAML anchors are not allowed in {source}")
            if event_name == "AliasEvent":
                raise ValueError(f"YAML aliases are not allowed in {source}")
            if event_name in {"MappingStartEvent", "SequenceStartEvent"}:
                if stack and stack[-1]["kind"] == "mapping" and stack[-1]["expecting_key"]:
                    raise ValueError(f"YAML mapping keys must be scalars in {source}")
                nodes += 1
                depth += 1
                if nodes > policy.limits["max_yaml_nodes"]:
                    raise ValueError(f"YAML node count exceeds the configured limit in {source}")
                if depth > policy.limits["max_yaml_depth"]:
                    raise ValueError(f"YAML nesting exceeds the configured limit in {source}")
                stack.append({"kind": "mapping" if event_name == "MappingStartEvent" else "sequence", "expecting_key": event_name == "MappingStartEvent", "keys": set()})
            elif event_name in {"MappingEndEvent", "SequenceEndEvent"}:
                depth -= 1
                if depth < 0 or not stack:
                    raise ValueError(f"YAML structure is invalid in {source}")
                stack.pop()
                if stack and stack[-1]["kind"] == "mapping" and not stack[-1]["expecting_key"]:
                    stack[-1]["expecting_key"] = True
            elif event_name == "ScalarEvent":
                nodes += 1
                scalars += 1
                if nodes > policy.limits["max_yaml_nodes"] or scalars > policy.limits["max_yaml_scalars"]:
                    raise ValueError(f"YAML node or scalar count exceeds the configured limit in {source}")
                if stack and stack[-1]["kind"] == "mapping":
                    mapping = stack[-1]
                    if mapping["expecting_key"]:
                        if event.value == "<<" or getattr(event, "tag", None) == "tag:yaml.org,2002:merge":
                            raise ValueError(f"YAML merge keys are not allowed in {source}")
                        key = (getattr(event, "tag", None), event.value)
                        if key in mapping["keys"]:
                            raise ValueError(f"YAML duplicate keys are not allowed in {source}")
                        mapping["keys"].add(key)
                        mapping["expecting_key"] = False
                    else:
                        mapping["expecting_key"] = True
        if stack or depth:
            raise ValueError(f"YAML structure is incomplete in {source}")
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {source}") from exc


def load_yaml_mapping(file_path: Path, policy: LocalPathPolicy, source: str) -> tuple[dict[str, Any], str]:
    """Bounded-load a YAML mapping after rejecting risky parser features."""
    try:
        if not file_path.exists() or not file_path.is_file():
            raise ValueError(f"Bundle configuration source '{source}' is missing or not a regular file")
        size = file_path.stat().st_size
        if size > policy.limits["max_yaml_file_bytes"]:
            raise ValueError(f"Bundle configuration source '{source}' exceeds the per-file size limit")
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Bundle configuration source '{source}' is not UTF-8 text") from exc
    except OSError as exc:
        raise ValueError(f"Bundle configuration source '{source}' could not be read") from exc

    yaml_event_preflight(text, policy, source)
    yaml = load_yaml_module()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {source}") from exc

    if data is None:
        return {}, text
    if not isinstance(data, dict):
        raise ValueError(f"Top-level YAML in {source} must be a mapping")
    return data, text


def load_runtime_prefixes(runtime_config: Path | None = None, extra_prefixes: list[str] | None = None) -> tuple[str, ...]:
    """Load approved classic runtime prefixes from the shared policy file."""
    config_path = runtime_config or DEFAULT_RUNTIME_CONFIG
    prefixes = list(DEFAULT_RUNTIME_PREFIXES)

    if config_path.exists():
        runtime_policy, _ = load_yaml_mapping(config_path, load_local_path_policy(), "runtime-policy")
        configured_prefixes = runtime_policy.get("classic_runtime_prefixes")
        if not isinstance(configured_prefixes, list) or not configured_prefixes:
            raise ValueError("Runtime policy must define a non-empty classic_runtime_prefixes list")
        invalid_prefixes = [prefix for prefix in configured_prefixes if not isinstance(prefix, str) or not prefix.strip()]
        if invalid_prefixes:
            raise ValueError("Runtime policy contains invalid runtime prefixes")
        prefixes = [prefix.strip() for prefix in configured_prefixes]
    elif runtime_config is not None:
        raise FileNotFoundError("Runtime policy file not found")

    if extra_prefixes:
        prefixes.extend(prefix.strip() for prefix in extra_prefixes if prefix.strip())

    return tuple(dict.fromkeys(prefixes))


def validate_tag_mapping(tags: Any, path: str) -> list[ValidationError]:
    """Validate that a tag map contains the required enterprise tag keys."""
    errors: list[ValidationError] = []
    if not isinstance(tags, dict):
        errors.append(ValidationError("Tags should be a mapping so required tag keys can be validated", path, is_warning=True))
        return errors

    missing_tags = sorted(REQUIRED_TAGS - set(tags))
    if missing_tags:
        errors.append(
            ValidationError(
                f"Tags missing required keys: {missing_tags}",
                path,
                is_warning=True,
            )
        )
    return errors


def dependency_package_name(dependency: Any) -> str | None:
    """Extract a package requirement string from a DAB dependency entry."""
    if isinstance(dependency, str):
        return dependency.strip()
    if not isinstance(dependency, dict):
        return None

    pypi_dependency = dependency.get("pypi")
    if isinstance(pypi_dependency, str):
        return pypi_dependency.strip()
    if isinstance(pypi_dependency, dict):
        package = pypi_dependency.get("package")
        if isinstance(package, str):
            return package.strip()

    return None


def is_external_or_variable_dependency(package: str) -> bool:
    """Return whether a dependency should be skipped by PyPI pin checks."""
    lower_package = package.lower()
    return any(
        marker in package
        for marker in ("${", "/", "\\")
    ) or lower_package.endswith(".whl") or "://" in lower_package or lower_package.startswith(("dbfs:", "file:"))


def validate_dependency_pinning(dependencies: Any, path: str) -> list[ValidationError]:
    """Warn when PyPI dependencies are not pinned with exact versions."""
    errors: list[ValidationError] = []
    if not isinstance(dependencies, list):
        return errors

    for index, dependency in enumerate(dependencies):
        package = dependency_package_name(dependency)
        if not package or is_external_or_variable_dependency(package):
            continue

        requirement = package.split(";", maxsplit=1)[0].strip()
        if "==" not in requirement:
            errors.append(
                ValidationError(
                    f"Dependency '{package}' is not pinned with ==",
                    f"{path}[{index}]",
                    is_warning=True,
                )
            )

    return errors


def is_contained(candidate: Path, allowed_roots: LocalSourceRoots) -> bool:
    """Return whether a canonical candidate is under at least one canonical source root."""
    return any(candidate.is_relative_to(root) for root in allowed_roots.canonical)


def is_lexically_contained(candidate: Path, allowed_roots: LocalSourceRoots) -> bool:
    """Reject unsynced traversal before resolving or probing an external location."""
    return any(candidate.is_relative_to(root) for root in allowed_roots.lexical)


def resolve_local_reference(source_directory: Path, file_reference: str) -> Path:
    """Normalize a relative local reference without resolving reparses or probing it."""
    return Path(os.path.abspath(source_directory / file_reference))


def validate_task_file_reference(
    source_directory: Path,
    allowed_roots: LocalSourceRoots,
    file_reference: Any,
    path: str,
    label: str,
    policy: LocalPathPolicy,
    source: str,
) -> tuple[list[ValidationError], Path | None]:
    """Validate a path only after classifying and containing it, never probe unsafe hosts."""
    errors: list[ValidationError] = []
    if not isinstance(file_reference, str) or not file_reference.strip():
        return [ValidationError(f"{label} must be a non-empty string", path, source=source)], None

    path_class = classify_path_reference(file_reference, policy)
    if path_class in {"dynamic", "remote"}:
        return errors, None
    if path_class == "unsupported-host":
        errors.append(
            ValidationError(
                f"{label} must be a relative local path, supported remote/workspace path, or dynamic substitution; host-specific paths are not probed",
                path,
                source=source,
            )
        )
        return errors, None

    lexical = resolve_local_reference(source_directory, file_reference)
    display_name = safe_basename(file_reference)
    if not is_lexically_contained(lexical, allowed_roots):
        errors.append(
            ValidationError(
                f"{label} local file '{display_name}' is outside the bundle root and declared sync.paths; declare its containing source in sync.paths or use a supported remote path",
                path,
                source=source,
            )
        )
        return errors, None

    resolved = lexical.resolve(strict=False)
    if not is_contained(resolved, allowed_roots):
        errors.append(
            ValidationError(
                f"{label} local file '{display_name}' escapes its declared local source through a reparse point; declare its containing source in sync.paths or use a supported remote path",
                path,
                source=source,
            )
        )
        return errors, None

    # Containment is established after following any reparse point. Only now may
    # validation ask the filesystem whether the final file exists or is readable.
    try:
        if not resolved.exists() or not resolved.is_file():
            errors.append(
                ValidationError(
                    f"{label} local file '{display_name}' is missing or not a regular file; restore it, declare its containing source in sync.paths, or use a supported remote path",
                    path,
                    source=source,
                )
            )
            return errors, None
        with resolved.open("rb") as handle:
            handle.read(1)
    except OSError:
        errors.append(
            ValidationError(
                f"{label} local file '{display_name}' is not readable; restore it, declare its containing source in sync.paths, or use a supported remote path",
                path,
                source=source,
            )
        )
        return errors, None
    return errors, resolved


def validate_glob_reference(
    source_directory: Path,
    allowed_roots: LocalSourceRoots,
    pattern: Any,
    path: str,
    policy: LocalPathPolicy,
    source: str,
) -> list[ValidationError]:
    """Contain a pipeline glob's non-wildcard prefix without expanding the glob."""
    if not isinstance(pattern, str) or not pattern.strip():
        return [ValidationError("Pipeline glob include must be a non-empty string", path, source=source)]
    path_class = classify_path_reference(pattern, policy)
    if path_class in {"dynamic", "remote"}:
        return []
    if path_class == "unsupported-host":
        return [
            ValidationError(
                "Pipeline glob include must be a relative local pattern, supported remote/workspace path, or dynamic substitution; host-specific paths are not probed",
                path,
                source=source,
            )
        ]
    normalized = pattern.replace("\\", "/")
    if any(component == ".." for component in normalized.split("/")):
        return [
            ValidationError(
                "Pipeline glob include cannot use parent-directory traversal; declare a contained sync.paths source and use a pattern rooted there",
                path,
                source=source,
            )
        ]
    static_components: list[str] = []
    for component in normalized.split("/"):
        if any(marker in component for marker in ("*", "?", "[")):
            break
        static_components.append(component)
    static_prefix = "/".join(static_components) or "."
    lexical = resolve_local_reference(source_directory, static_prefix)
    if not is_lexically_contained(lexical, allowed_roots):
        return [
            ValidationError(
                "Pipeline glob include is outside the bundle root and declared sync.paths; declare its containing source in sync.paths or use a supported remote path",
                path,
                source=source,
            )
        ]
    resolved = lexical.resolve(strict=False)
    if not is_contained(resolved, allowed_roots):
        return [
            ValidationError(
                "Pipeline glob include escapes its declared local source through a reparse point; declare its containing source in sync.paths or use a supported remote path",
                path,
                source=source,
            )
        ]
    return []


def validate_notebook_pip_usage(
    resolved: Path | None,
    path: str,
    policy: LocalPathPolicy,
    source: str,
) -> list[ValidationError]:
    """Warn when a verified local notebook contains `%pip install` within the scan cap."""
    if resolved is None:
        return []
    try:
        if resolved.stat().st_size > policy.limits["max_task_scan_bytes"]:
            return [
                ValidationError(
                    "Local notebook was not scanned for %pip install because it exceeds the bounded scan limit",
                    path,
                    is_warning=True,
                    source=source,
                )
            ]
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [ValidationError("Could not scan local notebook for %pip install because it is not UTF-8 text", path, is_warning=True, source=source)]
    except OSError:
        return [ValidationError("Could not scan local notebook for %pip install", path, is_warning=True, source=source)]

    if PIP_INSTALL_PATTERN.search(content):
        return [
            ValidationError(
                "Notebook uses %pip install; declare dependencies in the bundle config instead",
                path,
                is_warning=True,
                source=source,
            )
        ]
    return []


def merge_mappings(
    base: dict[str, Any],
    incoming: dict[str, Any],
    errors: list[ValidationError],
    source: str,
    context: SourceContext,
    source_directory: Path,
    path: str = "",
) -> None:
    """Merge one include mapping into the root config."""
    for key, value in incoming.items():
        key_path = f"{path}.{key}" if path else key
        incoming_directory, incoming_source = context.member_origin(
            incoming,
            key,
            source_directory,
            source,
        )
        if key not in base:
            base[key] = value
            context.mark_member(base, key, incoming_directory, incoming_source)
            continue

        existing = base[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            merge_mappings(existing, value, errors, source, context, source_directory, key_path)
            continue

        if isinstance(existing, list) and isinstance(value, list):
            combined = existing + value
            existing_directory, existing_source = context.member_origin(
                base,
                key,
                context.directory_for(base, source_directory),
                context.source_for(base),
            )
            context.mark_container(combined, existing_directory, existing_source)
            for index in range(len(existing)):
                member_directory, member_source = context.member_origin(
                    existing,
                    index,
                    existing_directory,
                    existing_source,
                )
                context.mark_member(combined, index, member_directory, member_source)
            for index in range(len(value)):
                member_directory, member_source = context.member_origin(
                    value,
                    index,
                    incoming_directory,
                    incoming_source,
                )
                context.mark_member(combined, len(existing) + index, member_directory, member_source)
            base[key] = combined
            continue

        if existing != value:
            errors.append(
                ValidationError(
                    f"Include file '{source}' overrides '{key_path}'",
                    key_path,
                    is_warning=True,
                )
            )
        base[key] = value
        context.mark_member(base, key, incoming_directory, incoming_source)


def validate_include_pattern(pattern: Any, path: str, source: str, policy: LocalPathPolicy) -> list[ValidationError]:
    """Reject non-local and escaping include patterns before any glob is evaluated."""
    if not isinstance(pattern, str) or not pattern.strip():
        return [ValidationError("Include entries must be non-empty strings", path, source=source)]
    if classify_path_reference(pattern, policy) != "local-relative":
        return [
            ValidationError(
                "Include entries must be contained relative local patterns; remote, dynamic, and host-specific paths are not loaded",
                path,
                source=source,
            )
        ]
    components = [component for component in re.split(r"[\\/]", pattern) if component not in {"", "."}]
    if any(component == ".." for component in components):
        return [
            ValidationError(
                "Include entries cannot escape the bundle root; keep shared configuration inside the bundle",
                path,
                source=source,
            )
        ]
    if "**" in pattern or any("*" in component or "?" in component for component in components[:-1]):
        return [
            ValidationError(
                "Include entries support bounded flat file globs only",
                path,
                source=source,
            )
        ]
    return []


def bounded_include_matches(
    caller_directory: Path,
    bundle_root: Path,
    pattern: str,
    path: str,
    source: str,
    policy: LocalPathPolicy,
) -> tuple[list[Path], list[ValidationError]]:
    """Evaluate a pre-validated local glob with deterministic ordering and match caps."""
    errors: list[ValidationError] = []
    matches: list[Path] = []
    try:
        iterator = caller_directory.glob(pattern)
        for candidate in iterator:
            canonical = candidate.resolve(strict=False)
            if not canonical.is_relative_to(bundle_root):
                errors.append(
                    ValidationError(
                        "An include match escapes the bundle root through a reparse point and was rejected",
                        path,
                        source=source,
                    )
                )
                continue
            if not canonical.is_file():
                continue
            matches.append(canonical)
            if len(matches) > policy.limits["max_include_matches_per_pattern"]:
                return [], [
                    ValidationError(
                        "Include pattern exceeds the configured match limit; split the include into narrower patterns",
                        path,
                        source=source,
                    )
                ]
    except (OSError, ValueError):
        return [], [ValidationError("Include pattern could not be evaluated safely", path, source=source)]

    matches = sorted(dict.fromkeys(matches), key=lambda item: safe_relative_path(item, bundle_root).casefold())
    if not matches:
        errors.append(ValidationError("Include pattern matched no files", path, is_warning=True, source=source))
    return matches, errors


def load_bundle_with_files(
    bundle_path: Path,
) -> tuple[dict[str, Any], list[ValidationError], list[LoadedYamlFile], SourceContext, LocalPathPolicy]:
    """Load root-only includes with bounded, source-aware YAML parsing."""
    policy = load_local_path_policy()
    errors: list[ValidationError] = []
    config_file = resolve_bundle_file(bundle_path)
    if not config_file.exists():
        raise FileNotFoundError("No bundle file found at the requested bundle path")
    bundle_root = config_file.parent.resolve(strict=False)
    root_source = safe_relative_path(config_file, bundle_root)
    root_config, root_text = load_yaml_mapping(config_file, policy, root_source)
    context = SourceContext()
    context.mark(root_config, config_file.parent, root_source)
    loaded_files = [LoadedYamlFile(config_file, root_source, root_text)]
    aggregate_bytes = len(root_text.encode("utf-8"))
    seen = {config_file.resolve(strict=False)}

    def load_root_includes(config: dict[str, Any], caller_file: Path, caller_source: str) -> None:
        nonlocal aggregate_bytes
        include_patterns = config.get("include")
        if include_patterns is None:
            return
        if not isinstance(include_patterns, list):
            errors.append(ValidationError("'include' must be a list", "include", source=caller_source))
            return
        if len(include_patterns) > policy.limits["max_include_patterns"]:
            errors.append(ValidationError("Include list exceeds the configured pattern limit", "include", source=caller_source))
            return

        for index, include_pattern in enumerate(include_patterns):
            include_path = f"include[{index}]"
            pattern_errors = validate_include_pattern(include_pattern, include_path, caller_source, policy)
            errors.extend(pattern_errors)
            if pattern_errors:
                continue
            assert isinstance(include_pattern, str)
            matches, match_errors = bounded_include_matches(
                caller_file.parent,
                bundle_root,
                include_pattern,
                include_path,
                caller_source,
                policy,
            )
            errors.extend(match_errors)
            for include_file in matches:
                canonical = include_file.resolve(strict=False)
                include_source = safe_relative_path(canonical, bundle_root)
                if canonical in seen:
                    errors.append(ValidationError("Duplicate include was loaded once and ignored thereafter", include_path, is_warning=True, source=caller_source))
                    continue
                if len(loaded_files) >= policy.limits["max_include_files"]:
                    errors.append(ValidationError("Include graph exceeds the configured file limit", include_path, source=caller_source))
                    return
                try:
                    include_config, include_text = load_yaml_mapping(canonical, policy, include_source)
                except ValueError as exc:
                    errors.append(ValidationError(str(exc), include_path, source=caller_source))
                    continue
                source_bytes = len(include_text.encode("utf-8"))
                if aggregate_bytes + source_bytes > policy.limits["max_yaml_aggregate_bytes"]:
                    errors.append(ValidationError("Included YAML exceeds the aggregate size limit", include_path, source=caller_source))
                    return
                aggregate_bytes += source_bytes
                seen.add(canonical)
                context.mark(include_config, canonical.parent, include_source)
                loaded_files.append(LoadedYamlFile(canonical, include_source, include_text))
                fragment = dict(include_config)
                if "include" in fragment:
                    errors.append(
                        ValidationError(
                            "Include directives in included fragments are ignored; only root databricks.yml include is applied",
                            "include",
                            is_warning=True,
                            source=include_source,
                        )
                    )
                    fragment.pop("include", None)
                context.mark(fragment, canonical.parent, include_source)
                merge_mappings(config, fragment, errors, include_source, context, canonical.parent)

    load_root_includes(root_config, config_file, root_source)
    return root_config, errors, loaded_files, context, policy


def load_bundle_config(bundle_path: Path) -> tuple[dict[str, Any], list[ValidationError]]:
    """Load the root config and bounded included configuration files."""
    config, errors, _, _, _ = load_bundle_with_files(bundle_path)
    return config, errors


def validate_current_user_usage(loaded_files: list[LoadedYamlFile]) -> list[ValidationError]:
    """Warn when bundle YAML depends on the interactive current_user lookup."""
    errors: list[ValidationError] = []
    for loaded_file in loaded_files:
        for line_number, line in enumerate(loaded_file.text.splitlines(), start=1):
            if CURRENT_USER_PATTERN in line:
                errors.append(
                    ValidationError(
                        "Avoid ${workspace.current_user.userName}; use variables.user_name and ${var.user_name} for headless validation and service-principal deploys",
                        f"{loaded_file.relative_path}:{line_number}",
                        is_warning=True,
                    )
                )

    return errors


def validate_bundle_section(config: dict[str, Any]) -> list[ValidationError]:
    """Validate the `bundle` section."""
    errors: list[ValidationError] = []

    bundle = config.get("bundle")
    if bundle is None:
        errors.append(ValidationError("Missing required 'bundle' section"))
        return errors
    if not isinstance(bundle, dict):
        errors.append(ValidationError("'bundle' must be a mapping", "bundle"))
        return errors

    bundle_name = bundle.get("name")
    if not isinstance(bundle_name, str) or not bundle_name.strip():
        errors.append(ValidationError("Missing or empty 'bundle.name'", "bundle.name"))
    elif not bundle_name.replace("-", "").replace("_", "").isalnum():
        errors.append(
            ValidationError(
                "Bundle name should only contain alphanumeric characters, hyphens, and underscores",
                "bundle.name",
                is_warning=True,
            )
        )

    if "databricks_cli_version" not in bundle:
        errors.append(
            ValidationError(
                "Consider specifying 'databricks_cli_version' for compatibility",
                "bundle.databricks_cli_version",
                is_warning=True,
            )
        )

    return errors


def validate_permission_entries(
    permissions: Any,
    path: str,
    allowed_levels: frozenset[str],
    *,
    source: str = "",
    context: SourceContext | None = None,
) -> list[ValidationError]:
    """Validate one DAB permission list against its surface-specific level set."""
    errors: list[ValidationError] = []
    if not isinstance(permissions, list):
        errors.append(ValidationError("'permissions' must be a list", path, source=source))
        return errors

    for index, permission in enumerate(permissions):
        entry_path = f"{path}[{index}]"
        entry_source = context.member_origin(permissions, index, Path(), source)[1] if context else source
        if not isinstance(permission, dict):
            errors.append(ValidationError("Permission entry must be a mapping", entry_path, source=entry_source))
            continue

        present_principals = [key for key in PERMISSION_PRINCIPAL_KEYS if key in permission]
        if len(present_principals) != 1:
            detail = f"; found {present_principals}" if present_principals else ""
            errors.append(
                ValidationError(
                    f"Permission entry must define exactly one principal key: user_name, group_name, or service_principal_name{detail}",
                    entry_path,
                    source=entry_source,
                )
            )
        for principal_key in present_principals:
            principal = permission.get(principal_key)
            if not isinstance(principal, str) or not principal.strip():
                errors.append(
                    ValidationError(
                        f"Permission principal '{principal_key}' must be a non-empty string",
                        f"{entry_path}.{principal_key}",
                        source=entry_source,
                    )
                )

        level = permission.get("level")
        if not isinstance(level, str) or not level.strip():
            errors.append(
                ValidationError(
                    "Permission level must be a non-empty string",
                    f"{entry_path}.level",
                    source=entry_source,
                )
            )
        elif level not in allowed_levels:
            errors.append(
                ValidationError(
                    f"Permission level '{level}' is not allowed here; expected one of: {', '.join(sorted(allowed_levels))}",
                    f"{entry_path}.level",
                    source=entry_source,
                )
            )

    return errors


def validate_permissions_section(
    config: dict[str, Any],
    context: SourceContext | None = None,
) -> list[ValidationError]:
    """Validate root bundle permissions and retain the existing owner recommendation."""
    if "permissions" not in config:
        return [
            ValidationError(
                "Missing 'permissions' section - resources will have limited access",
                "permissions",
                is_warning=True,
            )
        ]

    permissions = config.get("permissions")
    source = (
        context.member_origin(config, "permissions", context.directory_for(config, Path()), context.source_for(config))[1]
        if context
        else ""
    )
    errors = validate_permission_entries(
        permissions,
        "permissions",
        BUNDLE_PERMISSION_LEVELS,
        source=source,
        context=context,
    )

    has_manager = any(
        isinstance(permission, dict) and permission.get("level") == "CAN_MANAGE" for permission in permissions
    ) if isinstance(permissions, list) else False
    if isinstance(permissions, list) and not has_manager:
        errors.append(
            ValidationError(
                "No manager permission found - consider adding CAN_MANAGE",
                "permissions",
                is_warning=True,
                source=source,
            )
        )

    return errors


def validate_variables_section(config: dict[str, Any]) -> list[ValidationError]:
    """Validate the `variables` section."""
    errors: list[ValidationError] = []

    variables = config.get("variables")
    if variables is None:
        errors.append(
            ValidationError(
                "Missing 'variables' section - define user_name, catalog, schema, and tags explicitly",
                "variables",
                is_warning=True,
            )
        )
        return errors
    if not isinstance(variables, dict):
        errors.append(ValidationError("'variables' must be a mapping", "variables"))
        return errors

    for recommended_var in ("user_name", "catalog", "schema"):
        if recommended_var not in variables:
            errors.append(
                ValidationError(
                    f"Define variables.{recommended_var} so workspace owner and Unity Catalog targets are explicit",
                    f"variables.{recommended_var}",
                    is_warning=True,
                )
            )

    tags = variables.get("tags")
    if tags is None:
        errors.append(
            ValidationError(
                "Define variables.tags.default with the required enterprise tags",
                "variables.tags",
                is_warning=True,
            )
        )
    elif isinstance(tags, dict):
        tag_default = tags.get("default")
        if tag_default is None:
            errors.append(
                ValidationError(
                    "variables.tags should define a default tag mapping",
                    "variables.tags.default",
                    is_warning=True,
                )
            )
        else:
            errors.extend(validate_tag_mapping(tag_default, "variables.tags.default"))
    else:
        errors.append(ValidationError("variables.tags should be a mapping", "variables.tags", is_warning=True))

    for var_name, var_def in variables.items():
        path = f"variables.{var_name}"
        if isinstance(var_def, dict) and var_def.get("type") == "complex" and "default" not in var_def:
            errors.append(
                ValidationError(
                    "Complex variable missing 'default' value",
                    path,
                    is_warning=True,
                )
            )

    return errors


def validate_targets_section(config: dict[str, Any], context: SourceContext | None = None) -> list[ValidationError]:
    """Validate the `targets` section."""
    errors: list[ValidationError] = []

    targets = config.get("targets")
    if targets is None:
        errors.append(
            ValidationError(
                "Missing 'targets' section - bundle will only work with default settings",
                "targets",
                is_warning=True,
            )
        )
        return errors
    if not isinstance(targets, dict):
        errors.append(ValidationError("'targets' must be a mapping", "targets"))
        return errors

    expected_targets = {"dev", "prod"}
    missing_expected_targets = expected_targets - set(targets)
    if missing_expected_targets:
        errors.append(
            ValidationError(
                f"Project has no {sorted(missing_expected_targets)} target; this is acceptable when the repo documents a different target topology",
                "targets",
                is_warning=True,
            )
        )

    default_count = 0
    for target_name, target_def in targets.items():
        path = f"targets.{target_name}"
        if not isinstance(target_def, dict):
            errors.append(ValidationError("Target definition must be a mapping", path))
            continue

        if target_def.get("default"):
            default_count += 1

        mode = target_def.get("mode")
        if mode is not None and mode not in {"development", "production"}:
            errors.append(ValidationError(f"Invalid mode '{mode}'", f"{path}.mode"))
        if target_name == "dev" and mode not in {None, "development"}:
            errors.append(ValidationError("The dev target should use mode: development", f"{path}.mode"))
        if target_name == "prod" and mode not in {None, "production"}:
            errors.append(ValidationError("The prod target should use mode: production", f"{path}.mode"))

        if "permissions" in target_def:
            permissions = target_def.get("permissions")
            source = (
                context.member_origin(
                    target_def,
                    "permissions",
                    context.directory_for(target_def, Path()),
                    context.source_for(target_def),
                )[1]
                if context
                else ""
            )
            errors.extend(
                validate_permission_entries(
                    permissions,
                    f"{path}.permissions",
                    BUNDLE_PERMISSION_LEVELS,
                    source=source,
                    context=context,
                )
            )

    if default_count == 0:
        errors.append(
            ValidationError(
                "No default target specified - one target should have 'default: true'",
                "targets",
                is_warning=True,
            )
        )
    elif default_count > 1:
        errors.append(ValidationError("Multiple targets marked as default", "targets"))

    prod_target = targets.get("prod")
    root_run_as = config.get("run_as")
    root_service_principal = isinstance(root_run_as, dict) and bool(root_run_as.get("service_principal_name"))
    prod_service_principal = False
    if isinstance(prod_target, dict):
        prod_run_as = prod_target.get("run_as")
        prod_service_principal = isinstance(prod_run_as, dict) and bool(prod_run_as.get("service_principal_name"))
    if isinstance(prod_target, dict) and not root_service_principal and not prod_service_principal:
        errors.append(
            ValidationError(
                "Production target should run as a service principal via targets.prod.run_as.service_principal_name or root run_as.service_principal_name",
                "targets.prod.run_as.service_principal_name",
                is_warning=True,
            )
        )

    return errors


def clone_with_origin(value: Any, context: SourceContext, fallback_directory: Path) -> Any:
    """Clone a config subtree while preserving the original declaration origin."""
    if isinstance(value, dict):
        clone: dict[str, Any] = {}
        container_directory = context.directory_for(value, fallback_directory)
        container_source = context.source_for(value)
        context.mark_container(clone, container_directory, container_source)
        for key, child in value.items():
            member_directory, member_source = context.member_origin(
                value,
                key,
                container_directory,
                container_source,
            )
            context.mark_member(clone, key, member_directory, member_source)
            clone[key] = clone_with_origin(child, context, fallback_directory)
        return clone
    if isinstance(value, list):
        clone_list: list[Any] = []
        container_directory = context.directory_for(value, fallback_directory)
        container_source = context.source_for(value)
        context.mark_container(clone_list, container_directory, container_source)
        for index, child in enumerate(value):
            member_directory, member_source = context.member_origin(
                value,
                index,
                container_directory,
                container_source,
            )
            context.mark_member(clone_list, index, member_directory, member_source)
            clone_list.append(clone_with_origin(child, context, fallback_directory))
        return clone_list
    return value


def merge_effective_mappings(base: dict[str, Any], overlay: dict[str, Any], context: SourceContext, fallback_directory: Path) -> None:
    """Apply a DAB target mapping overlay while retaining source origins for diagnostics."""
    for key, incoming in overlay.items():
        if key not in base:
            base[key] = clone_with_origin(incoming, context, fallback_directory)
            member_directory, member_source = context.member_origin(
                overlay,
                key,
                context.directory_for(overlay, fallback_directory),
                context.source_for(overlay),
            )
            context.mark_member(base, key, member_directory, member_source)
            continue
        existing = base[key]
        if key == "permissions" and isinstance(existing, list) and isinstance(incoming, list):
            start = len(existing)
            incoming_directory = context.directory_for(incoming, fallback_directory)
            incoming_source = context.source_for(incoming)
            for index, item in enumerate(incoming):
                member_directory, member_source = context.member_origin(
                    incoming,
                    index,
                    incoming_directory,
                    incoming_source,
                )
                context.mark_member(existing, start + index, member_directory, member_source)
                existing.append(clone_with_origin(item, context, fallback_directory))
            continue
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merge_effective_mappings(existing, incoming, context, fallback_directory)
        else:
            base[key] = clone_with_origin(incoming, context, fallback_directory)
            member_directory, member_source = context.member_origin(
                overlay,
                key,
                context.directory_for(overlay, fallback_directory),
                context.source_for(overlay),
            )
            context.mark_member(base, key, member_directory, member_source)


def effective_resources(
    root_resources: Any,
    target_resources: Any,
    bundle_root: Path,
    context: SourceContext,
) -> Any:
    """Return root resources overlaid by a single target's resource mapping."""
    if target_resources is None:
        return clone_with_origin(root_resources, context, bundle_root)
    if not isinstance(target_resources, dict):
        return target_resources
    if not isinstance(root_resources, dict):
        return clone_with_origin(target_resources, context, bundle_root)
    result = clone_with_origin(root_resources, context, bundle_root)
    assert isinstance(result, dict)
    merge_effective_mappings(result, target_resources, context, bundle_root)
    return result


def collect_allowed_source_roots(
    bundle_root: Path,
    context: SourceContext,
    policy: LocalPathPolicy,
    sync_mappings: list[tuple[str, Any]],
) -> tuple[LocalSourceRoots, list[ValidationError]]:
    """Canonicalize this validation context's root and declared local sync paths."""
    errors: list[ValidationError] = []
    lexical_roots: list[Path] = [Path(os.path.abspath(bundle_root))]
    canonical_roots: list[Path] = [bundle_root.resolve(strict=False)]

    for sync_path, sync in sync_mappings:
        if sync is None:
            continue
        source = context.source_for(sync)
        source_directory = context.directory_for(sync, bundle_root)
        if not isinstance(sync, dict):
            errors.append(ValidationError("sync must be a mapping", sync_path, source=source))
            continue
        declared_paths = sync.get("paths")
        if declared_paths is None:
            continue
        if not isinstance(declared_paths, list):
            errors.append(ValidationError("sync.paths must be a list of local directories", f"{sync_path}.paths", source=source))
            continue
        for index, declared_path in enumerate(declared_paths):
            location = f"{sync_path}.paths[{index}]"
            if not isinstance(declared_path, str) or not declared_path.strip():
                errors.append(ValidationError("sync.paths entries must be non-empty relative local directories", location, source=source))
                continue
            if classify_path_reference(declared_path, policy) != "local-relative":
                errors.append(
                    ValidationError(
                        "sync.paths entries must be relative local directories; dynamic, remote, and host-specific values cannot authorize local file checks",
                        location,
                        source=source,
                    )
                )
                continue
            lexical_candidate = resolve_local_reference(source_directory, declared_path)
            candidate = lexical_candidate.resolve(strict=False)
            try:
                if not candidate.exists() or not candidate.is_dir():
                    errors.append(ValidationError("Declared sync.paths directory is missing or not a directory", location, source=source))
                    continue
            except OSError:
                errors.append(ValidationError("Declared sync.paths directory could not be inspected", location, source=source))
                continue
            lexical_roots.append(lexical_candidate)
            canonical_roots.append(candidate)
    return LocalSourceRoots(tuple(dict.fromkeys(lexical_roots)), tuple(dict.fromkeys(canonical_roots))), errors


def validate_resources_mapping(
    resources: Any,
    path: str,
    bundle_root: Path,
    allowed_roots: LocalSourceRoots,
    runtime_prefixes: tuple[str, ...],
    context: SourceContext,
    policy: LocalPathPolicy,
) -> list[ValidationError]:
    """Validate jobs and pipelines at root or within a target resource mapping."""
    errors: list[ValidationError] = []
    source = context.source_for(resources)
    if not isinstance(resources, dict):
        return [ValidationError("resources must be a mapping", path, source=source)]

    for resource_type, resource_definitions in resources.items():
        if not isinstance(resource_definitions, dict):
            continue
        allowed_levels = RESOURCE_PERMISSION_LEVELS.get(resource_type, GENERIC_RESOURCE_PERMISSION_LEVELS)
        for resource_name, resource_definition in resource_definitions.items():
            if not isinstance(resource_definition, dict) or "permissions" not in resource_definition:
                continue
            permissions = resource_definition.get("permissions")
            permission_source = context.member_origin(
                resource_definition,
                "permissions",
                context.directory_for(resource_definition, bundle_root),
                context.source_for(resource_definition),
            )[1]
            permission_path = f"{path}.{resource_type}.{resource_name}.permissions"
            errors.extend(
                validate_permission_entries(
                    permissions,
                    permission_path,
                    allowed_levels,
                    source=permission_source,
                    context=context,
                )
            )

    jobs = resources.get("jobs")
    if jobs is not None:
        if not isinstance(jobs, dict):
            errors.append(ValidationError("resources.jobs must be a mapping", f"{path}.jobs", source=source))
        else:
            for job_name, job_def in jobs.items():
                errors.extend(
                    validate_job(
                        job_def,
                        f"{path}.jobs.{job_name}",
                        bundle_root,
                        allowed_roots,
                        runtime_prefixes,
                        context,
                        policy,
                    )
                )

    pipelines = resources.get("pipelines")
    if pipelines is not None:
        if not isinstance(pipelines, dict):
            errors.append(ValidationError("resources.pipelines must be a mapping", f"{path}.pipelines", source=source))
        else:
            for pipeline_name, pipeline_def in pipelines.items():
                errors.extend(
                    validate_pipeline(
                        pipeline_def,
                        f"{path}.pipelines.{pipeline_name}",
                        bundle_root,
                        allowed_roots,
                        context,
                        policy,
                    )
                )
    return errors


def validate_resources_section(
    config: dict[str, Any],
    bundle_root: Path,
    runtime_prefixes: tuple[str, ...],
    context: SourceContext,
    policy: LocalPathPolicy,
) -> list[ValidationError]:
    """Validate root and each target in isolated effective configuration contexts."""
    errors: list[ValidationError] = []
    root_resources = config.get("resources")
    if root_resources is not None:
        allowed_roots, root_errors = collect_allowed_source_roots(
            bundle_root,
            context,
            policy,
            [("sync", config.get("sync"))],
        )
        errors.extend(root_errors)
        errors.extend(
            validate_resources_mapping(root_resources, "resources", bundle_root, allowed_roots, runtime_prefixes, context, policy)
        )
    targets = config.get("targets")
    if isinstance(targets, dict):
        for target_name, target_def in targets.items():
            if not isinstance(target_def, dict):
                continue
            target_resources = target_def.get("resources")
            if root_resources is None and target_resources is None:
                continue
            allowed_roots, target_errors = collect_allowed_source_roots(
                bundle_root,
                context,
                policy,
                [("sync", config.get("sync")), (f"targets.{target_name}.sync", target_def.get("sync"))],
            )
            errors.extend(target_errors)
            errors.extend(
                validate_resources_mapping(
                    effective_resources(root_resources, target_resources, bundle_root, context),
                    f"targets.{target_name}.resources",
                    bundle_root,
                    allowed_roots,
                    runtime_prefixes,
                    context,
                    policy,
                )
            )
    if root_resources is None and not isinstance(targets, dict):
        errors.append(ValidationError("Missing 'resources' section - bundle has nothing to deploy", "resources", is_warning=True))
    return errors


def validate_task(
    task: Any,
    path: str,
    bundle_root: Path,
    allowed_roots: LocalSourceRoots,
    runtime_prefixes: tuple[str, ...],
    context: SourceContext,
    policy: LocalPathPolicy,
    job_cluster_map: dict[str, dict[str, Any]],
    require_task_key: bool,
    task_keys: set[str] | None = None,
) -> list[ValidationError]:
    """Validate a task, including the concrete task nested by for_each_task."""
    if not isinstance(task, dict):
        return [ValidationError("Task entry must be a mapping", path)]
    errors: list[ValidationError] = []
    source = context.source_for(task)
    source_directory = context.directory_for(task, bundle_root)
    if require_task_key:
        task_key = task.get("task_key")
        if not isinstance(task_key, str) or not task_key.strip():
            errors.append(ValidationError("Task missing 'task_key'", path, source=source))
        elif task_keys is not None and task_key in task_keys:
            errors.append(ValidationError("Duplicate task_key", path, source=source))
        elif task_keys is not None:
            task_keys.add(task_key)

    if "new_cluster" in task:
        errors.extend(validate_cluster_config(task["new_cluster"], f"{path}.new_cluster", runtime_prefixes))
        errors.extend(validate_task_gpu_topology(task, task["new_cluster"], f"{path}.new_cluster"))
    job_cluster_key = task.get("job_cluster_key")
    if isinstance(job_cluster_key, str) and job_cluster_key in job_cluster_map:
        errors.extend(validate_task_gpu_topology(task, job_cluster_map[job_cluster_key], f"{path}.job_cluster_key"))

    found_types = [task_type for task_type in TASK_TYPES if task_type in task]
    if not found_types:
        errors.append(ValidationError(f"Task has no task type defined. Expected one of: {sorted(TASK_TYPES)}", path, source=source))
    elif len(found_types) > 1:
        errors.append(ValidationError(f"Task has multiple task types: {found_types}", path, source=source))

    notebook_task = task.get("notebook_task")
    if isinstance(notebook_task, dict):
        location = f"{path}.notebook_task.notebook_path"
        file_errors, resolved = validate_task_file_reference(
            source_directory,
            allowed_roots,
            notebook_task.get("notebook_path"),
            location,
            "Notebook",
            policy,
            source,
        )
        errors.extend(file_errors)
        errors.extend(validate_notebook_pip_usage(resolved, location, policy, source))

    spark_python_task = task.get("spark_python_task")
    if isinstance(spark_python_task, dict):
        file_errors, _ = validate_task_file_reference(
            source_directory,
            allowed_roots,
            spark_python_task.get("python_file"),
            f"{path}.spark_python_task.python_file",
            "Python file",
            policy,
            source,
        )
        errors.extend(file_errors)

    for_each_task = task.get("for_each_task")
    if for_each_task is not None:
        if not isinstance(for_each_task, dict):
            errors.append(ValidationError("for_each_task must be a mapping", f"{path}.for_each_task", source=source))
        elif "task" not in for_each_task:
            errors.append(ValidationError("for_each_task must define a nested task", f"{path}.for_each_task.task", source=source))
        else:
            errors.extend(
                validate_task(
                    for_each_task["task"],
                    f"{path}.for_each_task.task",
                    bundle_root,
                    allowed_roots,
                    runtime_prefixes,
                    context,
                    policy,
                    job_cluster_map,
                    require_task_key=False,
                )
            )

    task_libraries = task.get("libraries")
    if task_libraries is not None:
        errors.extend(validate_dependency_pinning(task_libraries, f"{path}.libraries"))
    return errors


def validate_job(
    job_def: Any,
    path: str,
    bundle_root: Path,
    allowed_roots: LocalSourceRoots,
    runtime_prefixes: tuple[str, ...],
    context: SourceContext,
    policy: LocalPathPolicy,
) -> list[ValidationError]:
    """Validate a job definition."""
    errors: list[ValidationError] = []
    if not isinstance(job_def, dict):
        errors.append(ValidationError("Job definition must be a mapping", path))
        return errors

    job_name = job_def.get("name")
    if not isinstance(job_name, str) or not job_name.strip():
        errors.append(ValidationError("Job missing 'name' field", path))

    if path.startswith("resources.jobs."):
        job_key = path.removeprefix("resources.jobs.")
        searchable_name = f"{job_key} {job_name if isinstance(job_name, str) else ''}"
        if ROOT_REPAIR_JOB_PATTERN.search(searchable_name):
            errors.append(
                ValidationError(
                    "Repair, one-off, or dev-only jobs should usually be target-scoped under targets.dev.resources or a documented dev-only include pattern",
                    path,
                    is_warning=True,
                )
            )

    if "tags" not in job_def:
        errors.append(
            ValidationError(
                "Job missing 'tags' field - apply tags to every job",
                f"{path}.tags",
                is_warning=True,
            )
        )
    else:
        job_tags = job_def.get("tags")
        if isinstance(job_tags, dict):
            errors.extend(validate_tag_mapping(job_tags, f"{path}.tags"))

    job_libraries = job_def.get("libraries")
    if job_libraries is not None:
        errors.extend(validate_dependency_pinning(job_libraries, f"{path}.libraries"))

    environments = job_def.get("environments")
    if environments is not None:
        if not isinstance(environments, list):
            errors.append(ValidationError("Job 'environments' must be a list", f"{path}.environments"))
        else:
            for index, environment in enumerate(environments):
                env_path = f"{path}.environments[{index}]"
                if not isinstance(environment, dict):
                    errors.append(ValidationError("Environment entry must be a mapping", env_path))
                    continue
                spec = environment.get("spec")
                if spec is None:
                    continue
                if not isinstance(spec, dict):
                    errors.append(ValidationError("Environment spec must be a mapping", f"{env_path}.spec"))
                    continue
                environment_version = spec.get("environment_version")
                if environment_version is None:
                    errors.append(
                        ValidationError(
                            "Serverless environment missing 'environment_version' - pin the project/workspace-supported environment version",
                            f"{env_path}.spec.environment_version",
                        )
                    )
                elif str(environment_version).strip().lower() == "latest":
                    errors.append(
                        ValidationError(
                            "Serverless environment_version should be an explicit project/workspace-supported value, not 'latest'",
                            f"{env_path}.spec.environment_version",
                            is_warning=True,
                        )
                    )
                if "dependencies" in spec:
                    errors.extend(validate_dependency_pinning(spec["dependencies"], f"{env_path}.spec.dependencies"))

    schedule = job_def.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, dict):
            errors.append(ValidationError("Job 'schedule' must be a mapping", f"{path}.schedule"))
        elif "pause_status" not in schedule:
            errors.append(
                ValidationError(
                    "Scheduled jobs should explicitly set schedule.pause_status",
                    f"{path}.schedule.pause_status",
                    is_warning=True,
                )
            )
        elif path.startswith("resources.jobs.") and str(schedule.get("pause_status")) != "PAUSED":
            errors.append(
                ValidationError(
                    "Root-level scheduled jobs should start with schedule.pause_status: PAUSED; enable prod with an explicit prod target override after validation",
                    f"{path}.schedule.pause_status",
                    is_warning=True,
                )
            )

    job_cluster_map: dict[str, dict[str, Any]] = {}
    job_clusters = job_def.get("job_clusters")
    if isinstance(job_clusters, list):
        for cluster in job_clusters:
            if not isinstance(cluster, dict):
                continue
            cluster_key = cluster.get("job_cluster_key")
            new_cluster = cluster.get("new_cluster")
            if isinstance(cluster_key, str) and isinstance(new_cluster, dict):
                job_cluster_map[cluster_key] = new_cluster

    tasks = job_def.get("tasks")
    if tasks is None:
        errors.append(ValidationError("Job missing 'tasks' field", path))
    elif not isinstance(tasks, list):
        errors.append(ValidationError("Job 'tasks' must be a list", f"{path}.tasks"))
    elif not tasks:
        errors.append(ValidationError("Job has no tasks defined", f"{path}.tasks"))
    else:
        task_keys: set[str] = set()
        for index, task in enumerate(tasks):
            task_path = f"{path}.tasks[{index}]"
            errors.extend(
                validate_task(
                    task,
                    task_path,
                    bundle_root,
                    allowed_roots,
                    runtime_prefixes,
                    context,
                    policy,
                    job_cluster_map,
                    require_task_key=True,
                    task_keys=task_keys,
                )
            )

    job_clusters = job_def.get("job_clusters")
    if job_clusters is not None:
        if not isinstance(job_clusters, list):
            errors.append(ValidationError("Job 'job_clusters' must be a list", f"{path}.job_clusters"))
        else:
            for index, cluster in enumerate(job_clusters):
                cluster_path = f"{path}.job_clusters[{index}]"
                if not isinstance(cluster, dict):
                    errors.append(ValidationError("Job cluster entry must be a mapping", cluster_path))
                    continue
                if "new_cluster" in cluster:
                    errors.extend(validate_cluster_config(cluster["new_cluster"], f"{cluster_path}.new_cluster", runtime_prefixes))

    return errors


def validate_pipeline(
    pipeline_def: Any,
    path: str,
    bundle_root: Path,
    allowed_roots: LocalSourceRoots,
    context: SourceContext,
    policy: LocalPathPolicy,
) -> list[ValidationError]:
    """Validate a DLT pipeline definition."""
    errors: list[ValidationError] = []
    if not isinstance(pipeline_def, dict):
        errors.append(ValidationError("Pipeline definition must be a mapping", path))
        return errors

    source = context.source_for(pipeline_def)
    source_directory = context.directory_for(pipeline_def, bundle_root)

    pipeline_name = pipeline_def.get("name")
    if not isinstance(pipeline_name, str) or not pipeline_name.strip():
        errors.append(ValidationError("Pipeline missing 'name' field", path))

    if "libraries" not in pipeline_def:
        errors.append(
            ValidationError(
                "Pipeline missing 'libraries' - define the notebook or file that runs the pipeline",
                f"{path}.libraries",
                is_warning=True,
            )
        )
    elif not isinstance(pipeline_def["libraries"], list):
        errors.append(ValidationError("Pipeline libraries must be a list", f"{path}.libraries", source=source))
    else:
        for index, library in enumerate(pipeline_def["libraries"]):
            if not isinstance(library, dict):
                continue
            library_directory, library_source = context.origin_for(library, source_directory, source)
            notebook = library.get("notebook")
            if isinstance(notebook, dict) and "path" in notebook:
                library_path = f"{path}.libraries[{index}].notebook.path"
                file_errors, resolved = validate_task_file_reference(
                    library_directory,
                    allowed_roots,
                    notebook.get("path"),
                    library_path,
                    "Pipeline notebook",
                    policy,
                    library_source,
                )
                errors.extend(file_errors)
                errors.extend(validate_notebook_pip_usage(resolved, library_path, policy, library_source))

            file_library = library.get("file")
            if isinstance(file_library, dict) and "path" in file_library:
                file_errors, _ = validate_task_file_reference(
                    library_directory,
                    allowed_roots,
                    file_library.get("path"),
                    f"{path}.libraries[{index}].file.path",
                    "Pipeline file",
                    policy,
                    library_source,
                )
                errors.extend(file_errors)

            glob_library = library.get("glob")
            if isinstance(glob_library, dict) and "include" in glob_library:
                includes = glob_library.get("include")
                values = includes if isinstance(includes, list) else [includes]
                for glob_index, include_pattern in enumerate(values):
                    suffix = f"[{glob_index}]" if isinstance(includes, list) else ""
                    errors.extend(
                        validate_glob_reference(
                            library_directory,
                            allowed_roots,
                            include_pattern,
                            f"{path}.libraries[{index}].glob.include{suffix}",
                            policy,
                            library_source,
                        )
                    )
    if "target" not in pipeline_def:
        errors.append(
            ValidationError(
                "Pipeline missing 'target' - keep the output catalog and schema explicit",
                f"{path}.target",
                is_warning=True,
            )
        )
    else:
        target = pipeline_def.get("target")
        if isinstance(target, str) and "${var.catalog}.${var.schema}" not in target:
            errors.append(
                ValidationError(
                    "Pipeline target should use ${var.catalog}.${var.schema} so UC output is explicit",
                    f"{path}.target",
                    is_warning=True,
                )
            )

    return errors


def validate_cluster_config(cluster: Any, path: str, runtime_prefixes: tuple[str, ...]) -> list[ValidationError]:
    """Validate cluster configuration for runtime version."""
    errors: list[ValidationError] = []
    if not isinstance(cluster, dict):
        errors.append(ValidationError("Cluster definition must be a mapping", path))
        return errors

    version = cluster.get("spark_version")
    if isinstance(version, str) and version and not version.startswith(runtime_prefixes):
        errors.append(
            ValidationError(
                f"Cluster spark_version should match a verified runtime prefix from supported-runtimes.yml ({', '.join(runtime_prefixes)}), found '{version}'",
                f"{path}.spark_version",
                is_warning=True,
            )
        )

    return errors


def is_gpu_cluster(cluster: dict[str, Any]) -> bool:
    """Return whether the cluster appears to use GPU compute."""
    values = [
        cluster.get("spark_version"),
        cluster.get("node_type_id"),
        cluster.get("driver_node_type_id"),
        cluster.get("instance_pool_id"),
    ]
    return any(isinstance(value, str) and GPU_CLUSTER_HINT_PATTERN.search(value) for value in values)


def is_single_node_cluster(cluster: dict[str, Any]) -> bool:
    """Return whether a cluster is configured as single-node."""
    num_workers = cluster.get("num_workers")
    if isinstance(num_workers, int) and num_workers == 0:
        return True
    if isinstance(num_workers, str) and num_workers.strip() == "0":
        return True

    spark_conf = cluster.get("spark_conf")
    if isinstance(spark_conf, dict):
        profile = spark_conf.get("spark.databricks.cluster.profile")
        master = spark_conf.get("spark.master")
        if profile == "singleNode" or master == "local[*]":
            return True

    return False


def task_may_load_spark_tables(task: dict[str, Any]) -> bool:
    """Return whether a task shape commonly loads Spark or Delta tables."""
    return "notebook_task" in task or "spark_python_task" in task


def validate_task_gpu_topology(task: Any, cluster: Any, path: str) -> list[ValidationError]:
    """Validate GPU cluster topology against Spark task shapes."""
    errors: list[ValidationError] = []
    if not isinstance(task, dict) or not isinstance(cluster, dict):
        return errors

    if is_gpu_cluster(cluster) and is_single_node_cluster(cluster) and task_may_load_spark_tables(task):
        errors.append(
            ValidationError(
                "Single-node GPU clusters with notebook or Spark Python tasks can fail Spark table reads; use worker-capable compute or split prep and training",
                path,
                is_warning=True,
            )
        )

    return errors


def validate_bundle(
    bundle_path: Path,
    strict: bool = False,
    runtime_config: Path | None = None,
    extra_runtime_prefixes: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Validate a Databricks Asset Bundle."""
    try:
        config, preload_errors, loaded_files, source_context, path_policy = load_bundle_with_files(bundle_path)
        runtime_prefixes = load_runtime_prefixes(runtime_config, extra_runtime_prefixes)
    except FileNotFoundError as exc:
        return False, [f"ERROR: {exc}"]
    except RuntimeError as exc:
        return False, [f"ERROR: {exc}"]
    except ValueError as exc:
        return False, [f"ERROR: {exc}"]

    config_file = resolve_bundle_file(bundle_path)
    bundle_root = config_file.parent

    all_errors: list[ValidationError] = list(preload_errors)
    all_errors.extend(validate_bundle_section(config))
    all_errors.extend(validate_current_user_usage(loaded_files))
    all_errors.extend(validate_permissions_section(config, source_context))
    all_errors.extend(validate_variables_section(config))
    all_errors.extend(validate_targets_section(config, source_context))
    all_errors.extend(validate_resources_section(config, bundle_root, runtime_prefixes, source_context, path_policy))

    blocking_errors = [error for error in all_errors if not error.is_warning]
    messages = [str(error) for error in all_errors]
    success = not all_errors if strict else not blocking_errors
    return success, messages


def strip_log_level(message: str, level: str) -> str:
    """Remove the validator's level prefix before passing to logging."""
    colon_prefix = f"{level}: "
    if message.startswith(colon_prefix):
        return message[len(colon_prefix) :]
    if message.startswith(level):
        return message[len(level) :].lstrip()
    return message


def main() -> int:
    """Run the CLI validator."""
    parser = argparse.ArgumentParser(description="Validate Databricks Asset Bundle configuration")
    parser.add_argument(
        "bundle_path",
        nargs="?",
        default=".",
        help="Path to a bundle directory or root bundle YAML file (default: current directory)",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=None,
        help="Path to a YAML runtime policy file with classic_runtime_prefixes",
    )
    parser.add_argument(
        "--allow-runtime-prefix",
        action="append",
        default=[],
        help="Additional accepted classic spark_version prefix after project verification",
    )

    args = parser.parse_args()
    bundle_path = Path(args.bundle_path).resolve()
    logger.info("Validating requested bundle")

    success, messages = validate_bundle(
        bundle_path,
        strict=args.strict,
        runtime_config=args.runtime_config,
        extra_runtime_prefixes=args.allow_runtime_prefix,
    )
    for message in messages:
        if message.startswith("WARNING"):
            logger.warning(strip_log_level(message, "WARNING"))
        elif message.startswith("ERROR"):
            logger.error(strip_log_level(message, "ERROR"))
        else:
            logger.error(message)

    if success:
        logger.info("Bundle validation passed")
        return 0

    logger.error("Bundle validation failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
