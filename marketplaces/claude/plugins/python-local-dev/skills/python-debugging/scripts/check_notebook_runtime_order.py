#!/usr/bin/env python3
"""Statically check bounded notebook runtime order without exposing notebook contents.

This checker never executes cells and cannot prove aliases, dynamic bindings,
call side effects, or whether a branch or iteration will run.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import sys
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from diagnostic_safety import (
    clamp_limit,
    error_envelope,
    read_utf8_bounded,
    safe_path,
    sanitize_text,
)

PLATFORM_NAMES = {"display", "spark", "dbutils", "sc", "sql", "get_ipython"}
BUILTIN_NAMES = set(dir(builtins)) | PLATFORM_NAMES
SETUP_MAGICS = ("%pip", "!pip", "pip install", "python -m pip")
LAZY_ANNOTATIONS = sys.version_info >= (3, 14)
TYPE_ALIAS = getattr(ast, "TypeAlias", ())


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 8 * 1024 * 1024
    max_cells: int = 2_000
    max_total_chars: int = 4 * 1024 * 1024
    max_cell_chars: int = 512 * 1024
    max_cell_lines: int = 25_000
    max_ast_nodes: int = 100_000
    max_ast_depth: int = 512


HARD_LIMITS = Limits(
    max_bytes=32 * 1024 * 1024,
    max_cells=10_000,
    max_total_chars=16 * 1024 * 1024,
    max_cell_chars=1 * 1024 * 1024,
    max_cell_lines=100_000,
    max_ast_nodes=250_000,
    max_ast_depth=1_024,
)


class NotebookInputError(ValueError):
    """Expected invalid-input and bounded-resource failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Finding:
    """A safe notebook runtime-order finding."""

    severity: str
    cell: int
    category: str
    message: str
    segment: int


@dataclass(frozen=True)
class StateSegment:
    """A clean-kernel state segment split by a structural restart call."""

    segment: int
    start_cell: int
    end_cell: int | None = None


@dataclass
class NotebookReport:
    """No-source, deterministic summary for one notebook."""

    notebook: str
    install_cells: list[int] = field(default_factory=list)
    restart_cells: list[int] = field(default_factory=list)
    import_cells: list[int] = field(default_factory=list)
    language_cells: dict[int, str] = field(default_factory=dict)
    first_non_setup_cell: int | None = None
    first_use_cells: dict[str, int] = field(default_factory=dict)
    definition_cells: dict[str, int] = field(default_factory=dict)
    segments: list[StateSegment] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass
class RuntimeState:
    """Mutable module-level state for a single warm-kernel segment."""

    segment: int = 0
    known: set[str] = field(default_factory=lambda: set(BUILTIN_NAMES))
    definitions: dict[str, tuple[int, int]] = field(default_factory=dict)
    first_uses: dict[str, tuple[int, int]] = field(default_factory=dict)
    conditional: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    maybe_deleted: dict[str, int] = field(default_factory=dict)
    star_import_cells: set[int] = field(default_factory=set)
    postponed_annotations: bool = False
    dbutils_restart_intact: bool = True
    ipython_shutdown_intact: bool = True


def _reject_nonfinite(value: str) -> None:
    raise NotebookInputError(
        "invalid_json", "notebook JSON contains a non-finite value"
    )


def _cell_source(cell: dict[str, Any], *, cell_number: int, limits: Limits) -> str:
    source = cell.get("source", "")
    if isinstance(source, str):
        parts = [source]
    elif isinstance(source, list) and all(isinstance(item, str) for item in source):
        parts = source
    else:
        raise NotebookInputError(
            "invalid_shape", f"cell {cell_number} has a non-text source"
        )
    char_count = sum(len(part) for part in parts)
    if char_count > limits.max_cell_chars:
        raise NotebookInputError(
            "cell_char_limit",
            f"cell {cell_number} exceeds the configured character limit",
        )
    source_text = "".join(parts)
    if source_text.count("\n") + 1 > limits.max_cell_lines:
        raise NotebookInputError(
            "cell_line_limit", f"cell {cell_number} exceeds the configured line limit"
        )
    return source_text


