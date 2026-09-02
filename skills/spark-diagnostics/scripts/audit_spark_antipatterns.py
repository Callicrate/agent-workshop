"""Bounded, privacy-safe static checks for Spark and Databricks source files.

The scanner is intentionally heuristic. It identifies source patterns that merit
runtime verification; it does not prove that a workload is serverless, unsafe, or
incorrect. Its JSON report is schema 1 and never includes source text unless the
caller explicitly requests bounded, redacted excerpts.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import ctypes.wintypes
import json
import os
import re
import stat
import tokenize
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
SUPPORTED_SUFFIXES = frozenset({".ipynb", ".py", ".sql", ".yaml", ".yml"})
SKIPPED_DIRECTORIES = frozenset({".git", "__pycache__"})
PATH_CALL_NAMES = frozenset(
    {
        "add",
        "cp",
        "csv",
        "head",
        "json",
        "load",
        "ls",
        "mv",
        "open",
        "option",
        "orc",
        "parquet",
        "put",
        "read",
        "rm",
        "save",
        "text",
        "write",
    }
)
RESERVED_DBFS_PREFIXES = (
    "volumes/",
    "databricks-datasets/",
    "databricks/mlflow",
    "databricks/mlflow-tracking",
    "databricks/mlflow-registry",
    "mlflow/",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic|dpop)\s+[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bgh[pousr]_[a-zA-Z0-9_]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bdapi[a-zA-Z0-9]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|token)\s*[:=]\s*[^\s,;]+"),
)
URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@")
JSON_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class Caps:
    """Positive limits that prevent a scan from becoming an unbounded read."""

    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_notebook_cells: int
    max_notebook_cell_bytes: int
    max_notebook_nodes: int
    max_findings: int
    max_diagnostics: int
    max_directory_entries: int
    max_excerpt_chars: int


@dataclass(frozen=True)
class SourceUnit:
    """One source text unit, optionally a code cell inside an IPYNB file."""

    file: str
    suffix: str
    text: str
    cell: int | None = None


@dataclass(frozen=True)
class Finding:
    """One privacy-safe scanner finding."""

    file: str
    line: int
    pattern: str
    severity: str
    message: str
    fix: str
    cell: int | None = None
    excerpt: str | None = None

    def as_json(self) -> dict[str, Any]:
        """Return the stable public representation, omitting optional fields."""
        value: dict[str, Any] = {
            "file": self.file,
            "line": self.line,
            "pattern": self.pattern,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
        }
        if self.cell is not None:
            value["cell"] = self.cell
        if self.excerpt is not None:
            value["excerpt"] = self.excerpt
        return value


class Audit:
    """Collect bounded findings and value-free diagnostics for one scan."""

    def __init__(self, caps: Caps, include_excerpts: bool) -> None:
        self.caps = caps
        self.include_excerpts = include_excerpts
        self.complete = True
        self.discovered = 0
        self.scanned = 0
        self.skipped = 0
        self.total_bytes = 0
        self.canonical_root: str | None = None
        self.findings: list[Finding] = []
        self.diagnostics: list[dict[str, str]] = []
        self.stop = False
        self._diagnostic_cap_reported = False
        self._finding_cap_reported = False

    def diagnostic(self, code: str, file: str = ".", stage: str = "scan") -> None:
        """Add a value-free diagnostic, preserving a terminal cap diagnostic."""
        self.complete = False
        if len(self.diagnostics) < self.caps.max_diagnostics:
            self.diagnostics.append({"code": code, "file": file, "stage": stage})
            return
        if not self._diagnostic_cap_reported:
            self.diagnostics[-1] = {
                "code": "diagnostics_cap_reached",
                "file": ".",
                "stage": "diagnostics",
            }
            self._diagnostic_cap_reported = True
        self.stop = True

    def add_finding(
        self,
        unit: SourceUnit,
        line: int,
        pattern: str,
        severity: str,
        message: str,
        fix: str,
        source_line: str,
    ) -> None:
        """Add a finding unless the configured finding cap has been reached."""
        if self.stop:
            return
        if len(self.findings) >= self.caps.max_findings:
            if not self._finding_cap_reported:
                self.diagnostic("findings_cap_reached", unit.file, "findings")
                self._finding_cap_reported = True
            self.stop = True
            return
        excerpt = None
        if self.include_excerpts:
            excerpt = redact_text(source_line.strip()[: self.caps.max_excerpt_chars])
        self.findings.append(
            Finding(
                file=unit.file,
                line=line,
                pattern=pattern,
                severity=severity,
                message=message,
                fix=fix,
                cell=unit.cell,
                excerpt=excerpt,
            )
        )

    def report(self) -> dict[str, Any]:
        """Build the schema-1 report with deterministic ordering."""
        ordered_findings = sorted(
            self.findings,
            key=lambda item: (item.file, -1 if item.cell is None else item.cell, item.line, item.pattern),
        )
        ordered_diagnostics = sorted(
            self.diagnostics,
            key=lambda item: (item["file"], item["stage"], item["code"]),
        )
        report: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "complete": self.complete,
            "root": ".",
            "summary": {
                "discovered": self.discovered,
                "scanned": self.scanned,
                "skipped": self.skipped,
                "findings": len(ordered_findings),
            },
            "findings": [finding.as_json() for finding in ordered_findings],
            "diagnostics": ordered_diagnostics,
        }
        return redact_recursive(report)


class InvalidArgumentsError(Exception):
    """Internal sentinel used to keep invalid argument values out of output."""


class QuietArgumentParser(argparse.ArgumentParser):
    """Emit the schema contract, not raw untrusted argument values, on errors."""

    def error(self, message: str) -> None:
        del message
        raise InvalidArgumentsError


def positive_int(raw: str) -> int:
    """Parse an explicit positive CLI cap."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def redact_text(value: str) -> str:
    """Redact common credential shapes from an explicitly requested excerpt."""
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", redacted)


def redact_recursive(value: Any) -> Any:
    """Apply excerpt redaction recursively before serializing a report."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_recursive(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_recursive(item) for key, item in value.items()}
    return value


def is_reparse_or_symlink(file_stat: os.stat_result) -> bool:
    """Reject POSIX symlinks and Windows reparse points without following either."""
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & reparse_point)


def relative_path(root: Path, candidate: Path) -> str | None:
    """Return a slash-only contained path, or None if the candidate escapes root."""
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


class NofollowCapabilityError(OSError):
    """Raised when the host cannot provide a safe no-follow source open."""


class ContainmentError(OSError):
    """Raised when an opened handle is outside the audit root's verified boundary."""


