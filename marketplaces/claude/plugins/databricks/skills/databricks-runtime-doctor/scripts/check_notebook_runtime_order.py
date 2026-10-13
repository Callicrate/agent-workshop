#!/usr/bin/env python3
"""Conservatively diagnose Databricks notebook runtime ordering.

Restart recognition accepts a direct call only when no supported evident rebind
precedes it. This static diagnostic does not prove referential identity against
arbitrary dynamic mutation, aliases, eval/exec, or side effects.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import re
import sys
import tokenize
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime_safety import SafeArgumentParser, redact_structure, redact_text, safe_error

BUILTIN_NAMES = set(dir(builtins)) | {"display", "spark", "dbutils"}
INSTALL_PREFIX_PATTERNS = (
    ("percent_pip", re.compile(r"^%pip(?:[ \t]|$)")),
    ("bang_pip", re.compile(r"^!pip(?:[ \t]|$)")),
    ("pip_install", re.compile(r"^pip[ \t]+install(?:[ \t]|$)")),
    ("python_pip", re.compile(r"^python[ \t]+-m[ \t]+pip(?:[ \t]|$)")),
)
NON_PYTHON_MAGICS = frozenset({"%r", "%scala", "%sh", "%sql"})
UNSAFE_COMMAND_CHARACTERS = frozenset(";&|<>")


@dataclass(frozen=True)
class Issue:
    """Notebook runtime-order issue or safe input/read failure."""

    severity: str
    cell: int
    message: str
    error_type: str | None = None


@dataclass(frozen=True)
class _DirectFunctionSummary:
    """Bounded module-global load facts for one directly called local function."""

    name: str
    global_loads: frozenset[str]


@dataclass(frozen=True)
class _InstallEvent:
    """One confirmed opaque package-install command at its original source line."""

    line: int
    kind: str


@dataclass(frozen=True)
class _NormalizedCell:
    """One cell's normalized Python source and external lexical evidence."""

    source: str
    install_events: tuple[_InstallEvent, ...]
    placeholder_lines: frozenset[int]
    unsafe_install_lines: frozenset[int]
    non_python: bool
    non_python_install_seen: bool


@dataclass
class _NotebookOrderState:
    """Notebook-level install ordering state that survives Python restarts."""

    pip_seen: bool = False
    restart_after_last_pip: bool = True
    import_or_code_seen: bool = False


def structured_error(error_type: str, message: str) -> list[Issue]:
    """Return the stable cell-zero error record used for unreadable notebooks."""
    return [Issue("error", 0, message, error_type)]


def cell_source(cell: Mapping[str, Any]) -> str:
    """Return a validated notebook cell source as a single string."""
    source = cell.get("source", "")
    if isinstance(source, str):
        return source
    if isinstance(source, list) and all(isinstance(line, str) for line in source):
        return "".join(source)
    raise ValueError("cell source must be a string or list of strings")


def code_cells(notebook: Mapping[str, Any]) -> list[tuple[int, str]]:
    """Return validated code cell numbers and source."""
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook cells must be a list")
    results: list[tuple[int, str]] = []
    for index, cell in enumerate(cells, start=1):
        if not isinstance(cell, Mapping):
            raise ValueError("notebook cells must be objects")
        if cell.get("cell_type") == "code":
            results.append((index, cell_source(cell)))
    return results


def _line_body(line: str) -> str:
    """Return one physical line without changing its other characters."""
    return line.removesuffix("\n").removesuffix("\r")


def _install_prefix(text: str) -> str | None:
    """Return one exact supported install prefix without parsing its arguments."""
    for kind, pattern in INSTALL_PREFIX_PATTERNS:
        if pattern.match(text):
            return kind
    return None


def _placeholder_line(line: str) -> str:
    """Replace one physical command line with a position-preserving pass."""
    body = _line_body(line)
    newline = line[len(body) :]
    indent = body[: len(body) - len(body.lstrip(" \t"))]
    width = len(body) - len(indent)
    return f"{indent}pass{' ' * max(0, width - 4)}{newline}"


def _tokens_with_partial_recovery(source: str) -> list[tokenize.TokenInfo]:
    """Return safe lexical evidence even when the candidate source is incomplete."""
    tokens: list[tokenize.TokenInfo] = []
    generator = tokenize.generate_tokens(io.StringIO(source).readline)
    try:
        while True:
            tokens.append(next(generator))
    except StopIteration:
        return tokens
    except (IndentationError, tokenize.TokenError):
        return tokens


