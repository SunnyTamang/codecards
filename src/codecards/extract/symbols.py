"""Pass 1 - read every file and record what exists.

Nothing is resolved here. The point is to finish knowing every definition,
every import alias and every class base, so pass 2 can resolve call sites
against a complete picture. This is why forward references and circular
imports are not problems.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from ..graph.model import Location, NodeKind
from .discovery import SkippedFile

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Definition:
    qualname: str
    kind: NodeKind
    name: str
    parent: str | None
    location: Location
    signature: str
    summary: str | None
    decorators: tuple[str, ...]
    bases: tuple[str, ...] = ()  # raw dotted base expressions, classes only
    is_async: bool = False


@dataclass
class ModuleInfo:
    module_id: str
    path: Path
    rel_path: str
    aliases: dict[str, str] = field(default_factory=dict)
    main_block_calls: tuple[str, ...] = ()
    source: str = ""


@dataclass
class SymbolTable:
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    definitions: dict[str, Definition] = field(default_factory=dict)
    by_simple_name: dict[str, list[str]] = field(default_factory=dict)
    packages: set[str] = field(default_factory=set)

    def is_internal(self, qualname: str) -> bool:
        if qualname in self.definitions or qualname in self.modules:
            return True
        if qualname in self.packages:
            return True
        return any(qualname.startswith(p + ".") for p in self.modules)

    def mro(self, class_qualname: str) -> list[str]:
        """Depth-first, left-to-right, deduplicated. Good enough to own a name."""
        order: list[str] = []
        stack = [class_qualname]
        while stack:
            current = stack.pop(0)
            if current in order:
                continue
            definition = self.definitions.get(current)
            if definition is None or definition.kind is not NodeKind.CLASS:
                continue
            order.append(current)
            resolved_bases = [
                self._resolve_base(current, base) for base in definition.bases
            ]
            stack = [b for b in resolved_bases if b] + stack
        return order

    def _resolve_base(self, class_qualname: str, base: str) -> str | None:
        module_id = self.definitions[class_qualname].qualname.rsplit(".", 1)[0]
        while module_id and module_id not in self.modules:
            module_id = module_id.rsplit(".", 1)[0] if "." in module_id else ""
        if not module_id:
            return None
        # Check same-module candidate first (it takes priority)
        candidate = f"{module_id}.{base}"
        if candidate in self.definitions:
            return candidate
        # If no same-module definition, try aliases
        head, _, rest = base.partition(".")
        aliases = self.modules[module_id].aliases
        if head in aliases:
            target = aliases[head]
            full_target = f"{target}.{rest}" if rest else target
            # Only return if the target actually exists in definitions
            if full_target in self.definitions:
                return full_target
        return None


def module_id_for(path: Path, root: Path) -> str:
    rel = path.resolve().relative_to(Path(root).resolve())
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


def _summary(node: ast.AST) -> str | None:
    """The first line of the docstring, or None if there is nothing to show.

    A docstring can be entirely whitespace. `ast.get_docstring` returns that
    as a non-empty string which strips to nothing, so guarding only on
    falsiness leaves an empty `splitlines()` to index into. Found on sympy,
    where it aborted the whole run rather than costing one file its summary.
    """
    doc = ast.get_docstring(node)
    if not doc:
        return None
    stripped = doc.strip()
    if not stripped:
        return None
    return stripped.splitlines()[0].strip() or None


def _root_for(path: Path, roots: list[Path]) -> Path:
    resolved = path.resolve()
    candidates = [r for r in roots if _is_within(resolved, Path(r).resolve())]
    return max(candidates, key=lambda r: len(str(r))) if candidates else Path(path).parent


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def build_symbol_table(
    files: list[Path], roots: list[Path]
) -> tuple[SymbolTable, list[SkippedFile]]:
    table = SymbolTable()
    skipped: list[SkippedFile] = []

    for path in files:
        root = _root_for(path, roots)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(SkippedFile(str(path), "encoding error: not valid UTF-8"))
            continue
        except OSError as exc:
            skipped.append(SkippedFile(str(path), f"unreadable: {exc.strerror}"))
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            skipped.append(SkippedFile(str(path), f"syntax error: line {exc.lineno}"))
            continue
        except RecursionError:
            # A long chained expression exhausts the stack inside ast.parse.
            # Without this the exception escapes build_symbol_table and kills
            # the whole run, losing every healthy file alongside it.
            skipped.append(SkippedFile(str(path), "too deeply nested to parse"))
            continue

        module_id = module_id_for(path, root)
        rel_path = path.resolve().relative_to(Path(root).resolve()).as_posix()
        info = ModuleInfo(module_id=module_id, path=path, rel_path=rel_path, source=source)
        is_package = path.name == "__init__.py"

        try:
            aliases = _collect_aliases(tree, module_id, info, is_package)
            main_calls = _main_block_calls(tree)
            definitions = _collect_definitions_for(tree, module_id, rel_path)
        except RecursionError:
            # The collectors recurse over the same tree, so a file can parse
            # and still exhaust the stack here. Nothing has been written to
            # `table` yet, so skipping leaves no half-built module behind.
            skipped.append(SkippedFile(str(path), "too deeply nested to analyse"))
            continue

        table.modules[module_id] = info
        if is_package:
            table.packages.add(module_id)
        for i in range(1, module_id.count(".") + 1):
            table.packages.add(module_id.rsplit(".", i)[0])
        table.definitions.update(definitions)
        info.main_block_calls = main_calls
        _ = aliases

    for qualname in table.definitions:
        table.by_simple_name.setdefault(qualname.rsplit(".", 1)[-1], []).append(qualname)

    return table, skipped


def _collect_aliases(tree: ast.Module, module_id: str, info: ModuleInfo, is_package: bool = False) -> None:
    package = module_id.rsplit(".", 1)[0] if "." in module_id else ""
    # Collect module-level imports first (they win)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else alias.name.split(".")[0]
                info.aliases[local] = target
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = module_id.split(".")
                # For a package, module_id already IS the package, so trim (level - 1)
                # For a module, trim level
                adjustment = node.level - 1 if is_package else node.level
                trimmed = parts[: max(0, len(parts) - adjustment)]
                base = ".".join([p for p in trimmed if p] + ([base] if base else []))
                if not base:
                    # Over-deep relative import that goes past the root - drop this alias
                    base = None
            elif not base:
                base = package
            if base is not None:
                for alias in node.names:
                    local = alias.asname or alias.name
                    info.aliases[local] = f"{base}.{alias.name}" if base else alias.name

    # Collect nested imports only with setdefault (don't clobber module-level)
    def visit_nested(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    info.aliases.setdefault(local, target)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = module_id.split(".")
                    adjustment = node.level - 1 if is_package else node.level
                    trimmed = parts[: max(0, len(parts) - adjustment)]
                    base = ".".join([p for p in trimmed if p] + ([base] if base else []))
                    if not base:
                        base = None
                elif not base:
                    base = package
                if base is not None:
                    for alias in node.names:
                        local = alias.asname or alias.name
                        info.aliases.setdefault(local, f"{base}.{alias.name}" if base else alias.name)
            # Recurse into nested blocks
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.With, ast.Try)):
                if isinstance(node, ast.ClassDef):
                    visit_nested(node.body)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit_nested(node.body)
                elif isinstance(node, ast.If):
                    visit_nested(node.body)
                    visit_nested(node.orelse)
                elif isinstance(node, ast.With):
                    visit_nested(node.body)
                elif isinstance(node, ast.Try):
                    visit_nested(node.body)
                    for handler in node.handlers:
                        visit_nested(handler.body)
                    visit_nested(node.orelse)
                    visit_nested(node.finalbody)

    visit_nested(tree.body)


def _main_block_calls(tree: ast.Module) -> tuple[str, ...]:
    calls: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
        ):
            continue
        # Check that the comparator is the string "__main__"
        comparator = test.comparators[0]
        if not (isinstance(comparator, ast.Constant) and comparator.value == "__main__"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = _dotted(inner.func)
                if name:
                    calls.append(name)
    return tuple(calls)


def _collect_definitions_for(
    tree: ast.Module, module_id: str, rel_path: str
) -> dict[str, Definition]:
    definitions: dict[str, Definition] = {}

    def visit(body: list[ast.stmt], parent: str, inside_class: bool) -> None:
        for node in body:
            if isinstance(node, _FUNCTION_NODES):
                qualname = f"{parent}.{node.name}"
                definitions[qualname] = Definition(
                    qualname=qualname,
                    kind=NodeKind.METHOD if inside_class else NodeKind.FUNCTION,
                    name=node.name,
                    parent=parent,
                    location=Location(rel_path, node.lineno, _end_line(node)),
                    signature=_signature(node),
                    summary=_summary(node),
                    decorators=tuple(
                        d for d in (_dotted(x) for x in node.decorator_list) if d
                    ),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                )
                visit(node.body, qualname, inside_class=False)
            elif isinstance(node, ast.ClassDef):
                qualname = f"{parent}.{node.name}"
                definitions[qualname] = Definition(
                    qualname=qualname,
                    kind=NodeKind.CLASS,
                    name=node.name,
                    parent=parent,
                    location=Location(rel_path, node.lineno, _end_line(node)),
                    signature="",
                    summary=_summary(node),
                    decorators=tuple(
                        d for d in (_dotted(x) for x in node.decorator_list) if d
                    ),
                    bases=tuple(b for b in (_dotted(x) for x in node.bases) if b),
                )
                visit(node.body, qualname, inside_class=True)

    visit(tree.body, module_id, inside_class=False)
    return definitions


def _end_line(node: ast.AST) -> int:
    return getattr(node, "end_lineno", None) or getattr(node, "lineno", 1)


def _signature(node: ast.AST) -> str:
    try:
        return f"({ast.unparse(node.args)})"
    except Exception:  # pragma: no cover - unparse is reliable on 3.10+
        return "(...)"