def load_notebook(path: Path, *, limits: Limits) -> dict[str, Any]:
    """Read, decode, parse, and shape-check a notebook before analysis allocation."""
    try:
        raw = read_utf8_bounded(path, max_bytes=limits.max_bytes)
    except UnicodeDecodeError as exc:
        raise NotebookInputError("invalid_utf8", "notebook is not valid UTF-8") from exc
    except OSError as exc:
        raise NotebookInputError("read_error", "notebook could not be read") from exc
    except ValueError as exc:
        raise NotebookInputError(
            "byte_limit", "notebook exceeds the configured byte limit"
        ) from exc
    try:
        notebook = json.loads(raw, parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as exc:
        raise NotebookInputError("invalid_json", "notebook is not valid JSON") from exc
    if not isinstance(notebook, dict):
        raise NotebookInputError("invalid_shape", "notebook root must be an object")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise NotebookInputError("invalid_shape", "notebook cells must be an array")
    if len(cells) > limits.max_cells:
        raise NotebookInputError(
            "cell_count_limit", "notebook exceeds the configured cell limit"
        )
    seen_ids: set[str] = set()
    total_chars = 0
    for cell_number, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            raise NotebookInputError(
                "invalid_shape", f"cell {cell_number} must be an object"
            )
        cell_type = cell.get("cell_type")
        if not isinstance(cell_type, str):
            raise NotebookInputError(
                "invalid_shape", f"cell {cell_number} has no cell type"
            )
        cell_id = cell.get("id")
        if cell_id is not None:
            if not isinstance(cell_id, str):
                raise NotebookInputError(
                    "invalid_shape", f"cell {cell_number} has a non-text id"
                )
            if cell_id in seen_ids:
                raise NotebookInputError(
                    "duplicate_cell_id", "notebook contains duplicate cell ids"
                )
            seen_ids.add(cell_id)
        if cell_type == "code":
            total_chars += len(
                _cell_source(cell, cell_number=cell_number, limits=limits)
            )
            if total_chars > limits.max_total_chars:
                raise NotebookInputError(
                    "total_char_limit",
                    "notebook exceeds the configured total source limit",
                )
    return notebook


def code_cells(
    notebook: dict[str, Any], *, limits: Limits
) -> list[tuple[int, dict[str, Any], str]]:
    """Return 1-based code cells after input limits have been checked."""
    return [
        (cell_number, cell, _cell_source(cell, cell_number=cell_number, limits=limits))
        for cell_number, cell in enumerate(notebook["cells"], start=1)
        if cell["cell_type"] == "code"
    ]


def is_install_cell(source: str) -> bool:
    """Return whether a cell asks the runtime to install packages."""
    return any(
        line.strip().lower().startswith(SETUP_MAGICS)
        for line in source.splitlines()
        if line.strip()
    )


def _metadata_language(cell: dict[str, Any], notebook: dict[str, Any]) -> str | None:
    metadata = cell.get("metadata")
    if isinstance(metadata, dict):
        for key in ("language", "languageId"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        vscode = metadata.get("vscode")
        if isinstance(vscode, dict):
            value = vscode.get("languageId")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    root_metadata = notebook.get("metadata")
    if isinstance(root_metadata, dict):
        language_info = root_metadata.get("language_info")
        if isinstance(language_info, dict):
            value = language_info.get("name")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def cell_language(cell: dict[str, Any], notebook: dict[str, Any], source: str) -> str:
    """Classify whole-cell language before Python parsing."""
    metadata_language = _metadata_language(cell, notebook)
    first = next(
        (line.strip().lower() for line in source.splitlines() if line.strip()), ""
    )
    if first.startswith(("%%sql", "%sql", "-- magic %sql")):
        return "sql"
    if first.startswith(("%%python", "%python", "-- magic %python")):
        return "python"
    if first.startswith("%%"):
        return first[2:].split(maxsplit=1)[0] or "unknown"
    if metadata_language and metadata_language not in {"python", "py", "ipython"}:
        return metadata_language
    return "python"


def strip_python_magics(source: str) -> str:
    """Remove line magics and shell lines from an already-Python cell."""
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("%", "!", "-- MAGIC %"))
    )


def _bracket_depth(source: str) -> int:
    """Bound obvious nesting before AST parsing without interpreting the source."""
    depth = 0
    maximum = 0
    quote: str | None = None
    escaped = False
    for char in source:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
            maximum = max(maximum, depth)
        elif char in ")]}":
            depth = max(0, depth - 1)
    return maximum


def _pre_ast_limit(source: str, *, limits: Limits) -> tuple[str, str] | None:
    """Reject risky lexical structure before asking CPython to build an AST."""
    if _bracket_depth(source) > limits.max_ast_depth:
        return "ast_depth_limit", "cell exceeds the pre-AST nesting limit"
    token_count = 0
    operator_chain = 0
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token_info in tokens:
            if token_info.type in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.COMMENT,
            }:
                continue
            token_count += 1
            if token_count * 2 > limits.max_ast_nodes:
                return (
                    "ast_node_estimate_limit",
                    "cell exceeds the pre-AST node estimate limit",
                )
            is_unary = token_info.string in {"not", "await", "+", "-", "~"}
            operator_chain = operator_chain + 1 if is_unary else 0
            if operator_chain > limits.max_ast_depth:
                return (
                    "ast_operator_chain_limit",
                    "cell exceeds the pre-AST operator-chain limit",
                )
    except (IndentationError, tokenize.TokenError):
        # Let ast.parse produce the stable syntax finding for malformed source.
        return None
    return None


def parse_python_cell(
    source: str, cell_number: int, *, limits: Limits
) -> tuple[ast.Module | None, Finding | None]:
    """Parse one bounded Python cell, keeping syntax failures source-free."""
    cleaned = strip_python_magics(source).strip()
    if not cleaned:
        return None, None
    pre_ast_failure = _pre_ast_limit(cleaned, limits=limits)
    if pre_ast_failure is not None:
        category, message = pre_ast_failure
        return None, Finding("error", cell_number, category, message, 0)
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as exc:
        message, _ = sanitize_text(exc.msg, limit=256)
        return None, Finding(
            "error", cell_number, "parse", f"could not parse Python: {message}", 0
        )
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_ast_nodes:
            return None, Finding(
                "error",
                cell_number,
                "ast_node_limit",
                "cell exceeds the AST node limit",
                0,
            )
        if depth > limits.max_ast_depth:
            return None, Finding(
                "error",
                cell_number,
                "ast_depth_limit",
                "cell exceeds the AST depth limit",
                0,
            )
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree, None