def _confirmed_candidate_lines(
    source: str, candidates: Mapping[int, tuple[str, int]]
) -> tuple[set[int], set[int]]:
    """Separate top-level standalone candidates from inert string contents."""
    confirmed: set[int] = set()
    string_lines: set[int] = set()
    indent_depth = 0
    bracket_depth = 0
    logical_line_start = True
    for token in _tokens_with_partial_recovery(source):
        token_name = tokenize.tok_name[token.type]
        if token.type == tokenize.INDENT:
            indent_depth += 1
            continue
        if token.type == tokenize.DEDENT:
            indent_depth = max(0, indent_depth - 1)
            continue
        if token.type == tokenize.NEWLINE:
            logical_line_start = True
            continue
        if token.type in {tokenize.NL, tokenize.COMMENT, tokenize.ENDMARKER}:
            continue
        if token.type == tokenize.STRING or token_name.startswith("FSTRING"):
            string_lines.update(range(token.start[0], token.end[0] + 1))

        candidate = candidates.get(token.start[0])
        if (
            candidate is not None
            and token.type == tokenize.NAME
            and token.string == "pass"
            and token.start[1] == candidate[1]
            and indent_depth == 0
            and bracket_depth == 0
            and logical_line_start
        ):
            confirmed.add(token.start[0])

        logical_line_start = False
        if token.type == tokenize.OP:
            if token.string in "([{":
                bracket_depth += 1
            elif token.string in ")]}":
                bracket_depth = max(0, bracket_depth - 1)
    return confirmed, string_lines


def _original_cross_line_string_lines(
    source: str, candidate_lines: set[int]
) -> set[int]:
    """Return candidates covered by strings that began on an earlier code line."""
    covered: set[int] = set()
    for token in _tokens_with_partial_recovery(source):
        token_name = tokenize.tok_name[token.type]
        if not (token.type == tokenize.STRING or token_name.startswith("FSTRING")):
            continue
        if token.start[0] in candidate_lines:
            # Opaque quoting on an actual command line cannot hide later commands.
            continue
        covered.update(
            line_number
            for line_number in candidate_lines
            if token.start[0] < line_number <= token.end[0]
        )
    return covered


def normalize_cell(source: str) -> _NormalizedCell:
    """Map supported opaque install lines and normalize one full Python cell."""
    lines = source.splitlines(keepends=True)
    first_nonblank = next(
        (index for index, line in enumerate(lines, start=1) if _line_body(line).strip()),
        None,
    )
    leading_text = (
        _line_body(lines[first_nonblank - 1]).strip()
        if first_nonblank is not None
        else ""
    )
    leading_word = leading_text.split(maxsplit=1)[0] if leading_text else ""
    if leading_word in NON_PYTHON_MAGICS:
        leading_remainder = leading_text[len(leading_word) :].lstrip(" \t")
        install_seen = _install_prefix(leading_remainder) is not None or any(
            _install_prefix(_line_body(line).strip()) is not None
            for line in lines[first_nonblank or 0 :]
        )
        return _NormalizedCell(
            source=source,
            install_events=(),
            placeholder_lines=frozenset(),
            unsafe_install_lines=frozenset(),
            non_python=True,
            non_python_install_seen=install_seen,
        )

    directive_line = (
        first_nonblank
        if first_nonblank is not None
        and re.match(r"^%python(?:[ \t]|$)", _line_body(lines[first_nonblank - 1]))
        else None
    )
    candidates: dict[int, tuple[str, int]] = {}
    unsafe_command_lines: set[int] = set()
    provisional_lines = list(lines)
    if directive_line is not None:
        provisional_lines[directive_line - 1] = _placeholder_line(
            provisional_lines[directive_line - 1]
        )
    for line_number, line in enumerate(lines, start=1):
        body = _line_body(line)
        stripped = body.lstrip(" \t")
        kind = _install_prefix(stripped)
        if kind is None:
            continue
        indent = len(body) - len(stripped)
        candidates[line_number] = (kind, indent)
        if body.rstrip().endswith("\\") or any(
            character in stripped for character in UNSAFE_COMMAND_CHARACTERS
        ):
            unsafe_command_lines.add(line_number)
        provisional_lines[line_number - 1] = _placeholder_line(line)

    confirmed, string_lines = _confirmed_candidate_lines(
        "".join(provisional_lines), candidates
    )
    string_lines.update(
        _original_cross_line_string_lines(source, set(candidates))
    )
    confirmed.difference_update(unsafe_command_lines)
    inert = set(candidates).intersection(string_lines)
    unsafe = set(candidates).difference(confirmed, inert)
    normalized_lines = list(lines)
    placeholder_lines: set[int] = set()
    if directive_line is not None:
        normalized_lines[directive_line - 1] = _placeholder_line(
            normalized_lines[directive_line - 1]
        )
        placeholder_lines.add(directive_line)
    events = tuple(
        _InstallEvent(line_number, candidates[line_number][0])
        for line_number in sorted(confirmed)
    )
    for event in events:
        normalized_lines[event.line - 1] = _placeholder_line(
            normalized_lines[event.line - 1]
        )
        placeholder_lines.add(event.line)
    return _NormalizedCell(
        source="".join(normalized_lines),
        install_events=events,
        placeholder_lines=frozenset(placeholder_lines),
        unsafe_install_lines=frozenset(unsafe),
        non_python=False,
        non_python_install_seen=False,
    )


