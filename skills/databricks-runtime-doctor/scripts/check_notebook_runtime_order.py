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
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime_safety import SafeArgumentParser, redact_structure, redact_text, safe_error

BUILTIN_NAMES = set(dir(builtins)) | {"display", "spark", "dbutils"}


@dataclass(frozen=True)
class Issue:
    """Notebook runtime-order issue or safe input/read failure."""

    severity: str
    cell: int
    message: str
    error_type: str | None = None


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


def is_pip_cell(source: str) -> bool:
    """Return True when a cell installs packages."""
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    return any(
        line.startswith(("%pip", "!pip", "pip install", "python -m pip"))
        for line in lines
    )


def strip_magic_lines(source: str) -> str:
    """Remove notebook magic and shell lines before AST parsing."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("%", "!"))
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
    cleaned = strip_magic_lines(source).strip()
    if not cleaned:
        return False
    try:
        return contains_restart(ast.parse(cleaned))
    except SyntaxError:
        return False


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
    ) -> None:
        self.known_names = known_names
        self.first_use = first_use
        self.issues = issues
        self.cell = cell
        self.postponed_annotations = postponed_annotations
        self.restart_chain_intact = restart_chain_intact
        self.event = 0
        self.scopes = [_Scope("module", known_names)]

    def analyze_until_restart(self, tree: ast.Module) -> bool:
        """Process a cell until one trustworthy direct restart expression executes."""
        for statement in tree.body:
            if _restart_statement(statement) and self.restart_chain_intact:
                return True
            self._statement(statement)
        return False

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
        self.scopes.append(_Scope("conditional", set()))
        self._statements(statements)
        self.scopes.pop()

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
) -> tuple[ast.AST | None, Issue | None]:
    """Parse a code cell after removing notebook magics without echoing source errors."""
    cleaned = strip_magic_lines(source).strip()
    if not cleaned:
        return None, None
    try:
        return ast.parse(cleaned), None
    except SyntaxError:
        return None, Issue(
            "warning",
            cell_number,
            "could not parse Python after removing notebook magics",
            "SyntaxError",
        )


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
    pip_seen = False
    restart_after_last_pip = True
    import_or_code_seen = False

    for cell_number, source in cells:
        if is_pip_cell(source):
            if import_or_code_seen:
                issues.append(
                    Issue(
                        "error",
                        cell_number,
                        "%pip or pip install appears after imports or executable code",
                    )
                )
            pip_seen = True
            restart_after_last_pip = False
            continue
        tree, parse_issue = parse_python_cell(source, cell_number)
        if parse_issue:
            issues.append(parse_issue)
        if tree is None:
            continue

        if not isinstance(tree, ast.Module):
            issues.append(
                Issue(
                    "warning",
                    cell_number,
                    "could not parse Python after removing notebook magics",
                    "SyntaxError",
                )
            )
            continue
        analyzer = _ExecutionAnalyzer(
            known_names=known_names,
            first_use=first_use,
            issues=issues,
            cell=cell_number,
            postponed_annotations=postponed_annotations,
            restart_chain_intact=restart_chain_intact,
        )
        restarted = analyzer.analyze_until_restart(tree)
        postponed_annotations = analyzer.postponed_annotations
        restart_chain_intact = analyzer.restart_chain_intact

        import_or_code_seen = True
        if restarted:
            if pip_seen:
                restart_after_last_pip = True
            _finalize_unresolved(issues, first_use, known_names)
            known_names = set(BUILTIN_NAMES)
            first_use = {}
            postponed_annotations = False
            restart_chain_intact = True

    if pip_seen and not restart_after_last_pip:
        issues.append(
            Issue(
                "warning",
                0,
                "notebook has %pip or pip install without a later Python restart cell",
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