class _LoadVisitor(ast.NodeVisitor):
    """Collect expression loads without leaking nested-scope locals into globals."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def loaded_names(nodes: Iterable[ast.AST]) -> set[str]:
    visitor = _LoadVisitor()
    for node in nodes:
        visitor.visit(node)
    return visitor.names


def target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return set().union(*(target_names(element) for element in target.elts))
    return set()


def imported_names(statement: ast.stmt) -> tuple[set[str], bool]:
    if isinstance(statement, ast.Import):
        return {
            alias.asname or alias.name.split(".")[0] for alias in statement.names
        }, False
    if isinstance(statement, ast.ImportFrom):
        return {
            alias.asname or alias.name for alias in statement.names if alias.name != "*"
        }, any(alias.name == "*" for alias in statement.names)
    return set(), False


def _restart_kind(node: ast.AST) -> str | None:
    """Classify a literal direct restart call without resolving aliases or values."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr == "restartPython":
        owner = node.func.value
        return (
            "dbutils"
            if (
                not node.args
                and not node.keywords
                and isinstance(owner, ast.Attribute)
                and owner.attr == "library"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "dbutils"
            )
            else None
        )
    if isinstance(node.func, ast.Attribute) and node.func.attr == "do_shutdown":
        owner = node.func.value
        is_kernel = (
            isinstance(owner, ast.Attribute)
            and owner.attr == "kernel"
            and isinstance(owner.value, ast.Call)
        )
        is_ipython = (
            isinstance(owner.value.func, ast.Name)
            and owner.value.func.id == "get_ipython"
            and not owner.value.args
            and not owner.value.keywords
            if is_kernel
            else False
        )
        restart = node.keywords[0] if len(node.keywords) == 1 else None
        return (
            "ipython"
            if (
                is_ipython
                and not node.args
                and restart is not None
                and restart.arg == "restart"
                and isinstance(restart.value, ast.Constant)
                and restart.value.value is True
            )
            else None
        )
    return None


def _restart_call(node: ast.AST) -> bool:
    """Return whether a node is one supported literal restart call."""
    return _restart_kind(node) is not None


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    """Return a literal dotted-name path without evaluating or resolving it."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return current.id, *reversed(parts)


def _ipython_attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    """Return a literal ``get_ipython()`` attribute path with no call evaluation."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Name)
        and current.func.id == "get_ipython"
        and not current.args
        and not current.keywords
    ):
        return None
    return "get_ipython", *reversed(parts)


def _literal_string(node: ast.AST) -> str | None:
    """Return a source-literal string, never the value of an expression."""
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _reflective_restart_mutations(call: ast.Call) -> set[str]:
    """Recognize supported literal ``setattr`` and ``delattr`` replacements."""
    if not isinstance(call.func, ast.Name) or call.func.id not in {
        "setattr",
        "delattr",
    }:
        return set()
    if len(call.args) < 2:
        return set()
    target = call.args[0]
    attribute = _literal_string(call.args[1])
    mutations: set[str] = set()
    if (_attribute_path(target), attribute) in {
        (("dbutils", "library"), "restartPython"),
        (("dbutils",), "library"),
    }:
        mutations.add("dbutils")
    if (_ipython_attribute_path(target), attribute) in {
        (("get_ipython",), "kernel"),
        (("get_ipython", "kernel"), "do_shutdown"),
    }:
        mutations.add("ipython")
    return mutations


def _vars_restart_mutations(target: ast.AST) -> set[str]:
    """Recognize literal ``vars`` chain-replacement assignment targets."""
    if not isinstance(target, ast.Subscript):
        return set()
    call = target.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "vars"
        and len(call.args) == 1
    ):
        return set()
    root = call.args[0]
    attribute = _literal_string(target.slice)
    mutations: set[str] = set()
    if (_attribute_path(root), attribute) in {
        (("dbutils",), "library"),
        (("dbutils", "library"), "restartPython"),
    }:
        mutations.add("dbutils")
    if (_ipython_attribute_path(root), attribute) in {
        (("get_ipython",), "kernel"),
        (("get_ipython", "kernel"), "do_shutdown"),
    }:
        mutations.add("ipython")
    return mutations


def _direct_restart_statement(statement: ast.stmt) -> bool:
    """Return whether one immediate module statement invokes a restart callable."""
    return isinstance(statement, ast.Expr) and _restart_call(statement.value)


def _direct_restart_kind(statement: ast.stmt) -> str | None:
    """Return the restart family for one immediate expression statement."""
    return _restart_kind(statement.value) if isinstance(statement, ast.Expr) else None


def contains_restart(tree: ast.AST) -> bool:
    """Return whether a module contains a direct restart expression statement."""
    return isinstance(tree, ast.Module) and any(
        _direct_restart_statement(statement) for statement in tree.body
    )


def _add(
    report: NotebookReport,
    state: RuntimeState,
    severity: str,
    cell: int,
    category: str,
    message: str,
) -> None:
    safe_message, _ = sanitize_text(message, limit=512)
    report.findings.append(
        Finding(severity, cell, category, safe_message, state.segment)
    )