def target_names(target: ast.AST) -> set[str]:
    """Return names defined directly by an assignment target."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return set().union(*(target_names(element) for element in target.elts))
    return set()


def _target_name_sequence(target: ast.AST) -> list[str]:
    """Return assignment target names in source order."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_name_sequence(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return [
            name for element in target.elts for name in _target_name_sequence(element)
        ]
    return []


def imported_names(statement: ast.stmt) -> set[str]:
    """Return the names made visible by one module-level import statement."""
    if isinstance(statement, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in statement.names}
    if isinstance(statement, ast.ImportFrom):
        return {
            alias.asname or alias.name for alias in statement.names if alias.name != "*"
        }
    return set()


def imports_in_tree(tree: ast.AST) -> set[str]:
    """Collect only imports that bind names in this module's immediate body."""
    body = tree.body if isinstance(tree, ast.Module) else [tree]
    return set().union(
        *(
            imported_names(statement)
            for statement in body
            if isinstance(statement, ast.stmt)
        )
    )


def definitions_in_statement(statement: ast.stmt) -> set[str]:
    """Return names definitely bound by one top-level statement."""
    if isinstance(statement, ast.Import | ast.ImportFrom):
        return imported_names(statement)
    if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {statement.name}
    if isinstance(statement, ast.Assign):
        return set().union(*(target_names(target) for target in statement.targets))
    if isinstance(statement, ast.AugAssign):
        return target_names(statement.target)
    if isinstance(statement, ast.AnnAssign) and statement.value is not None:
        return target_names(statement.target)
    return set()


def definitions_in_tree(tree: ast.AST) -> set[str]:
    """Collect immediate module-body definitions without leaking nested locals."""
    body = tree.body if isinstance(tree, ast.Module) else [tree]
    return set().union(
        *(
            definitions_in_statement(statement)
            for statement in body
            if isinstance(statement, ast.stmt)
        )
    )


def loaded_names_in_tree(tree: ast.AST) -> set[str]:
    """Return direct module-expression loads for compatibility callers."""
    names: set[str] = set()

    class Collector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                names.add(node.id)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

    Collector().visit(tree)
    return names