def _normalize_windows_handle_path(value: str) -> str:
    """Normalize a final Win32 handle path for a case-insensitive containment check."""
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _windows_final_handle_path(kernel32: Any, handle: int) -> str:
    """Derive the opened handle's final path before exposing content to the scanner."""
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
    get_final_path.restype = ctypes.wintypes.DWORD
    required = get_final_path(handle, None, 0, 0)
    if required == 0:
        raise NofollowCapabilityError("GetFinalPathNameByHandleW unavailable")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        raise NofollowCapabilityError("final handle path unavailable")
    return _normalize_windows_handle_path(buffer.value)


def _windows_open_nofollow(path: Path, canonical_root: str | None = None) -> int:
    """Open a Windows path itself, reject a reparse handle, then lock its identity."""
    import msvcrt

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.wintypes.DWORD), ("high", ctypes.wintypes.DWORD)]

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", ctypes.wintypes.DWORD),
            ("creation", FileTime),
            ("access", FileTime),
            ("write", FileTime),
            ("volume_serial", ctypes.wintypes.DWORD),
            ("size_high", ctypes.wintypes.DWORD),
            ("size_low", ctypes.wintypes.DWORD),
            ("links", ctypes.wintypes.DWORD),
            ("file_index_high", ctypes.wintypes.DWORD),
            ("file_index_low", ctypes.wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.HANDLE,
    ]
    create_file.restype = ctypes.wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ only: block replacement or modification while read
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    information = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "GetFileInformationByHandle failed")
    if information.attributes & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
        kernel32.CloseHandle(handle)
        raise OSError("reparse handle rejected")
    if canonical_root is not None:
        try:
            final_path = _windows_final_handle_path(kernel32, handle)
        except OSError:
            kernel32.CloseHandle(handle)
            raise
        root = _normalize_windows_handle_path(canonical_root)
        if final_path != root and not final_path.startswith(root + "\\"):
            kernel32.CloseHandle(handle)
            raise ContainmentError("opened handle outside root")
    return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))