def _record_uses(
    report: NotebookReport,
    state: RuntimeState,
    names: set[str],
    *,
    cell: int,
    order: int,
) -> None:
    for name in sorted(names - BUILTIN_NAMES):
        if name in state.deleted:
            _add(
                report,
                state,
                "error",
                cell,
                "deleted-symbol",
                f"symbol {name!r} was deleted in cell {state.deleted[name]}",
            )
            continue
        if name in state.maybe_deleted:
            _add(
                report,
                state,
                "warning",
                cell,
                "conditional-state",
                f"symbol {name!r} may have been deleted in cell {state.maybe_deleted[name]}",
            )
        if name in state.known:
            continue
        if name in state.conditional:
            _add(
                report,
                state,
                "warning",
                cell,
                "conditional-definition",
                f"symbol {name!r} is only conditionally defined in cell {state.conditional[name]}",
            )
            continue
        if state.star_import_cells:
            _add(
                report,
                state,
                "warning",
                cell,
                "star-import-uncertain",
                f"symbol {name!r} may come from a star import",
            )
            continue
        state.first_uses.setdefault(name, (cell, order))
        report.first_use_cells.setdefault(name, cell)


def _record_definitions(
    report: NotebookReport,
    state: RuntimeState,
    names: set[str],
    *,
    cell: int,
    order: int,
    conditional: bool,
    category: str = "definition-after-use",
) -> None:
    for name in sorted(names):
        report.definition_cells.setdefault(name, cell)
        if conditional:
            state.conditional.setdefault(name, cell)
            continue
        first_use = state.first_uses.get(name)
        if first_use is not None and first_use <= (cell, order):
            _add(
                report,
                state,
                "error",
                cell,
                category,
                f"definition for {name!r} appears after its first use in cell {first_use[0]}",
            )
        state.known.add(name)
        state.definitions[name] = (cell, order)
        state.deleted.pop(name, None)
        state.maybe_deleted.pop(name, None)
        state.conditional.pop(name, None)


def _finalize_unresolved(report: NotebookReport, state: RuntimeState) -> None:
    for name, (cell, _) in sorted(state.first_uses.items()):
        if name not in state.known and name not in state.conditional:
            _add(
                report,
                state,
                "warning",
                cell,
                "unresolved-symbol",
                f"symbol {name!r} has no visible import or definition in this kernel segment",
            )


@dataclass
class _Scope:
    """Names bound by one immediately executing lexical scope."""

    kind: str
    known: set[str]
    possible: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)