def _restart_call(node: ast.AST) -> bool:
    """Recognize only an actual ``dbutils.library.restartPython(...)`` call."""
    if (
        not isinstance(node, ast.Call)
        or not isinstance(node.func, ast.Attribute)
        or node.func.attr != "restartPython"
    ):
        return False
    owner = node.func.value
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "library"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "dbutils"
    )


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    """Return a literal dotted-name path without resolving aliases or dynamic state."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return current.id, *reversed(parts)


def _literal_string(node: ast.AST) -> str | None:
    """Return a source-literal string, never evaluating an expression."""
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _reflective_restart_mutation(call: ast.Call) -> bool:
    """Recognize the supported literal setattr/delattr chain replacements."""
    if not isinstance(call.func, ast.Name) or call.func.id not in {
        "setattr",
        "delattr",
    }:
        return False
    if len(call.args) < 2:
        return False
    target_path = _attribute_path(call.args[0])
    attribute = _literal_string(call.args[1])
    return (target_path, attribute) in {
        (("dbutils", "library"), "restartPython"),
        (("dbutils",), "library"),
    }


def _vars_restart_slot(target: ast.AST) -> bool:
    """Recognize the two supported literal ``vars`` chain-replacement targets."""
    if not isinstance(target, ast.Subscript):
        return False
    call = target.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "vars"
        and len(call.args) == 1
    ):
        return False
    return (_attribute_path(call.args[0]), _literal_string(target.slice)) in {
        (("dbutils",), "library"),
        (("dbutils", "library"), "restartPython"),
    }


def _restart_statement(statement: ast.stmt) -> bool:
    """Return whether one direct module statement invokes the restart callable."""
    return isinstance(statement, ast.Expr) and _restart_call(statement.value)


def contains_restart(tree: ast.AST) -> bool:
    """Return whether a direct module expression restarts the Databricks runtime."""
    if not isinstance(tree, ast.Module):
        return False
    return any(_restart_statement(statement) for statement in tree.body)


def is_restart_cell(source: str) -> bool:
    """Return whether parseable Python contains a structural Databricks restart call."""
    normalized = normalize_cell(source)
    if normalized.non_python or normalized.unsafe_install_lines:
        return False
    try:
        return contains_restart(ast.parse(normalized.source))
    except SyntaxError:
        return False


class _FunctionBindingVisitor(ast.NodeVisitor):
    """Collect direct function locals without traversing nested callable scopes."""

    def __init__(self, function: ast.FunctionDef) -> None:
        self.bound = {
            argument.arg
            for argument in [
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            ]
        }
        if function.args.vararg is not None:
            self.bound.add(function.args.vararg.arg)
        if function.args.kwarg is not None:
            self.bound.add(function.args.kwarg.arg)
        self.global_names: set[str] = set()
        self.global_writes: set[str] = set()
        self.nonlocal_seen = False

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        del node
        self.nonlocal_seen = True

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store | ast.Del):
            self.bound.add(node.id)
            self.global_writes.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._comprehension(node.generators, [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._comprehension(node.generators, [node.elt])

    def _comprehension(
        self, generators: list[ast.comprehension], values: list[ast.expr]
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bound.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.bound.add(node.rest)
        self.generic_visit(node)


class _FunctionLoadVisitor(ast.NodeVisitor):
    """Collect nonlocal loads while respecting comprehension and nested scopes."""

    def __init__(self, function_locals: set[str]) -> None:
        self.scopes = [function_locals]
        self.loads: set[str] = set()
        self.dynamic = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and not any(
            node.id in scope for scope in reversed(self.scopes)
        ):
            self.loads.add(node.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Local annotations do not evaluate; values and complex targets still do."""
        if node.value is not None:
            self.visit(node.value)
        self._annotation_target(node.target)

    def _annotation_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Attribute):
            self.visit(target.value)
        elif isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)
        elif isinstance(target, ast.Starred):
            self._annotation_target(target.value)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._annotation_target(element)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {
            "globals",
            "locals",
            "vars",
            "eval",
            "exec",
            "getattr",
        }:
            self.dynamic = True
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._comprehension(node.generators, [node.elt], eager=True)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._comprehension(node.generators, [node.elt], eager=True)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._comprehension(node.generators, [node.key, node.value], eager=True)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if node.generators:
            self.visit(node.generators[0].iter)

    def _comprehension(
        self,
        generators: list[ast.comprehension],
        values: list[ast.expr],
        *,
        eager: bool,
    ) -> None:
        if not generators:
            return
        self.visit(generators[0].iter)
        if not eager:
            return
        scope: set[str] = set()
        self.scopes.append(scope)
        for index, generator in enumerate(generators):
            if index:
                self.visit(generator.iter)
            scope.update(target_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.scopes.pop()


def _direct_function_summary(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
) -> _DirectFunctionSummary | None:
    """Summarize only an undecorated synchronous function with static globals."""
    if not isinstance(statement, ast.FunctionDef) or statement.decorator_list:
        return None
    if any(isinstance(node, ast.Yield | ast.YieldFrom | ast.Await) for node in ast.walk(statement)):
        return None
    bindings = _FunctionBindingVisitor(statement)
    for child in statement.body:
        bindings.visit(child)
    if bindings.nonlocal_seen or bindings.global_names.intersection(bindings.global_writes):
        return None
    loads = _FunctionLoadVisitor(bindings.bound.difference(bindings.global_names))
    for child in statement.body:
        loads.visit(child)
    if loads.dynamic:
        return None
    return _DirectFunctionSummary(
        statement.name,
        frozenset(loads.loads.difference(BUILTIN_NAMES)),
    )


@dataclass
class _Scope:
    """Names visible in one immediately executing lexical scope."""

    kind: str
    known: set[str]


class _ExecutionAnalyzer:
    """Bounded AST interpreter for the ordering facts this checker reports."""

    def __init__(
        self,
        *,
        known_names: set[str],
        first_use: dict[str, tuple[int, int]],
        issues: list[Issue],
        cell: int,
        postponed_annotations: bool,
        restart_chain_intact: bool,
        direct_functions: dict[str, _DirectFunctionSummary],
        order_state: _NotebookOrderState,
    ) -> None:
        self.known_names = known_names
        self.first_use = first_use
        self.issues = issues
        self.cell = cell
        self.postponed_annotations = postponed_annotations
        self.restart_chain_intact = restart_chain_intact
        self.direct_functions = direct_functions
        self.order_state = order_state
        self.event = 0
        self.scopes = [_Scope("module", known_names)]

    def analyze_until_restart(
        self,
        tree: ast.Module,
        install_events: tuple[_InstallEvent, ...],
        placeholder_lines: frozenset[int],
    ) -> bool:
        """Merge real statements and opaque installs until a trusted restart."""
        ordered_events: list[tuple[int, int, ast.stmt | _InstallEvent]] = [
            (event.line, 0, event) for event in install_events
        ]
        ordered_events.extend(
            (statement.lineno, index + 1, statement)
            for index, statement in enumerate(tree.body)
            if not (
                isinstance(statement, ast.Pass)
                and statement.lineno in placeholder_lines
            )
        )
        ordered_events.sort(key=lambda event: (event[0], event[1]))
        for _, _, event in ordered_events:
            if isinstance(event, _InstallEvent):
                self._install_event()
                continue
            statement = event
            self.order_state.import_or_code_seen = True
            if _restart_statement(statement) and self.restart_chain_intact:
                if self.order_state.pip_seen:
                    self.order_state.restart_after_last_pip = True
                return True
            self._statement(statement)
        return False

    def _install_event(self) -> None:
        """Apply one confirmed install without interpreting its opaque arguments."""
        self._advance()
        if self.order_state.import_or_code_seen:
            self.issues.append(
                Issue(
                    "error",
                    self.cell,
                    "%pip or pip install appears after imports or executable code",
                )
            )
        self.order_state.pip_seen = True
        self.order_state.restart_after_last_pip = False

    def _advance(self) -> tuple[int, int]:
        self.event += 1
        return self.cell, self.event

    def _current_scope(self) -> _Scope:
        return self.scopes[-1]

    def _binding_scope(self) -> _Scope:
        return next(
            scope for scope in reversed(self.scopes) if scope.kind != "comprehension"
        )

    def _known(self, name: str) -> bool:
        return any(name in scope.known for scope in reversed(self.scopes))

    def _use(self, name: str) -> None:
        position = self._advance()
        if not self._known(name):
            self.first_use.setdefault(name, position)

    def _define(
        self, name: str, *, imported: bool = False, scope: _Scope | None = None
    ) -> None:
        position = self._advance()
        target_scope = scope or self._binding_scope()
        if name == "dbutils" and target_scope.kind in {"module", "conditional"}:
            self.restart_chain_intact = False
        if target_scope.kind != "module":
            target_scope.known.add(name)
            return
        self.direct_functions.pop(name, None)
        first = self.first_use.get(name)
        if first is not None and first < position:
            message = (
                f"import for {name!r} appears after first use in cell {first[0]}"
                if imported
                else f"definition for {name!r} appears after first use in cell {first[0]}"
            )
            self.issues.append(Issue("error", self.cell, message))
        target_scope.known.add(name)

    def _define_target(self, target: ast.AST, *, scope: _Scope | None = None) -> None:
        for name in _target_name_sequence(target):
            self._define(name, scope=scope)

    def _delete_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._advance()
            target_scope = self._binding_scope()
            if target.id == "dbutils" and target_scope.kind in {
                "module",
                "conditional",
            }:
                self.restart_chain_intact = False
            target_scope.known.discard(target.id)
            self.direct_functions.pop(target.id, None)
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
        """Invalidate restart recognition after plain mutable chain replacement."""
        if isinstance(target, ast.Starred):
            self._note_restart_mutation_target(target.value)
            return
        if isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._note_restart_mutation_target(element)
            return
        path = _attribute_path(target)
        if path in {
            ("dbutils", "library"),
            ("dbutils", "library", "restartPython"),
        } or _vars_restart_slot(target):
            self.restart_chain_intact = False

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
            for alias in statement.names:
                self._define(alias.asname or alias.name.split(".")[0], imported=True)
            return
        if isinstance(statement, ast.ImportFrom):
            if statement.module == "__future__":
                if any(alias.name == "annotations" for alias in statement.names):
                    self.postponed_annotations = True
                return
            for alias in statement.names:
                if alias.name != "*":
                    self._define(alias.asname or alias.name, imported=True)
            return
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            self._function(statement)
            return
        if isinstance(statement, ast.ClassDef):
            self._class(statement)
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
            if not self.postponed_annotations:
                self._expression(statement.annotation)
            return
        if isinstance(statement, ast.AugAssign):
            self._target_location(statement.target)
            self._note_restart_mutation_target(statement.target)
            for name in _target_name_sequence(statement.target):
                self._use(name)
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
            self.scopes.append(_Scope("conditional", set()))
            self._note_restart_mutation_target(statement.target)
            self._define_target(statement.target)
            self._statements(statement.body)
            self.scopes.pop()
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
                self.scopes.append(_Scope("conditional", set()))
                if handler.name:
                    self._define(handler.name)
                self._statements(handler.body)
                self.scopes.pop()
            self._conditional_statements(statement.orelse)
            self._statements(statement.finalbody)
            return
        if isinstance(statement, ast.Match):
            self._expression(statement.subject)
            for case in statement.cases:
                self.scopes.append(_Scope("conditional", set()))
                self._define_pattern(case.pattern)
                if case.guard is not None:
                    self._expression(case.guard)
                self._statements(case.body)
                self.scopes.pop()
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

    def _conditional_statements(self, statements: list[ast.stmt]) -> None:
        """Run a possibly skipped block without exporting its bindings as definite."""
        scope = _Scope("conditional", set())
        self.scopes.append(scope)
        self._statements(statements)
        self.scopes.pop()
        for name in scope.known:
            self.direct_functions.pop(name, None)

    def _function(self, statement: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in statement.decorator_list:
            self._expression(decorator)
        for default in statement.args.defaults:
            self._expression(default)
        for default in statement.args.kw_defaults:
            if default is not None:
                self._expression(default)
        if not self.postponed_annotations:
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
        self._define(statement.name)
        if self._current_scope().kind == "module":
            summary = _direct_function_summary(statement)
            if summary is not None:
                self.direct_functions[statement.name] = summary

    def _class(self, statement: ast.ClassDef) -> None:
        for decorator in statement.decorator_list:
            self._expression(decorator)
        for base in statement.bases:
            self._expression(base)
        for keyword in statement.keywords:
            self._expression(keyword.value)
        parent = self._current_scope()
        self.scopes.append(_Scope("class", set()))
        self._statements(statement.body)
        self.scopes.pop()
        self._define(statement.name, scope=parent)

    def _expression(self, expression: ast.AST) -> None:
        if isinstance(expression, ast.Call) and _reflective_restart_mutation(
            expression
        ):
            self.restart_chain_intact = False
        if isinstance(expression, ast.Call):
            self._expression(expression.func)
            for argument in expression.args:
                self._expression(argument)
            for keyword in expression.keywords:
                self._expression(keyword.value)
            self._direct_function_call(expression)
            return
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
        if isinstance(expression, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            self._comprehension(expression.generators, [expression.elt])
            return
        if isinstance(expression, ast.DictComp):
            self._comprehension(
                expression.generators, [expression.key, expression.value]
            )
            return
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                self._expression(child)
            elif isinstance(child, ast.keyword):
                self._expression(child.value)

    def _direct_function_call(self, expression: ast.Call) -> None:
        """Warn only for a direct module-level call to a current local summary."""
        if self._current_scope().kind != "module" or not isinstance(expression.func, ast.Name):
            return
        summary = self.direct_functions.get(expression.func.id)
        if summary is None:
            return
        for name in sorted(summary.global_loads):
            if name not in self.known_names:
                self.issues.append(
                    Issue(
                        "warning",
                        self.cell,
                        f"direct call to {summary.name!r} may load global {name!r} before a visible import or definition",
                    )
                )

    def _comprehension(
        self, generators: list[ast.comprehension], values: list[ast.expr]
    ) -> None:
        if not generators:
            return
        self._expression(generators[0].iter)
        self.scopes.append(_Scope("conditional", set()))
        self.scopes.append(_Scope("comprehension", set()))
        for index, generator in enumerate(generators):
            if index:
                self._expression(generator.iter)
            self._define_target(generator.target, scope=self._current_scope())
            for condition in generator.ifs:
                self._expression(condition)
        for value in values:
            self._expression(value)
        self.scopes.pop()
        self.scopes.pop()

    def _define_pattern(self, pattern: ast.pattern) -> None:
        if isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                self._define_pattern(pattern.pattern)
            if pattern.name is not None:
                self._define(pattern.name)
            return
        if isinstance(pattern, ast.MatchStar):
            if pattern.name is not None:
                self._define(pattern.name)
            return
        if isinstance(pattern, ast.MatchMapping):
            for key in pattern.keys:
                self._expression(key)
            for child in pattern.patterns:
                self._define_pattern(child)
            if pattern.rest is not None:
                self._define(pattern.rest)
            return
        if isinstance(pattern, ast.MatchClass):
            self._expression(pattern.cls)
            for child in [*pattern.patterns, *pattern.kwd_patterns]:
                self._define_pattern(child)
            return
        if isinstance(pattern, ast.MatchSequence | ast.MatchOr):
            for child in pattern.patterns:
                self._define_pattern(child)


def parse_python_cell(
    source: str, cell_number: int
) -> tuple[ast.Module | None, Issue | None]:
    """Parse one entire normalized cell without echoing source errors."""
    try:
        return ast.parse(source), None
    except SyntaxError:
        return None, Issue(
            "warning",
            cell_number,
            "could not parse Python after removing notebook magics",
            "SyntaxError",
        )


def _record_pending_install(
    state: _NotebookOrderState, issues: list[Issue], cell_number: int
) -> None:
    """Keep install evidence pending when ordered Python analysis is unsafe."""
    if state.import_or_code_seen:
        issues.append(
            Issue(
                "error",
                cell_number,
                "%pip or pip install appears after imports or executable code",
            )
        )
    state.pip_seen = True
    state.restart_after_last_pip = False


def load_notebook(path: Path) -> tuple[Mapping[str, Any] | None, list[Issue] | None]:
    """Load and validate a notebook, recovering malformed input as one cell-zero error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, structured_error(
            "FileNotFoundError", "notebook file was not found"
        )
    except UnicodeDecodeError:
        return None, structured_error(
            "UnicodeDecodeError", "notebook is not valid UTF-8"
        )
    except OSError as exc:
        return None, structured_error(
            safe_error("notebook read", exc)["error_type"], "notebook could not be read"
        )
    try:
        notebook = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None, structured_error("JSONDecodeError", "notebook is not valid JSON")
    if not isinstance(notebook, Mapping) or not isinstance(notebook.get("cells"), list):
        return None, structured_error(
            "NotebookSchemaError", "notebook must be an object with a cells list"
        )
    return notebook, None


def _finalize_unresolved(
    issues: list[Issue],
    first_use: dict[str, tuple[int, int]],
    known_names: set[str],
) -> None:
    """Emit unresolved names for one kernel segment before it ends or restarts."""
    for name, (cell_number, _) in sorted(first_use.items()):
        if name not in known_names:
            issues.append(
                Issue(
                    "warning",
                    cell_number,
                    f"symbol {name!r} is used before a visible import or definition",
                )
            )


def check_notebook(path: Path) -> list[Issue]:
    """Return runtime-order findings; all input failures are structured errors."""
    notebook, load_issues = load_notebook(path)
    if load_issues is not None:
        return load_issues
    if notebook is None:
        return structured_error(
            "NotebookSchemaError", "notebook could not be validated"
        )
    try:
        cells = code_cells(notebook)
    except ValueError:
        return structured_error(
            "NotebookSchemaError", "notebook cells have an unsupported schema"
        )

    issues: list[Issue] = []
    known_names = set(BUILTIN_NAMES)
    first_use: dict[str, tuple[int, int]] = {}
    postponed_annotations = False
    restart_chain_intact = True
    direct_functions: dict[str, _DirectFunctionSummary] = {}
    order_state = _NotebookOrderState()

    for cell_number, source in cells:
        normalized = normalize_cell(source)
        if normalized.non_python:
            if normalized.non_python_install_seen:
                issues.append(
                    Issue(
                        "warning",
                        cell_number,
                        "could not safely analyze package install ordering in a non-Python notebook cell",
                        "UnsupportedCellLanguage",
                    )
                )
                _record_pending_install(order_state, issues, cell_number)
            continue

        tree, parse_issue = parse_python_cell(normalized.source, cell_number)
        if tree is None:
            if normalized.unsafe_install_lines:
                issues.append(
                    Issue(
                        "warning",
                        cell_number,
                        "could not safely analyze mixed package-install and Python ordering",
                        "UnsupportedCellSyntax",
                    )
                )
            elif parse_issue is not None:
                issues.append(parse_issue)
            if normalized.install_events or normalized.unsafe_install_lines:
                _record_pending_install(order_state, issues, cell_number)
            continue
        if normalized.unsafe_install_lines:
            issues.append(
                Issue(
                    "warning",
                    cell_number,
                    "could not safely analyze mixed package-install and Python ordering",
                    "UnsupportedCellSyntax",
                )
            )
            _record_pending_install(order_state, issues, cell_number)
            continue
        analyzer = _ExecutionAnalyzer(
            known_names=known_names,
            first_use=first_use,
            issues=issues,
            cell=cell_number,
            postponed_annotations=postponed_annotations,
            restart_chain_intact=restart_chain_intact,
            direct_functions=direct_functions,
            order_state=order_state,
        )
        restarted = analyzer.analyze_until_restart(
            tree,
            normalized.install_events,
            normalized.placeholder_lines,
        )
        postponed_annotations = analyzer.postponed_annotations
        restart_chain_intact = analyzer.restart_chain_intact

        if restarted:
            _finalize_unresolved(issues, first_use, known_names)
            known_names = set(BUILTIN_NAMES)
            first_use = {}
            postponed_annotations = False
            restart_chain_intact = True
            direct_functions = {}

    if order_state.pip_seen and not order_state.restart_after_last_pip:
        issues.append(
            Issue(
                "warning",
                0,
                "notebook has %pip or pip install without a later Python restart",
            )
        )
    _finalize_unresolved(issues, first_use, known_names)
    return issues


def build_parser() -> argparse.ArgumentParser:
    """Build the notebook runtime-order CLI parser."""
    parser = SafeArgumentParser(
        description="Check Databricks notebook runtime ordering"
    )
    parser.add_argument("notebooks", nargs="+", help="Notebook .ipynb files to check")
    parser.add_argument("--json", action="store_true", help="Output JSON findings")
    parser.add_argument(
        "--warnings-fail", action="store_true", help="Treat warnings as failures"
    )
    return parser


def main() -> int:
    """Run notebook runtime-order checks with valid output for every input error."""
    args = build_parser().parse_args()
    all_results: dict[str, list[dict[str, Any]]] = {}
    failed = False
    for notebook in args.notebooks:
        path = Path(notebook)
        issues = check_notebook(path)
        all_results[redact_text(path)] = [
            redact_structure(asdict(issue)) for issue in issues
        ]
        failed = failed or any(issue.severity == "error" for issue in issues)
        failed = failed or (
            args.warnings_fail and any(issue.severity == "warning" for issue in issues)
        )

    if args.json:
        sys.stdout.write(
            json.dumps(redact_structure(all_results), indent=2, sort_keys=True) + "\n"
        )
    else:
        for notebook, issues in redact_structure(all_results).items():
            sys.stdout.write(f"\n== {notebook} ==\n")
            if not issues:
                sys.stdout.write("PASS\n")
                continue
            for issue in issues:
                cell = issue["cell"] or "global"
                sys.stdout.write(
                    f"{issue['severity'].upper()}: cell {cell}: {issue['message']}\n"
                )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