def _open_nofollow(path: Path | str, directory_fd: int | None = None, canonical_root: str | None = None) -> int:
    """Return an atomic regular-file descriptor without following a path link."""
    if os.name == "nt":
        if directory_fd is not None:
            raise NofollowCapabilityError("Windows dir_fd unavailable")
        return _windows_open_nofollow(Path(path), canonical_root)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None or os.open not in os.supports_dir_fd:
        raise NofollowCapabilityError("O_NOFOLLOW unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags, dir_fd=directory_fd)


def _read_fd_bounded(file_descriptor: int, maximum: int) -> bytes:
    """Read at most maximum + 1 bytes without a file-object buffering layer."""
    chunks: list[bytes] = []
    total = 0
    while total <= maximum:
        chunk = os.read(file_descriptor, min(64 * 1024, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def read_bounded_file(path: Path | str, audit: Audit, relative: str, directory_fd: int | None = None) -> bytes | None:
    """Atomically open, validate, and bounded-read one regular contained source file."""
    try:
        file_descriptor = _open_nofollow(path, directory_fd, audit.canonical_root)
    except NofollowCapabilityError:
        audit.skipped += 1
        audit.diagnostic("nofollow_capability_unavailable", relative, "read")
        return None
    except FileNotFoundError:
        audit.skipped += 1
        audit.diagnostic("source_race_rejected", relative, "read")
        return None
    except ContainmentError:
        audit.skipped += 1
        audit.diagnostic("handle_containment_rejected", relative, "read")
        return None
    except PermissionError:
        audit.skipped += 1
        audit.diagnostic("permission_error", relative, "read")
        return None
    except OSError:
        audit.skipped += 1
        audit.diagnostic("read_error", relative, "read")
        return None
    try:
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or is_reparse_or_symlink(file_stat):
            audit.skipped += 1
            audit.diagnostic("non_regular_or_reparse_file", relative, "read")
            return None
        if file_stat.st_size > audit.caps.max_file_bytes:
            audit.skipped += 1
            audit.diagnostic("file_size_cap_reached", relative, "read")
            return None
        if audit.total_bytes + file_stat.st_size > audit.caps.max_total_bytes:
            audit.skipped += 1
            audit.diagnostic("total_bytes_cap_reached", relative, "read")
            return None
        data = _read_fd_bounded(file_descriptor, audit.caps.max_file_bytes)
    except PermissionError:
        audit.skipped += 1
        audit.diagnostic("permission_error", relative, "read")
        return None
    except OSError:
        audit.skipped += 1
        audit.diagnostic("read_error", relative, "read")
        return None
    finally:
        os.close(file_descriptor)
    if len(data) > audit.caps.max_file_bytes:
        audit.skipped += 1
        audit.diagnostic("file_size_cap_reached", relative, "read")
        return None
    if audit.total_bytes + len(data) > audit.caps.max_total_bytes:
        audit.skipped += 1
        audit.diagnostic("total_bytes_cap_reached", relative, "read")
        return None
    audit.total_bytes += len(data)
    return data


def mask_python(text: str) -> str | None:
    """Mask Python comments and string literals with tokenize positions preserved."""
    lines = text.splitlines(keepends=True)
    masked = [list(line) for line in lines]
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        for token in tokens:
            if token.type not in {tokenize.COMMENT, tokenize.STRING}:
                continue
            start_line, start_column = token.start
            end_line, end_column = token.end
            for line_index in range(start_line - 1, min(end_line, len(masked))):
                first_column = start_column if line_index == start_line - 1 else 0
                final_column = end_column if line_index == end_line - 1 else len(masked[line_index])
                for column in range(first_column, min(final_column, len(masked[line_index]))):
                    if masked[line_index][column] not in "\r\n":
                        masked[line_index][column] = " "
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return None
    return "".join("".join(line) for line in masked)


def mask_sql(text: str) -> str:
    """Mask SQL comments and quoted literals while preserving line positions."""
    output: list[str] = []
    index = 0
    state = "code"
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            output.append("\n" if current == "\n" else " ")
            if current == "\n":
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "code"
            else:
                output.append("\n" if current == "\n" else " ")
                index += 1
            continue
        if state in {"single", "double", "backtick"}:
            closer = {"single": "'", "double": '"', "backtick": "`"}[state]
            if current == closer and following == closer:
                output.extend((" ", " "))
                index += 2
                continue
            output.append("\n" if current == "\n" else " ")
            if current == closer:
                state = "code"
            index += 1
            continue
        if current == "-" and following == "-":
            output.extend((" ", " "))
            index += 2
            state = "line_comment"
        elif current == "/" and following == "*":
            output.extend((" ", " "))
            index += 2
            state = "block_comment"
        elif current == "'":
            output.append(" ")
            index += 1
            state = "single"
        elif current == '"':
            output.append(" ")
            index += 1
            state = "double"
        elif current == "`":
            output.append(" ")
            index += 1
            state = "backtick"
        else:
            output.append(current)
            index += 1
    return "".join(output)


def strip_yaml_comment(line: str) -> str:
    """Remove YAML comments without treating a quoted # as a comment delimiter."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#":
            return line[:index]
    return line


def mask_yaml_literal_blocks(text: str) -> list[str]:
    """Keep YAML mapping context while blanking block-scalar examples and prose."""
    clean_lines: list[str] = []
    literal_indent: int | None = None
    for raw_line in text.splitlines():
        line = strip_yaml_comment(raw_line)
        indentation = len(line) - len(line.lstrip(" "))
        if literal_indent is not None:
            if not line.strip() or indentation > literal_indent:
                clean_lines.append("")
                continue
            literal_indent = None
        clean_lines.append(line)
        if re.search(r":\s*[>|][+-]?\s*$", line):
            literal_indent = indentation
    return clean_lines


def is_dbfs_root_path(value: str) -> bool:
    """Return true for DBFS root or mount paths, never for documented reserves."""
    normalized = value.strip().strip("'\"").replace("\\", "/").casefold()
    if normalized.startswith("/dbfs/"):
        normalized = "dbfs:/" + normalized[len("/dbfs/") :]
    if not normalized.startswith("dbfs:/"):
        return False
    relative = normalized[len("dbfs:/") :].lstrip("/")
    return not any(relative.startswith(prefix) for prefix in RESERVED_DBFS_PREFIXES)


def ast_call_name(node: ast.expr) -> str | None:
    """Return the final simple call attribute for semantic literal path checks."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def python_semantic_path_findings(unit: SourceUnit, audit: Audit, tree: ast.AST) -> None:
    """Detect DBFS root literals used as path arguments, not documentation strings."""
    source_lines = unit.text.splitlines()
    for node in ast.walk(tree):
        if audit.stop or not isinstance(node, ast.Call):
            continue
        if ast_call_name(node.func) not in PATH_CALL_NAMES:
            continue
        values = [*node.args, *(keyword.value for keyword in node.keywords)]
        for value in values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            if not is_dbfs_root_path(value.value):
                continue
            line = getattr(value, "lineno", 1)
            source_line = source_lines[line - 1] if line <= len(source_lines) else ""
            audit.add_finding(
                unit,
                line,
                "dbfs_root_path",
                "medium",
                "A DBFS root or mount path is used by executable path-handling code.",
                "Use a Unity Catalog Volume, external location, or workspace file as the workload contract requires.",
                source_line,
            )


def ast_root_name(node: ast.AST) -> str | None:
    """Return a receiver's root identifier without treating arbitrary members as Spark."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return ast_root_name(node.value)
    if isinstance(node, ast.Call):
        return ast_root_name(node.func)
    return None


def assignment_targets(node: ast.AST) -> set[str]:
    """Return simple names assigned by an AST assignment target."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {name for item in node.elts for name in assignment_targets(item)}
    return set()


def spark_provenance(tree: ast.AST) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    """Infer only locally evidenced SparkSession, DataFrame, context, and sys names."""
    spark_names: set[str] = set()
    dataframe_names: set[str] = set()
    context_names: set[str] = set()
    spark_context_class_names: set[str] = set()
    sys_names: set[str] = set()
    assignments: list[tuple[set[str], ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    sys_names.add(alias.asname or "sys")
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if node.module.startswith("pyspark") and alias.name == "SparkSession":
                    spark_names.add(alias.asname or alias.name)
                if node.module.startswith("pyspark") and alias.name == "SparkContext":
                    spark_context_class_names.add(alias.asname or alias.name)
                if alias.name == "sys" and node.module == "sys":
                    sys_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            targets = {name for target in node.targets for name in assignment_targets(target)}
            assignments.append((targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((assignment_targets(node.target), node.value))
    for _ in range(len(assignments) + 1):
        changed = False
        for targets, value in assignments:
            root = ast_root_name(value)
            is_context = isinstance(value, ast.Attribute) and value.attr == "sparkContext" and root in spark_names
            is_spark = root == "SparkSession"
            is_dataframe = root in dataframe_names or (root in spark_names and root != "SparkSession")
            for target in targets:
                if is_context and target not in context_names:
                    context_names.add(target)
                    changed = True
                elif is_spark and target not in spark_names:
                    spark_names.add(target)
                    changed = True
                elif is_dataframe and target not in dataframe_names and target not in context_names:
                    dataframe_names.add(target)
                    changed = True
        if not changed:
            break
    return spark_names, dataframe_names, context_names, spark_context_class_names, sys_names


def member_provenance(receiver: ast.AST, spark_names: set[str], dataframe_names: set[str], context_names: set[str]) -> str | None:
    """Classify a member receiver only when its root has local Spark evidence."""
    root = ast_root_name(receiver)
    if root in dataframe_names:
        return "dataframe"
    if root in context_names:
        return "context"
    if root in spark_names:
        return "spark"
    return None


def add_provenanced_finding(
    audit: Audit,
    unit: SourceUnit,
    node: ast.AST,
    provenanced: bool,
    pattern: str,
    severity: str,
    message: str,
    fix: str,
    source_lines: list[str],
) -> None:
    """Emit an authoritative finding only with provenance, else an explicit low-confidence lead."""
    line = getattr(node, "lineno", 1)
    source_line = source_lines[line - 1] if line <= len(source_lines) else ""
    if provenanced:
        audit.add_finding(unit, line, pattern, severity, message, fix, source_line)
    else:
        audit.add_finding(
            unit,
            line,
            f"possible_{pattern}",
            "low",
            "A member name matches a Spark API, but receiver provenance was not established.",
            "Verify that the receiver is a Spark DataFrame, SparkSession, or SparkContext before applying Spark remediation.",
            source_line,
        )


def scan_python(unit: SourceUnit, audit: Audit) -> None:
    """Scan executable Python while excluding comments, strings, and literal examples."""
    masked = mask_python(unit.text)
    if masked is None:
        audit.diagnostic("python_tokenize_error", unit.file, "lex")
        return
    try:
        tree = ast.parse(unit.text)
    except (SyntaxError, ValueError, TypeError):
        audit.diagnostic("python_parse_error", unit.file, "parse")
        return
    source_lines = unit.text.splitlines()
    spark_names, dataframe_names, context_names, spark_context_class_names, sys_names = spark_provenance(tree)
    specific_rdd_values: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "isEmpty":
            receiver = node.func.value
            if isinstance(receiver, ast.Attribute) and receiver.attr == "rdd":
                specific_rdd_values.add(id(receiver))
                add_provenanced_finding(audit, unit, node, member_provenance(receiver.value, spark_names, dataframe_names, context_names) == "dataframe", "serverless_rdd_is_empty", "high", "RDD APIs are unavailable on Databricks serverless compute.", "Use a bounded DataFrame action such as not df.limit(1).take(1), or select compatible compute.", source_lines)
    for node in ast.walk(tree):
        if audit.stop:
            return
        if isinstance(node, ast.Attribute) and node.attr == "rdd" and id(node) not in specific_rdd_values:
            add_provenanced_finding(audit, unit, node, member_provenance(node.value, spark_names, dataframe_names, context_names) == "dataframe", "serverless_rdd_access", "high", "RDD APIs are unavailable on Databricks serverless compute.", "Use DataFrame or SQL APIs, or select compute that supports the required RDD operation.", source_lines)
        if isinstance(node, ast.Attribute) and node.attr == "sparkContext":
            add_provenanced_finding(audit, unit, node, member_provenance(node.value, spark_names, dataframe_names, context_names) == "spark", "serverless_spark_context", "high", "SparkContext is unavailable on Databricks serverless compute.", "Use SparkSession and public DataFrame or SQL APIs, or select compatible compute.", source_lines)
        if isinstance(node, ast.Name) and node.id in spark_context_class_names:
            add_provenanced_finding(audit, unit, node, True, "serverless_spark_context", "high", "SparkContext is unavailable on Databricks serverless compute.", "Use SparkSession and public DataFrame or SQL APIs, or select compatible compute.", source_lines)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        receiver = node.func.value
        provenance = member_provenance(receiver, spark_names, dataframe_names, context_names)
        dataframe_method = provenance == "dataframe"
        if method == "collect":
            add_provenanced_finding(audit, unit, node, dataframe_method, "collect_call", "high", "collect() materializes all selected rows on the driver.", "Use an explicitly bounded sample, aggregation, or distributed write.", source_lines)
        elif method == "toPandas":
            add_provenanced_finding(audit, unit, node, dataframe_method, "toPandas_call", "high", "toPandas() materializes the DataFrame on the driver.", "Use an explicitly bounded sample or a distributed storage handoff.", source_lines)
        elif method == "count":
            add_provenanced_finding(audit, unit, node, dataframe_method, "count_action", "medium", "count() is a full DataFrame action; confirm that the result is required.", "Avoid count() solely for logging, or record a bounded or aggregate diagnostic instead.", source_lines)
        elif method == "show" and show_call_is_high_output(node):
            add_provenanced_finding(audit, unit, node, dataframe_method, "show_high_output", "medium", "show() requests an explicitly high-output or untruncated display.", "Use an intentionally small display size and truncation. Do not add .limit() before show() as redundant advice.", source_lines)
        elif method in {"cache", "persist", "unpersist", "checkpoint"}:
            add_provenanced_finding(audit, unit, node, dataframe_method, f"serverless_df_{method}", "high", "DataFrame cache APIs are unavailable on Databricks serverless compute.", "Remove the cache API for serverless or select a compatible compute target.", source_lines)
        elif method in {"cacheTable", "uncacheTable", "clearCache"}:
            catalog_patterns = {"cacheTable": "cache_table", "uncacheTable": "uncache_table", "clearCache": "clear_cache"}
            add_provenanced_finding(audit, unit, node, provenance == "spark", f"serverless_catalog_{catalog_patterns[method]}", "high", "Catalog cache APIs are unavailable on Databricks serverless compute.", "Remove the cache API for serverless or select a compatible compute target.", source_lines)
        elif method == "broadcast":
            add_provenanced_finding(audit, unit, node, provenance in {"context", "spark"}, "broadcast_usage", "medium", "Broadcast use needs a runtime payload-size and serialization review.", "Inspect cardinality and serialized size at runtime; source-code length does not establish payload size.", source_lines)
        elif method == "exit" and isinstance(receiver, ast.Name):
            add_provenanced_finding(audit, unit, node, receiver.id in sys_names, "sys_exit", "medium", "sys.exit() terminates the driver process rather than reporting a task failure.", "Raise an actionable exception or return through the task's normal error boundary.", source_lines)
    python_semantic_path_findings(unit, audit, tree)


def show_call_is_high_output(call: ast.Call) -> bool:
    """Apply the display threshold to parsed calls rather than text-shaped lookalikes."""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, int):
        if call.args[0].value > 1_000:
            return True
    return any(
        keyword.arg == "truncate" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
        for keyword in call.keywords
    )


def scan_sql(unit: SourceUnit, audit: Audit) -> None:
    """Scan SQL keywords without interpreting comments or quoted literal examples."""
    masked = mask_sql(unit.text)
    source_lines = unit.text.splitlines()
    for line_number, line in enumerate(masked.splitlines(), start=1):
        if audit.stop:
            return
        source_line = source_lines[line_number - 1] if line_number <= len(source_lines) else ""
        specific_temporal = bool(
            re.search(r"\bTIMESTAMP\s+AS\s+OF\s+(?:current_timestamp|now)\s*\(", line, flags=re.IGNORECASE)
        )
        if specific_temporal:
            audit.add_finding(unit, line_number, "sql_time_travel_without_fixed_timestamp", "high", "Delta time travel uses a moving timestamp.", "Use a fixed timestamp or an explicit diagnostic parameter.", source_line)
        elif re.search(r"\b(?:current_date|current_timestamp|now)\s*\(", line, flags=re.IGNORECASE):
            severity = "medium" if re.search(r"\b(?:backfill|diagnos|reproduc)\w*\b", masked, flags=re.IGNORECASE) else "low"
            audit.add_finding(unit, line_number, "sql_wall_clock_window", severity, "Wall-clock SQL makes a diagnostic or backfill window non-reproducible.", "Use a fixed date/timestamp or an explicit run parameter when reproducibility is required.", source_line)
        for command, pattern in (
            (r"\bCACHE\s+TABLE\b", "serverless_sql_cache_table"),
            (r"\bUNCACHE\s+TABLE\b", "serverless_sql_uncache_table"),
            (r"\bREFRESH\s+TABLE\b", "serverless_sql_refresh_table"),
            (r"\bCLEAR\s+CACHE\b", "serverless_sql_clear_cache"),
        ):
            if re.search(command, line, flags=re.IGNORECASE):
                audit.add_finding(unit, line_number, pattern, "high", "SQL cache APIs are unavailable on Databricks serverless compute.", "Remove the cache command for serverless or select a compatible compute target.", source_line)
        if re.search(r"\b(?:prep_train|prep_val|prepare_data)\b", line, flags=re.IGNORECASE):
            audit.add_finding(unit, line_number, "sql_temp_prep_contract", "low", "A prep-table contract needs a producer and consumer audit before optimization or deletion.", "Inventory downstream readers and the intended persistence contract.", source_line)


def yaml_mapping(line: str) -> tuple[int, str] | None:
    """Return indentation and key for a simple YAML mapping line."""
    match = re.match(r"^(\s*)(?:-\s*)?([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
    if match is None:
        return None
    return len(match.group(1)), match.group(2).casefold()


def yaml_cluster_scopes(lines: list[str]) -> list[tuple[int, int, int, int, int]]:
    """Return new_cluster spans and their containing jobs, never document-global context."""
    clusters: list[tuple[int, int, int, int, int]] = []
    job_markers = [(index, info[0]) for index, line in enumerate(lines) if (info := yaml_mapping(line)) and info[1] == "jobs"]
    for start, line in enumerate(lines):
        info = yaml_mapping(line)
        if info is None or info[1] != "new_cluster":
            continue
        indentation = info[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            child = yaml_mapping(lines[index])
            if child is not None and child[0] <= indentation:
                end = index
                break
        job_start, job_end = start, end
        candidate_jobs = [(index, job_indent) for index, job_indent in job_markers if index < start and job_indent < indentation]
        if candidate_jobs:
            jobs_start, jobs_indent = candidate_jobs[-1]
            direct_children = [
                (index, child_indent)
                for index in range(jobs_start + 1, len(lines))
                if (mapping := yaml_mapping(lines[index])) and (child_indent := mapping[0]) > jobs_indent
            ]
            if direct_children:
                job_indent = min(child_indent for _, child_indent in direct_children)
                job_candidates = [index for index, child_indent in direct_children if child_indent == job_indent]
                prior = [index for index in job_candidates if index <= start]
                if prior:
                    job_start = prior[-1]
                    following = [index for index in job_candidates if index > job_start]
                    job_end = following[0] if following else len(lines)
        clusters.append((start, end, job_start, job_end, indentation))
    return clusters


def scan_yaml(unit: SourceUnit, audit: Audit) -> None:
    """Scan YAML fields in their own new_cluster and job context, never globally."""
    raw_lines = unit.text.splitlines()
    lines = mask_yaml_literal_blocks(unit.text)
    for cluster_start, cluster_end, job_start, job_end, _ in yaml_cluster_scopes(lines):
        if audit.stop:
            return
        job_context = "\n".join(lines[job_start:job_end]).casefold()
        explicit_spark_task = bool(re.search(r"\b(?:spark_python_task|spark_submit_task|spark_conf)\b", job_context))
        serverless_target = bool(re.search(r"\bserverless\s*:\s*true\b", job_context))
        workers: list[tuple[int, int]] = []
        gpu_lines: list[tuple[int, str]] = []
        for index in range(cluster_start + 1, cluster_end):
            worker = re.search(r"^\s*(?:-\s*)?num_workers\s*:\s*(\d+)\b", lines[index], flags=re.IGNORECASE)
            if worker:
                workers.append((index, int(worker.group(1))))
            gpu = re.search(r"^\s*(?:-\s*)?(?:node_type_id|driver_node_type_id)\s*:\s*['\"]?([^\s'\"]+)", lines[index], flags=re.IGNORECASE)
            if gpu and re.search(r"\bg\d", gpu.group(1), flags=re.IGNORECASE):
                gpu_lines.append((index, gpu.group(1)))
        multiple_worker_gpus = any(value > 0 for _, value in workers)
        for index, worker_count in workers:
            if worker_count == 0 and not serverless_target:
                severity = "medium" if explicit_spark_task else "low"
                audit.add_finding(unit, index + 1, "dab_num_workers_zero", severity, "num_workers: 0 is a single-node topology; impact depends on the assigned task and access mode.", "Verify the actual task executes Spark/table work before changing topology; use workers only when that contract requires them.", raw_lines[index])
        for index, _ in gpu_lines:
            severity = "medium" if multiple_worker_gpus else "low"
            audit.add_finding(unit, index + 1, "dab_gpu_node_type", severity, "A GPU node type is configured; training parallelism must be verified from runtime evidence.", "When worker GPUs are configured, verify DDP/Trainer multi-GPU behavior or document why it is intentionally unused.", raw_lines[index])
    for line_number, line in enumerate(lines, start=1):
        if audit.stop:
            return
        for candidate in re.findall(r"(?:dbfs:/|/dbfs/)[^\s'\"]*", line, flags=re.IGNORECASE):
            if is_dbfs_root_path(candidate):
                audit.add_finding(unit, line_number, "dbfs_root_path", "medium", "A DBFS root or mount path is configured.", "Use a Unity Catalog Volume, external location, or workspace file as the workload contract requires.", raw_lines[line_number - 1])


def scan_unit(unit: SourceUnit, audit: Audit) -> None:
    """Dispatch source text to its extension-specific lexical scanner."""
    if unit.suffix == ".py":
        if unit.cell is not None:
            magic = re.match(r"^\s*(%%|%)([A-Za-z][A-Za-z0-9_-]*)(?:\s*(.*))?$", unit.text.splitlines()[0] if unit.text.splitlines() else "")
            if magic:
                name = magic.group(2).casefold()
                if name == "sql":
                    first_line = magic.group(3) or ""
                    remaining = unit.text.split("\n", 1)[1] if "\n" in unit.text else ""
                    payload = "\n".join(part for part in (first_line, remaining) if part)
                    scan_sql(SourceUnit(file=unit.file, suffix=".sql", text=payload, cell=unit.cell), audit)
                    return
                audit.diagnostic("unsupported_notebook_magic", unit.file, "notebook")
                return
        scan_python(unit, audit)
    elif unit.suffix == ".sql":
        scan_sql(unit, audit)
    elif unit.suffix in {".yml", ".yaml"}:
        scan_yaml(unit, audit)


class NotebookParseError(ValueError):
    """Value-free failure raised by the bounded notebook event parser."""


class NotebookNodesCapError(NotebookParseError):
    """Raised before a notebook fragment exceeds the configured node budget."""


class NotebookCellBytesCapError(NotebookParseError):
    """Raised before retaining an oversized individual notebook cell."""


class NotebookDuplicateCellsError(NotebookParseError):
    """Raised when a notebook repeats the top-level cells member."""


class NotebookMemberBytesCapError(NotebookParseError):
    """Raised before validating an oversized unknown top-level member."""


def _skip_json_whitespace(data: bytes, index: int) -> int:
    """Advance over JSON whitespace without allocating decoded text."""
    while index < len(data) and data[index] in b" \t\r\n":
        index += 1
    return index


def _json_string_end(data: bytes, index: int) -> int:
    """Return the exclusive end of one JSON string starting at index."""
    if index >= len(data) or data[index] != ord('"'):
        raise NotebookParseError
    index += 1
    while index < len(data):
        current = data[index]
        if current == ord('\\'):
            if index + 1 >= len(data):
                raise NotebookParseError
            escaped = data[index + 1]
            if escaped in b'"\\/bfnrt':
                index += 2
            elif escaped == ord('u') and index + 5 < len(data) and all(character in b"0123456789abcdefABCDEF" for character in data[index + 2 : index + 6]):
                index += 6
            else:
                raise NotebookParseError
        elif current == ord('"'):
            return index + 1
        elif current < 0x20:
            raise NotebookParseError
        else:
            index += 1
    raise NotebookParseError


def _json_value_end(data: bytes, index: int, remaining_nodes: int, maximum_bytes: int | None = None) -> tuple[int, int]:
    """Skip one JSON value while counting structural and scalar nodes."""
    index = _skip_json_whitespace(data, index)
    start = index
    depth = 0
    nodes = 0
    expecting_value = True
    while index < len(data):
        if maximum_bytes is not None and index - start > maximum_bytes:
            raise NotebookCellBytesCapError
        current = data[index]
        if current in b" \t\r\n,:":
            index += 1
            continue
        if current == ord('"'):
            nodes += 1
            index = _json_string_end(data, index)
        elif current in (ord('{'), ord('[')):
            nodes += 1
            depth += 1
            index += 1
        elif current in (ord('}'), ord(']')):
            depth -= 1
            index += 1
            if depth < 0:
                raise NotebookParseError
            if depth == 0 and not expecting_value:
                return index, nodes
        elif current in b"-0123456789tfn":
            scalar_start = index
            nodes += 1
            index += 1
            while index < len(data) and data[index] not in b" \t\r\n,]}":
                index += 1
            scalar = data[scalar_start:index]
            if scalar not in {b"true", b"false", b"null"} and JSON_NUMBER.fullmatch(scalar) is None:
                raise NotebookParseError
        else:
            raise NotebookParseError
        if nodes > remaining_nodes:
            raise NotebookNodesCapError
        expecting_value = False
        if depth == 0:
            return index, nodes
    if start == index:
        raise NotebookParseError
    raise NotebookParseError


def _decode_bounded_json(data: bytes, maximum_bytes: int) -> Any:
    """Decode one bounded JSON fragment, never an entire notebook document."""
    if len(data) > maximum_bytes:
        raise NotebookParseError
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise NotebookParseError from exc


def _notebook_cells_span(data: bytes, maximum_nodes: int, maximum_member_bytes: int) -> tuple[int, int]:
    """Locate the top-level cells array without materializing the notebook object."""
    index = _skip_json_whitespace(data, 0)
    if index >= len(data) or data[index] != ord('{'):
        raise NotebookParseError
    index += 1
    nodes = 1
    while True:
        index = _skip_json_whitespace(data, index)
        if index >= len(data):
            raise NotebookParseError
        if data[index] == ord('}'):
            break
        key_start = index
        key_end = _json_string_end(data, key_start)
        key = _decode_bounded_json(data[key_start:key_end], 4 * 1024)
        if not isinstance(key, str):
            raise NotebookParseError
        nodes += 1
        if nodes > maximum_nodes:
            raise NotebookNodesCapError
        index = _skip_json_whitespace(data, key_end)
        if index >= len(data) or data[index] != ord(':'):
            raise NotebookParseError
        value_start = _skip_json_whitespace(data, index + 1)
        if key == "cells":
            if value_start >= len(data) or data[value_start] != ord('['):
                raise NotebookParseError
            return value_start + 1, nodes + 1
        value_end, used_nodes = _json_value_end(data, value_start, maximum_nodes - nodes)
        if value_end - value_start > maximum_member_bytes:
            raise NotebookMemberBytesCapError
        _decode_bounded_json(data[value_start:value_end], maximum_member_bytes)
        nodes += used_nodes
        index = _skip_json_whitespace(data, value_end)
        if index < len(data) and data[index] == ord(','):
            index += 1
            continue
        if index < len(data) and data[index] == ord('}'):
            break
        raise NotebookParseError
    raise NotebookParseError


def _validate_notebook_tail(data: bytes, index: int, nodes: int, maximum_nodes: int, maximum_member_bytes: int) -> None:
    """Validate remaining top-level members, closing brace, and whitespace-only EOF."""
    index = _skip_json_whitespace(data, index)
    while True:
        if index >= len(data):
            raise NotebookParseError
        if data[index] == ord('}'):
            index = _skip_json_whitespace(data, index + 1)
            if index != len(data):
                raise NotebookParseError
            return
        if data[index] != ord(','):
            raise NotebookParseError
        key_start = _skip_json_whitespace(data, index + 1)
        key_end = _json_string_end(data, key_start)
        key = _decode_bounded_json(data[key_start:key_end], 4 * 1024)
        if key == "cells":
            raise NotebookDuplicateCellsError
        nodes += 1
        if nodes > maximum_nodes:
            raise NotebookNodesCapError
        index = _skip_json_whitespace(data, key_end)
        if index >= len(data) or data[index] != ord(':'):
            raise NotebookParseError
        value_start = _skip_json_whitespace(data, index + 1)
        value_end, used_nodes = _json_value_end(data, value_start, maximum_nodes - nodes)
        if value_end - value_start > maximum_member_bytes:
            raise NotebookMemberBytesCapError
        _decode_bounded_json(data[value_start:value_end], maximum_member_bytes)
        nodes += used_nodes
        index = _skip_json_whitespace(data, value_end)


def notebook_units(data: bytes, relative: str, audit: Audit) -> Iterable[SourceUnit]:
    """Yield capped code cells without expanding the notebook's cells array in memory."""
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        audit.diagnostic("utf8_error", relative, "decode")
        return
    try:
        index, nodes = _notebook_cells_span(data, audit.caps.max_notebook_nodes, audit.caps.max_notebook_cell_bytes)
    except NotebookMemberBytesCapError:
        audit.diagnostic("notebook_member_bytes_cap_reached", relative, "notebook")
        return
    except NotebookNodesCapError:
        audit.diagnostic("notebook_nodes_cap_reached", relative, "parse")
        return
    except NotebookParseError:
        audit.diagnostic("notebook_json_error", relative, "parse")
        return
    code_cells = 0
    cell_index = 0
    while index < len(data):
        if audit.stop:
            return
        index = _skip_json_whitespace(data, index)
        if index >= len(data):
            audit.diagnostic("notebook_json_error", relative, "parse")
            return
        if data[index] == ord(']'):
            try:
                _validate_notebook_tail(data, index + 1, nodes, audit.caps.max_notebook_nodes, audit.caps.max_notebook_cell_bytes)
            except NotebookDuplicateCellsError:
                audit.diagnostic("notebook_duplicate_cells", relative, "schema")
            except NotebookMemberBytesCapError:
                audit.diagnostic("notebook_member_bytes_cap_reached", relative, "notebook")
            except NotebookNodesCapError:
                audit.diagnostic("notebook_nodes_cap_reached", relative, "parse")
            except NotebookParseError:
                audit.diagnostic("notebook_json_error", relative, "parse")
            return
        try:
            cell_end, cell_nodes = _json_value_end(data, index, audit.caps.max_notebook_nodes - nodes, audit.caps.max_notebook_cell_bytes)
        except NotebookCellBytesCapError:
            audit.diagnostic("notebook_cell_bytes_cap_reached", relative, "notebook")
            return
        except NotebookNodesCapError:
            audit.diagnostic("notebook_nodes_cap_reached", relative, "parse")
            return
        except NotebookParseError:
            audit.diagnostic("notebook_json_error", relative, "parse")
            return
        nodes += cell_nodes
        cell_bytes = data[index:cell_end]
        if len(cell_bytes) > audit.caps.max_notebook_cell_bytes:
            audit.diagnostic("notebook_cell_bytes_cap_reached", relative, "notebook")
            return
        try:
            cell = _decode_bounded_json(cell_bytes, audit.caps.max_notebook_cell_bytes)
        except NotebookParseError:
            audit.diagnostic("notebook_json_error", relative, "parse")
            return
        if not isinstance(cell, dict):
            audit.diagnostic("notebook_schema_error", relative, "schema")
            return
        if cell.get("cell_type") == "code":
            if code_cells >= audit.caps.max_notebook_cells:
                audit.diagnostic("notebook_cells_cap_reached", relative, "notebook")
                return
            source = cell.get("source", "")
            if isinstance(source, str):
                text = source
            elif isinstance(source, list) and all(isinstance(item, str) for item in source):
                text = "".join(source)
            else:
                audit.diagnostic("notebook_schema_error", relative, "schema")
                return
            code_cells += 1
            yield SourceUnit(file=relative, suffix=".py", text=text, cell=cell_index)
        cell_index += 1
        index = _skip_json_whitespace(data, cell_end)
        if index < len(data) and data[index] == ord(','):
            index += 1
            continue
        if index < len(data) and data[index] == ord(']'):
            try:
                _validate_notebook_tail(data, index + 1, nodes, audit.caps.max_notebook_nodes, audit.caps.max_notebook_cell_bytes)
            except NotebookDuplicateCellsError:
                audit.diagnostic("notebook_duplicate_cells", relative, "schema")
            except NotebookMemberBytesCapError:
                audit.diagnostic("notebook_member_bytes_cap_reached", relative, "notebook")
            except NotebookNodesCapError:
                audit.diagnostic("notebook_nodes_cap_reached", relative, "parse")
            except NotebookParseError:
                audit.diagnostic("notebook_json_error", relative, "parse")
            return
        audit.diagnostic("notebook_json_error", relative, "parse")
        return


def process_file(
    path: Path | str,
    root: Path,
    audit: Audit,
    directory_fd: int | None = None,
    known_relative: str | None = None,
) -> None:
    """Read and scan one already-discovered, contained supported file."""
    relative = known_relative or relative_path(root, Path(path))
    if relative is None:
        audit.skipped += 1
        audit.diagnostic("path_escape_rejected", ".", "containment")
        return
    data = read_bounded_file(path, audit, relative, directory_fd)
    if data is None:
        return
    suffix = Path(path).suffix.casefold()
    if suffix == ".ipynb":
        units = notebook_units(data, relative, audit)
    else:
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            audit.skipped += 1
            audit.diagnostic("utf8_error", relative, "decode")
            return
        units = (SourceUnit(file=relative, suffix=suffix, text=text),)
    audit.scanned += 1
    for unit in units:
        scan_unit(unit, audit)
        if audit.stop:
            return


def _scan_directory_path(root: Path, audit: Audit) -> None:
    """Traverse contained regular files deterministically without following reparse points."""
    def visit(directory: Path) -> None:
        if audit.stop:
            return
        relative_directory = relative_path(root, directory) or "."
        entries: list[os.DirEntry[str]] = []
        try:
            before_stat = os.lstat(directory)
            before_identity = before_stat.st_dev, before_stat.st_ino
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= audit.caps.max_directory_entries:
                        audit.skipped += 1
                        audit.diagnostic("directory_entries_cap_reached", relative_directory, "discovery")
                        return
                    entries.append(entry)
            after_stat = os.lstat(directory)
            if (after_stat.st_dev, after_stat.st_ino) != before_identity or is_reparse_or_symlink(after_stat):
                audit.diagnostic("directory_identity_changed", relative_directory, "containment")
                return
        except PermissionError:
            audit.diagnostic("permission_error", relative_directory, "discovery")
            return
        except OSError:
            audit.diagnostic("directory_read_error", relative_directory, "discovery")
            return
        for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
            if audit.stop:
                return
            candidate = Path(entry.path)
            relative = relative_path(root, candidate)
            if relative is None:
                audit.skipped += 1
                audit.diagnostic("path_escape_rejected", ".", "containment")
                continue
            if entry.name in SKIPPED_DIRECTORIES:
                audit.skipped += 1
                continue
            try:
                file_stat = os.lstat(candidate)
            except PermissionError:
                audit.skipped += 1
                audit.diagnostic("permission_error", relative, "discovery")
                continue
            except OSError:
                audit.skipped += 1
                audit.diagnostic("stat_error", relative, "discovery")
                continue
            if is_reparse_or_symlink(file_stat):
                audit.skipped += 1
                audit.diagnostic("reparse_or_symlink_rejected", relative, "discovery")
                continue
            if stat.S_ISDIR(file_stat.st_mode):
                visit(candidate)
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                audit.skipped += 1
                continue
            if candidate.suffix.casefold() not in SUPPORTED_SUFFIXES:
                audit.skipped += 1
                continue
            if audit.discovered >= audit.caps.max_files:
                audit.skipped += 1
                audit.diagnostic("file_count_cap_reached", relative, "discovery")
                audit.stop = True
                return
            audit.discovered += 1
            process_file(candidate, root, audit)
        try:
            after_stat = os.lstat(directory)
            if (after_stat.st_dev, after_stat.st_ino) != before_identity or is_reparse_or_symlink(after_stat):
                audit.diagnostic("directory_identity_changed", relative_directory, "containment")
        except OSError:
            audit.diagnostic("directory_identity_changed", relative_directory, "containment")

    visit(root)


def _descriptor_identity(file_descriptor: int) -> tuple[int, int]:
    """Return a stable descriptor identity for directory change detection."""
    file_stat = os.fstat(file_descriptor)
    return file_stat.st_dev, file_stat.st_ino


def _open_directory_nofollow(path: Path | str, directory_fd: int | None = None) -> int:
    """Open one POSIX directory relative to an already-open parent without link following."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None or os.open not in os.supports_dir_fd:
        raise NofollowCapabilityError("descriptor-anchored directory traversal unavailable")
    return os.open(path, os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0), dir_fd=directory_fd)


def _scan_directory_posix(root: Path, audit: Audit) -> None:
    """Traverse through descriptor-anchored directory handles so parent swaps cannot redirect opens."""
    if os.stat not in os.supports_dir_fd:
        audit.diagnostic("directory_containment_capability_unavailable", ".", "containment")
        return
    try:
        root_descriptor = _open_directory_nofollow(root)
    except (NofollowCapabilityError, OSError):
        audit.diagnostic("directory_containment_capability_unavailable", ".", "containment")
        return

    def visit(directory_fd: int, prefix: str) -> None:
        if audit.stop:
            return
        try:
            before_identity = _descriptor_identity(directory_fd)
            entries: list[str] = []
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    if len(entries) >= audit.caps.max_directory_entries:
                        audit.skipped += 1
                        audit.diagnostic("directory_entries_cap_reached", prefix or ".", "discovery")
                        return
                    entries.append(entry.name)
            if _descriptor_identity(directory_fd) != before_identity:
                audit.diagnostic("directory_identity_changed", prefix or ".", "containment")
                return
        except OSError:
            audit.diagnostic("directory_read_error", prefix or ".", "discovery")
            return
        for name in sorted(entries, key=lambda item: (item.casefold(), item)):
            if audit.stop:
                return
            if name in {".", ".."} or "/" in name or "\\" in name:
                audit.skipped += 1
                audit.diagnostic("directory_entry_rejected", prefix or ".", "containment")
                continue
            relative = f"{prefix}/{name}" if prefix else name
            if name in SKIPPED_DIRECTORIES:
                audit.skipped += 1
                continue
            try:
                file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                audit.skipped += 1
                audit.diagnostic("source_race_rejected", relative, "discovery")
                continue
            except PermissionError:
                audit.skipped += 1
                audit.diagnostic("permission_error", relative, "discovery")
                continue
            except OSError:
                audit.skipped += 1
                audit.diagnostic("stat_error", relative, "discovery")
                continue
            if is_reparse_or_symlink(file_stat):
                audit.skipped += 1
                audit.diagnostic("reparse_or_symlink_rejected", relative, "discovery")
                continue
            if stat.S_ISDIR(file_stat.st_mode):
                try:
                    child_descriptor = _open_directory_nofollow(name, directory_fd)
                except (NofollowCapabilityError, OSError):
                    audit.skipped += 1
                    audit.diagnostic("directory_containment_rejected", relative, "containment")
                    continue
                try:
                    visit(child_descriptor, relative)
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                audit.skipped += 1
                continue
            if Path(name).suffix.casefold() not in SUPPORTED_SUFFIXES:
                audit.skipped += 1
                continue
            if audit.discovered >= audit.caps.max_files:
                audit.skipped += 1
                audit.diagnostic("file_count_cap_reached", relative, "discovery")
                audit.stop = True
                return
            audit.discovered += 1
            process_file(name, root, audit, directory_fd, relative)
        try:
            if _descriptor_identity(directory_fd) != before_identity:
                audit.diagnostic("directory_identity_changed", prefix or ".", "containment")
        except OSError:
            audit.diagnostic("directory_identity_changed", prefix or ".", "containment")

    try:
        root_stat = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_stat.st_mode) or is_reparse_or_symlink(root_stat):
            audit.diagnostic("directory_containment_rejected", ".", "containment")
            return
        visit(root_descriptor, "")
    finally:
        os.close(root_descriptor)


def scan_directory(root: Path, audit: Audit) -> None:
    """Use descriptor anchors where supported; otherwise require final-handle containment on Windows."""
    if os.name != "nt":
        _scan_directory_posix(root, audit)
        return
    try:
        audit.canonical_root = str(root.resolve(strict=True))
    except OSError:
        audit.diagnostic("directory_containment_capability_unavailable", ".", "containment")
        return
    _scan_directory_path(root, audit)


def scan_target(target: Path, audit: Audit) -> tuple[dict[str, Any], int]:
    """Scan a validated target and return its report and contract exit code."""
    try:
        target_stat = os.lstat(target)
    except (FileNotFoundError, PermissionError, OSError):
        audit.diagnostic("invalid_target", ".", "target")
        return audit.report(), 2
    if is_reparse_or_symlink(target_stat):
        audit.diagnostic("invalid_target_reparse_or_symlink", ".", "target")
        return audit.report(), 2
    target = Path(os.path.abspath(target))
    if stat.S_ISDIR(target_stat.st_mode):
        scan_directory(target, audit)
    elif stat.S_ISREG(target_stat.st_mode):
        if target.suffix.casefold() not in SUPPORTED_SUFFIXES:
            audit.diagnostic("unsupported_direct_target", target.name, "target")
            return audit.report(), 2
        if os.name == "nt":
            try:
                audit.canonical_root = str(target.parent.resolve(strict=True))
            except OSError:
                audit.diagnostic("directory_containment_capability_unavailable", ".", "containment")
                return audit.report(), 1
        audit.discovered = 1
        process_file(target, target.parent, audit)
    else:
        audit.diagnostic("invalid_target", ".", "target")
        return audit.report(), 2
    return audit.report(), 0 if audit.complete else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the public CLI parser with explicit positive resource limits."""
    parser = QuietArgumentParser(description="Audit Spark and Databricks static anti-patterns")
    parser.add_argument("path", help="Supported file or directory to scan")
    parser.add_argument("--json", action="store_true", help="Retained for compatibility; schema-1 JSON is always emitted")
    parser.add_argument("--excerpts", action="store_true", help="Include bounded, recursively redacted source excerpts")
    parser.add_argument("--max-files", type=positive_int, default=10_000)
    parser.add_argument("--max-file-bytes", type=positive_int, default=8 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=positive_int, default=64 * 1024 * 1024)
    parser.add_argument("--max-notebook-cells", type=positive_int, default=10_000)
    parser.add_argument("--max-notebook-cell-bytes", type=positive_int, default=1 * 1024 * 1024)
    parser.add_argument("--max-notebook-nodes", type=positive_int, default=1_000_000)
    parser.add_argument("--max-findings", type=positive_int, default=10_000)
    parser.add_argument("--max-diagnostics", type=positive_int, default=1_000)
    parser.add_argument("--max-directory-entries", type=positive_int, default=100_000)
    parser.add_argument("--max-excerpt-chars", type=positive_int, default=240)
    return parser


def invalid_arguments_report() -> dict[str, Any]:
    """Return the value-free schema response for an argparse failure."""
    return {
        "schema": SCHEMA_VERSION,
        "complete": False,
        "root": ".",
        "summary": {"discovered": 0, "scanned": 0, "skipped": 0, "findings": 0},
        "findings": [],
        "diagnostics": [{"code": "invalid_arguments", "file": ".", "stage": "args"}],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the scanner and emit exactly one schema-1 JSON document to stdout."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except InvalidArgumentsError:
        print(json.dumps(invalid_arguments_report(), sort_keys=True))
        return 2
    except SystemExit as exc:
        if exc.code == 0:
            raise
        print(json.dumps(invalid_arguments_report(), sort_keys=True))
        return 2
    caps = Caps(
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
        max_notebook_cells=args.max_notebook_cells,
        max_notebook_cell_bytes=args.max_notebook_cell_bytes,
        max_notebook_nodes=args.max_notebook_nodes,
        max_findings=args.max_findings,
        max_diagnostics=args.max_diagnostics,
        max_directory_entries=args.max_directory_entries,
        max_excerpt_chars=args.max_excerpt_chars,
    )
    report, exit_code = scan_target(Path(args.path), Audit(caps, args.excerpts))
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