class _ExecutionAnalyzer:
    """Bounded static execution model for one Python cell.

    The model follows only syntax with an immediate, documented effect.  It never
    executes code, resolves aliases, or proves dynamic object identity, call
    side effects, imports, or branch/iteration outcomes.
    """

    def __init__(
        self, report: NotebookReport, state: RuntimeState, *, cell: int
    ) -> None:
        self.report = report
        self.state = state
        self.cell = cell
        self.event = 0
        self.scopes = [_Scope("module", state.known)]

    def analyze_statement(self, statement: ast.stmt) -> str | None:
        """Process one top-level statement and return an accepted restart family."""
        restart_kind = _direct_restart_kind(statement)
        if restart_kind is not None and self._restart_intact(restart_kind):
            # The caller has already processed this cell's prefix.  The direct
            # call ends the current kernel, so suffix statements are not run.
            return restart_kind
        self._statement(statement)
        return None

    def _restart_intact(self, kind: str) -> bool:
        if kind == "dbutils":
            return self.state.dbutils_restart_intact
        assert kind == "ipython"
        return self.state.ipython_shutdown_intact

    def _invalidate_restart(self, kind: str) -> None:
        if kind == "dbutils":
            self.state.dbutils_restart_intact = False
            return
        assert kind == "ipython"
        self.state.ipython_shutdown_intact = False

    def _position(self) -> int:
        self.event += 1
        return self.event

    def _current_scope(self) -> _Scope:
        return self.scopes[-1]

    def _binding_scope(self) -> _Scope:
        return next(
            scope for scope in reversed(self.scopes) if scope.kind != "comprehension"
        )

    def _scope_deleted(self, name: str) -> bool:
        return any(name in scope.deleted for scope in reversed(self.scopes))

    def _known(self, name: str) -> bool:
        for scope in reversed(self.scopes):
            if name in scope.deleted:
                return False
            if name in scope.known:
                return True
        return False

    def _known_in_nested_scope(self, name: str) -> bool:
        for scope in reversed(self.scopes[1:]):
            if name in scope.deleted:
                return False
            if name in scope.known:
                return True
        return False

    def _shadowed(self, name: str) -> bool:
        return any(name in scope.known for scope in self.scopes[1:])

    def _use(self, name: str) -> None:
        order = self._position()
        if name in BUILTIN_NAMES:
            return
        if self._scope_deleted(name):
            _add(
                self.report,
                self.state,
                "error",
                self.cell,
                "deleted-symbol",
                f"symbol {name!r} was deleted before this use",
            )
            return
        if self._known_in_nested_scope(name):
            return
        if name in self.state.deleted:
            _add(
                self.report,
                self.state,
                "error",
                self.cell,
                "deleted-symbol",
                f"symbol {name!r} was deleted in cell {self.state.deleted[name]}",
            )
            return
        if name in self.state.maybe_deleted:
            _add(
                self.report,
                self.state,
                "warning",
                self.cell,
                "conditional-state",
                f"symbol {name!r} may have been deleted in cell {self.state.maybe_deleted[name]}",
            )
        if self._known(name):
            return
        if name in self.state.conditional:
            _add(
                self.report,
                self.state,
                "warning",
                self.cell,
                "conditional-definition",
                f"symbol {name!r} is only conditionally defined in cell {self.state.conditional[name]}",
            )
            return
        if self.state.star_import_cells:
            _add(
                self.report,
                self.state,
                "warning",
                self.cell,
                "star-import-uncertain",
                f"symbol {name!r} may come from a star import",
            )
            return
        self.state.first_uses.setdefault(name, (self.cell, order))
        self.report.first_use_cells.setdefault(name, self.cell)

    def _define(
        self, name: str, *, imported: bool = False, scope: _Scope | None = None
    ) -> None:
        order = self._position()
        target_scope = scope or self._binding_scope()
        if target_scope.kind in {"module", "conditional"}:
            if name == "dbutils":
                self._invalidate_restart("dbutils")
            elif name == "get_ipython":
                self._invalidate_restart("ipython")
        if target_scope.kind == "module":
            _record_definitions(
                self.report,
                self.state,
                {name},
                cell=self.cell,
                order=order,
                conditional=False,
                category="import-after-use" if imported else "definition-after-use",
            )
            return
        target_scope.known.add(name)
        if target_scope.kind == "conditional":
            target_scope.possible.add(name)

    def _define_target(self, target: ast.AST, *, scope: _Scope | None = None) -> None:
        if isinstance(target, ast.Name):
            self._define(target.id, scope=scope)
            return
        if isinstance(target, ast.Starred):
            self._define_target(target.value, scope=scope)
            return
        if isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._define_target(element, scope=scope)

    def _delete_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._position()
            target_scope = self._binding_scope()
            if target_scope.kind in {"module", "conditional"}:
                if target.id == "dbutils":
                    self._invalidate_restart("dbutils")
                elif target.id == "get_ipython":
                    self._invalidate_restart("ipython")
            if target_scope.kind == "module":
                self.state.known.discard(target.id)
                self.state.definitions.pop(target.id, None)
                self.state.deleted[target.id] = self.cell
                self.state.maybe_deleted.pop(target.id, None)
            else:
                target_scope.known.discard(target.id)
                target_scope.deleted.add(target.id)
            return
        if isinstance(target, ast.Starred):
            self._delete_target(target.value)
            return
        if isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._delete_target(element)
            return
        self._target_location(target)
        self._note_restart_mutation_target(target)

    def _note_restart_mutation_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Starred):
            self._note_restart_mutation_target(target.value)
            return
        if isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._note_restart_mutation_target(element)
            return
        dbutils_path = _attribute_path(target)
        if dbutils_path in {
            ("dbutils", "library"),
            ("dbutils", "library", "restartPython"),
        } or "dbutils" in _vars_restart_mutations(target):
            if not self._shadowed("dbutils"):
                self._invalidate_restart("dbutils")
        ipython_path = _ipython_attribute_path(target)
        if ipython_path in {
            ("get_ipython", "kernel"),
            ("get_ipython", "kernel", "do_shutdown"),
        } or "ipython" in _vars_restart_mutations(target):
            if not self._shadowed("get_ipython"):
                self._invalidate_restart("ipython")

    def _target_location(self, target: ast.AST) -> None:
        if isinstance(target, ast.Attribute):
            self._expression(target.value)
        elif isinstance(target, ast.Subscript):
            self._expression(target.value)
            self._expression(target.slice)
        elif isinstance(target, ast.Starred):
            self._target_location(target.value)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._target_location(element)

    def _statement(self, statement: ast.stmt) -> None:
        if isinstance(statement, ast.Import):
            self.report.import_cells.append(self.cell)
            for alias in statement.names:
                self._define(alias.asname or alias.name.split(".")[0], imported=True)
            return
        if isinstance(statement, ast.ImportFrom):
            self.report.import_cells.append(self.cell)
            if statement.module == "__future__":
                if any(alias.name == "annotations" for alias in statement.names):
                    self.state.postponed_annotations = True
                return
            for alias in statement.names:
                if alias.name == "*":
                    self.state.star_import_cells.add(self.cell)
                    _add(
                        self.report,
                        self.state,
                        "warning",
                        self.cell,
                        "star-import",
                        "star import prevents precise symbol inventory",
                    )
                else:
                    self._define(alias.asname or alias.name, imported=True)
            return
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            self._function(statement)
            return
        if isinstance(statement, ast.ClassDef):
            self._class(statement)
            return
        if isinstance(statement, TYPE_ALIAS):
            self._type_alias(statement)
            return
        if isinstance(statement, ast.Assign):
            self._expression(statement.value)
            for target in statement.targets:
                self._target_location(target)
                self._note_restart_mutation_target(target)
                self._define_target(target)
            return
        if isinstance(statement, ast.AnnAssign):
            if statement.value is not None:
                self._expression(statement.value)
                self._target_location(statement.target)
                self._note_restart_mutation_target(statement.target)
                self._define_target(statement.target)
            elif not isinstance(statement.target, ast.Name):
                self._target_location(statement.target)
            if not (self.state.postponed_annotations or LAZY_ANNOTATIONS):
                self._expression(statement.annotation)
            return
        if isinstance(statement, ast.AugAssign):
            self._target_location(statement.target)
            if isinstance(statement.target, ast.Name):
                self._use(statement.target.id)
            self._note_restart_mutation_target(statement.target)
            self._expression(statement.value)
            self._define_target(statement.target)
            return
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                self._delete_target(target)
            return
        if isinstance(statement, ast.Expr):
            self._expression(statement.value)
            return
        if isinstance(statement, ast.If):
            self._expression(statement.test)
            self._conditional_statements(statement.body)
            self._conditional_statements(statement.orelse)
            return
        if isinstance(statement, ast.While):
            self._expression(statement.test)
            self._conditional_statements(statement.body)
            self._conditional_statements(statement.orelse)
            return
        if isinstance(statement, ast.For | ast.AsyncFor):
            self._expression(statement.iter)
            scope = self._push_conditional()
            self._target_location(statement.target)
            self._note_restart_mutation_target(statement.target)
            self._define_target(statement.target, scope=scope)
            self._statements(statement.body)
            self._pop_conditional(scope)
            self._conditional_statements(statement.orelse)
            return
        if isinstance(statement, ast.With | ast.AsyncWith):
            for item in statement.items:
                self._expression(item.context_expr)
                if item.optional_vars is not None:
                    self._target_location(item.optional_vars)
                    self._note_restart_mutation_target(item.optional_vars)
                    self._define_target(item.optional_vars)
            self._statements(statement.body)
            return
        if isinstance(statement, ast.Try):
            self._conditional_statements(statement.body)
            for handler in statement.handlers:
                if handler.type is not None:
                    self._expression(handler.type)
                scope = self._push_conditional()
                if handler.name:
                    self._define(handler.name, scope=scope)
                self._statements(handler.body)
                self._pop_conditional(scope)
            self._conditional_statements(statement.orelse)
            self._statements(statement.finalbody)
            return
        if isinstance(statement, ast.Match):
            self._expression(statement.subject)
            for case in statement.cases:
                scope = self._push_conditional()
                self._define_pattern(case.pattern, scope=scope)
                if case.guard is not None:
                    self._expression(case.guard)
                self._statements(case.body)
                self._pop_conditional(scope)
            return
        if isinstance(statement, ast.Assert):
            self._expression(statement.test)
            if statement.msg is not None:
                self._expression(statement.msg)
            return
        if isinstance(statement, ast.Raise):
            if statement.exc is not None:
                self._expression(statement.exc)
            if statement.cause is not None:
                self._expression(statement.cause)
            return
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.expr):
                self._expression(child)

    def _statements(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self._statement(statement)

    def _push_conditional(self) -> _Scope:
        scope = _Scope("conditional", set())
        self.scopes.append(scope)
        return scope

    def _pop_conditional(self, scope: _Scope) -> None:
        assert self.scopes[-1] is scope
        self.scopes.pop()
        for name in scope.possible:
            self.state.conditional.setdefault(name, self.cell)
        for name in scope.deleted:
            self.state.maybe_deleted.setdefault(name, self.cell)

    def _conditional_statements(self, statements: list[ast.stmt]) -> None:
        scope = self._push_conditional()
        self._statements(statements)
        self._pop_conditional(scope)

    def _push_type_parameters(self, statement: ast.AST) -> _Scope | None:
        """Expose PEP 695 parameter names without forcing lazy bounds or defaults."""
        parameters = getattr(statement, "type_params", ())
        if not parameters:
            return None
        scope = _Scope("type-parameters", set())
        self.scopes.append(scope)
        for parameter in parameters:
            name = getattr(parameter, "name", None)
            if isinstance(name, str):
                self._define(name, scope=scope)
        return scope

    def _pop_type_parameters(self, scope: _Scope | None) -> None:
        if scope is not None:
            assert self.scopes[-1] is scope
            self.scopes.pop()

    def _function(self, statement: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in statement.decorator_list:
            self._expression(decorator)
        for default in statement.args.defaults:
            self._expression(default)
        for default in statement.args.kw_defaults:
            if default is not None:
                self._expression(default)
        type_parameters = self._push_type_parameters(statement)
        if not (self.state.postponed_annotations or LAZY_ANNOTATIONS):
            if statement.returns is not None:
                self._expression(statement.returns)
            for argument in [
                *statement.args.posonlyargs,
                *statement.args.args,
                *statement.args.kwonlyargs,
            ]:
                if argument.annotation is not None:
                    self._expression(argument.annotation)
            if statement.args.vararg and statement.args.vararg.annotation:
                self._expression(statement.args.vararg.annotation)
            if statement.args.kwarg and statement.args.kwarg.annotation:
                self._expression(statement.args.kwarg.annotation)
        self._pop_type_parameters(type_parameters)
        self._define(statement.name)

    def _class(self, statement: ast.ClassDef) -> None:
        for decorator in statement.decorator_list:
            self._expression(decorator)
        parent = self._current_scope()
        type_parameters = self._push_type_parameters(statement)
        for base in statement.bases:
            self._expression(base)
        for keyword in statement.keywords:
            self._expression(keyword.value)
        self.scopes.append(_Scope("class", set()))
        self._statements(statement.body)
        self.scopes.pop()
        self._pop_type_parameters(type_parameters)
        self._define(statement.name, scope=parent)

    def _type_alias(self, statement: ast.AST) -> None:
        """Bind a PEP 695 alias name without forcing its lazy value or parameters."""
        assert isinstance(statement, TYPE_ALIAS)
        self._define_target(statement.name)

    def _expression(self, expression: ast.AST) -> None:
        if isinstance(expression, ast.Call):
            for kind in _reflective_restart_mutations(expression):
                root = "dbutils" if kind == "dbutils" else "get_ipython"
                if not self._shadowed(root):
                    self._invalidate_restart(kind)
        if isinstance(expression, ast.Name):
            if isinstance(expression.ctx, ast.Load):
                self._use(expression.id)
            return
        if isinstance(expression, ast.NamedExpr):
            self._expression(expression.value)
            self._define_target(expression.target)
            return
        if isinstance(expression, ast.Lambda):
            for default in expression.args.defaults:
                self._expression(default)
            for default in expression.args.kw_defaults:
                if default is not None:
                    self._expression(default)
            return
        if isinstance(expression, ast.ListComp | ast.SetComp):
            self._comprehension(expression.generators, [expression.elt])
            return
        if isinstance(expression, ast.DictComp):
            self._comprehension(
                expression.generators, [expression.key, expression.value]
            )
            return
        if isinstance(expression, ast.GeneratorExp):
            # Generator bodies and their later clauses run only when consumed.
            # Its outermost iterator is evaluated immediately when it is built.
            if expression.generators:
                self._expression(expression.generators[0].iter)
            return
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                self._expression(child)
            elif isinstance(child, ast.keyword):
                self._expression(child.value)

    def _comprehension(
        self, generators: list[ast.comprehension], values: list[ast.expr]
    ) -> None:
        if not generators:
            return
        self._expression(generators[0].iter)
        conditional = self._push_conditional()
        comprehension = _Scope("comprehension", set())
        self.scopes.append(comprehension)
        for index, generator in enumerate(generators):
            if index:
                self._expression(generator.iter)
            self._target_location(generator.target)
            self._define_target(generator.target, scope=comprehension)
            for condition in generator.ifs:
                self._expression(condition)
        for value in values:
            self._expression(value)
        self.scopes.pop()
        self._pop_conditional(conditional)

    def _define_pattern(self, pattern: ast.pattern, *, scope: _Scope) -> None:
        if isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                self._define_pattern(pattern.pattern, scope=scope)
            if pattern.name is not None:
                self._define(pattern.name, scope=scope)
            return
        if isinstance(pattern, ast.MatchStar):
            if pattern.name is not None:
                self._define(pattern.name, scope=scope)
            return
        if isinstance(pattern, ast.MatchMapping):
            for key in pattern.keys:
                self._expression(key)
            for child in pattern.patterns:
                self._define_pattern(child, scope=scope)
            if pattern.rest is not None:
                self._define(pattern.rest, scope=scope)
            return
        if isinstance(pattern, ast.MatchClass):
            self._expression(pattern.cls)
            for child in [*pattern.patterns, *pattern.kwd_patterns]:
                self._define_pattern(child, scope=scope)
            return
        if isinstance(pattern, ast.MatchSequence | ast.MatchOr):
            for child in pattern.patterns:
                self._define_pattern(child, scope=scope)


def _reset_state(
    report: NotebookReport, state: RuntimeState, cell: int
) -> RuntimeState:
    _finalize_unresolved(report, state)
    if report.segments:
        report.segments[-1] = StateSegment(
            state.segment, report.segments[-1].start_cell, cell
        )
    next_state = RuntimeState(segment=state.segment + 1)
    report.segments.append(StateSegment(next_state.segment, cell, None))
    return next_state


def check_notebook(
    path: Path, *, limits: Limits = Limits(), repo_root: Path | None = None
) -> NotebookReport:
    """Return a safe runtime-order report, raising only expected bounded input errors."""
    notebook = load_notebook(path, limits=limits)
    report = NotebookReport(notebook=safe_path(path, repo_root=repo_root))
    state = RuntimeState()
    report.segments.append(StateSegment(state.segment, 1, None))
    code_seen = False
    install_seen = False
    restart_after_last_install = True
    for cell_number, cell, source in code_cells(notebook, limits=limits):
        language = cell_language(cell, notebook, source)
        if language != "python":
            report.language_cells[cell_number] = language
            _add(
                report,
                state,
                "info",
                cell_number,
                "non-python-cell",
                f"skipped {language} cell",
            )
            continue
        if is_install_cell(source):
            report.install_cells.append(cell_number)
            if code_seen:
                _add(
                    report,
                    state,
                    "error",
                    cell_number,
                    "install-after-code",
                    "%pip or pip install appears after executable Python",
                )
            install_seen = True
            restart_after_last_install = False
            continue
        tree, parse_finding = parse_python_cell(source, cell_number, limits=limits)
        if parse_finding:
            report.findings.append(
                Finding(
                    parse_finding.severity,
                    parse_finding.cell,
                    parse_finding.category,
                    parse_finding.message,
                    state.segment,
                )
            )
            continue
        if tree is None:
            continue
        if report.first_non_setup_cell is None:
            report.first_non_setup_cell = cell_number
        code_seen = True
        analyzer = _ExecutionAnalyzer(report, state, cell=cell_number)
        for statement in tree.body:
            restart_kind = analyzer.analyze_statement(statement)
            if restart_kind is not None:
                report.restart_cells.append(cell_number)
                if install_seen:
                    restart_after_last_install = True
                state = _reset_state(report, state, cell_number)
                break
    _finalize_unresolved(report, state)
    if report.segments:
        report.segments[-1] = StateSegment(
            state.segment, report.segments[-1].start_cell, None
        )
    if install_seen and not restart_after_last_install:
        _add(
            report,
            state,
            "warning",
            0,
            "install-without-restart",
            "notebook has an install cell without a later structural Python restart",
        )
    report.import_cells = sorted(set(report.import_cells))
    return report


def report_to_dict(report: NotebookReport) -> dict[str, Any]:
    return asdict(report)


def limits_from_args(args: argparse.Namespace) -> Limits:
    defaults = Limits()
    values: dict[str, int] = {}
    for field_name in defaults.__dataclass_fields__:
        argument = getattr(args, field_name, None)
        default = getattr(defaults, field_name)
        maximum = getattr(HARD_LIMITS, field_name)
        values[field_name] = (
            default
            if argument is None
            else clamp_limit(argument, minimum=1, maximum=maximum, name=field_name)
        )
    return Limits(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check bounded notebook runtime ordering"
    )
    parser.add_argument("notebooks", nargs="+", help="Notebook .ipynb files to check")
    parser.add_argument(
        "--json", action="store_true", help="Output stable JSON envelopes"
    )
    parser.add_argument(
        "--warnings-fail", action="store_true", help="Treat warnings as failures"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Allow repo-relative notebook names under this root",
    )
    for field_name in Limits().__dataclass_fields__:
        parser.add_argument(
            f"--{field_name.replace('_', '-')}",
            type=int,
            help="Explicit bounded resource override",
        )
    return parser


def result_envelope(
    path: Path,
    *,
    report: NotebookReport | None = None,
    error: NotebookInputError | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    if report is not None:
        return {
            "notebook": report.notebook,
            "ok": True,
            "error": None,
            "report": report_to_dict(report),
        }
    assert error is not None
    return {
        "notebook": safe_path(path, repo_root=repo_root),
        "ok": False,
        "error": error_envelope(error.code, error),
        "report": None,
    }


def batch_envelope(
    results: list[dict[str, Any]], *, warnings_fail: bool
) -> dict[str, Any]:
    finding_count = sum(
        len(result["report"]["findings"])
        for result in results
        if result["report"] is not None
    )
    failed_files = sum(not result["ok"] for result in results)
    error_findings = sum(
        1
        for result in results
        if result["report"] is not None
        for finding in result["report"]["findings"]
        if finding["severity"] == "error"
        or (warnings_fail and finding["severity"] == "warning")
    )
    return {
        "ok": failed_files == 0 and error_findings == 0,
        "schema_version": "1.0",
        "tool": "check_notebook_runtime_order",
        "results": results,
        "aggregate": {
            "files": len(results),
            "failed_files": failed_files,
            "findings": finding_count,
            "failing_findings": error_findings,
        },
    }


def print_text_report(envelope: dict[str, Any]) -> None:
    for result in envelope["results"]:
        print(f"== {result['notebook']} ==")
        if not result["ok"]:
            print(f"ERROR: {result['error']['code']}: {result['error']['message']}")
            continue
        report = result["report"]
        if not report["findings"]:
            print("PASS")
            continue
        for finding in report["findings"]:
            cell = finding["cell"] or "global"
            print(
                f"{finding['severity'].upper()}: cell {cell}: {finding['category']}: {finding['message']}"
            )


def main() -> int:
    args = build_parser().parse_args()
    try:
        limits = limits_from_args(args)
    except ValueError as exc:
        envelope = {
            "ok": False,
            "schema_version": "1.0",
            "tool": "check_notebook_runtime_order",
            "results": [],
            "aggregate": {
                "files": 0,
                "failed_files": 1,
                "findings": 0,
                "failing_findings": 0,
            },
            "error": error_envelope("invalid_limit", exc),
        }
        print(
            json.dumps(envelope, sort_keys=True)
            if args.json
            else "ERROR: invalid_limit"
        )
        return 2
    results: list[dict[str, Any]] = []
    for name in args.notebooks:
        path = Path(name)
        try:
            report = check_notebook(path, limits=limits, repo_root=args.repo_root)
            results.append(
                result_envelope(path, report=report, repo_root=args.repo_root)
            )
        except NotebookInputError as exc:
            results.append(result_envelope(path, error=exc, repo_root=args.repo_root))
        except (MemoryError, RecursionError):
            results.append(
                result_envelope(
                    path,
                    error=NotebookInputError(
                        "resource_error",
                        "resource limit exceeded while processing notebook",
                    ),
                    repo_root=args.repo_root,
                )
            )
        except Exception:
            results.append(
                result_envelope(
                    path,
                    error=NotebookInputError(
                        "internal_error", "notebook processing failed"
                    ),
                    repo_root=args.repo_root,
                )
            )
    envelope = batch_envelope(results, warnings_fail=args.warnings_fail)
    if args.json:
        print(json.dumps(envelope, sort_keys=True))
    else:
        print_text_report(envelope)
    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
