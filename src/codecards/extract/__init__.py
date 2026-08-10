"""Turn a set of paths into a validated, language-neutral CodeGraph."""

from __future__ import annotations

import re
from pathlib import Path

from ..graph.model import (
    CodeGraph,
    Confidence,
    EntryHint,
    EntryReason,
    Location,
    Node,
    NodeKind,
    validate,
)
from ..report import AnalysisReport
from .calls import resolve_calls, to_edges
from .discovery import SkippedFile, find_python_files
from .highlight import prepare
from .symbols import SymbolTable, build_symbol_table

#: Synthetic container that groups stdlib and third-party leaf nodes.
EXTERNAL_ROOT_ID = "<external>"

__all__ = ["EXTERNAL_ROOT_ID", "analyze"]


def _import_roots(roots: list[Path]) -> list[Path]:
    """Where module names should be measured from.

    Pointing at a package directory is the normal way to use this tool
    (`codecards src/codecards`), but the package directory is not the import
    root: Python puts its PARENT on the path and imports `codecards.cli`.
    Measuring from the directory itself named that module `cli`, and named the
    package's own `__init__.py` the empty string, which reached the canvas as a
    blank unnamed card.

    Discovery still walks only the paths the user asked for. This affects
    naming alone.
    """
    resolved = []
    for given in roots:
        root = Path(given)
        base = root if root.is_dir() else root.parent
        while (base / "__init__.py").is_file() and base.parent != base:
            base = base.parent
        resolved.append(base)
    return resolved


def analyze(
    roots: list[Path],
    excludes: tuple[str, ...] | list[str] = (),
    include_external: bool = False,
    embed_source: bool = True,
) -> tuple[CodeGraph, AnalysisReport]:
    roots = [Path(r) for r in roots]
    files, skipped = find_python_files(roots, excludes)
    table, parse_skipped = build_symbol_table(files, _import_roots(roots))

    # Pass 2 reports its own skips through an out-parameter so a function that
    # blows the recursion limit is visible in the summary rather than silently
    # contributing no edges.
    resolve_skipped: list[SkippedFile] = []
    calls = resolve_calls(table, resolve_skipped)
    all_edges = to_edges(calls)
    skipped = skipped + parse_skipped + resolve_skipped

    graph = CodeGraph()
    _add_containers(graph, table)
    _add_definitions(graph, table, embed_source)

    kept = []
    for edge in all_edges:
        if edge.confidence is Confidence.EXTERNAL:
            if not include_external:
                continue
            _add_external_node(graph, edge.target)
        if edge.source in graph.nodes and edge.target in graph.nodes:
            kept.append(edge)
    graph.edges = kept

    graph.entry_hints = _entry_hints(graph, table, roots)
    validate(graph)

    by_confidence: dict[str, int] = {}
    for call in calls:
        by_confidence[call.confidence.value] = by_confidence.get(call.confidence.value, 0) + 1

    report = AnalysisReport(
        total_calls=len(calls),
        by_confidence=by_confidence,
        skipped=skipped,
        node_count=len(graph.nodes),
        callable_count=len(graph.callables()),
        edge_count=len(graph.edges),
    )
    return graph, report


def _add_containers(graph: CodeGraph, table: SymbolTable) -> None:
    for package_id in sorted(table.packages):
        if package_id in table.modules:
            continue
        graph.nodes[package_id] = Node(
            id=package_id,
            kind=NodeKind.PACKAGE,
            name=package_id.rsplit(".", 1)[-1],
            parent=_parent_of(package_id),
        )
    for module_id, info in sorted(table.modules.items()):
        is_package = info.path.name == "__init__.py"
        graph.nodes[module_id] = Node(
            id=module_id,
            kind=NodeKind.PACKAGE if is_package else NodeKind.MODULE,
            name=module_id.rsplit(".", 1)[-1],
            parent=_parent_of(module_id),
            location=Location(info.rel_path, 1, max(1, info.source.count("\n") + 1)),
        )
    # Any parent referenced but not created (defensive, keeps validate() happy).
    for node in list(graph.nodes.values()):
        current = node.parent
        while current and current not in graph.nodes:
            graph.nodes[current] = Node(
                id=current,
                kind=NodeKind.PACKAGE,
                name=current.rsplit(".", 1)[-1],
                parent=_parent_of(current),
            )
            current = _parent_of(current)


def _parent_of(node_id: str) -> str | None:
    return node_id.rsplit(".", 1)[0] if "." in node_id else None


def _add_definitions(graph: CodeGraph, table: SymbolTable, embed_source: bool) -> None:
    for qualname, definition in sorted(table.definitions.items()):
        source = None
        tokens = None
        truncated = False
        if embed_source:
            module_id = _owning_module(qualname, table)
            if module_id:
                source, tokens, truncated = prepare(
                    table.modules[module_id].source,
                    definition.location.line_start,
                    definition.location.line_end,
                )
        graph.nodes[qualname] = Node(
            id=qualname,
            kind=definition.kind,
            name=definition.name,
            parent=definition.parent,
            location=definition.location,
            signature=definition.signature or None,
            summary=definition.summary,
            decorators=definition.decorators,
            source=source,
            source_tokens=tokens,
            source_truncated=truncated,
        )


def _owning_module(qualname: str, table: SymbolTable) -> str | None:
    current = qualname
    while "." in current:
        current = current.rsplit(".", 1)[0]
        if current in table.modules:
            return current
    return current if current in table.modules else None


def _add_external_node(graph: CodeGraph, qualname: str) -> None:
    if EXTERNAL_ROOT_ID not in graph.nodes:
        graph.nodes[EXTERNAL_ROOT_ID] = Node(
            id=EXTERNAL_ROOT_ID, kind=NodeKind.PACKAGE, name="external"
        )
    if qualname not in graph.nodes:
        graph.nodes[qualname] = Node(
            id=qualname,
            kind=NodeKind.FUNCTION,
            name=qualname,
            parent=EXTERNAL_ROOT_ID,
        )


def _entry_hints(graph: CodeGraph, table: SymbolTable, roots: list[Path]) -> list[EntryHint]:
    hints: list[EntryHint] = []

    for module_id, info in sorted(table.modules.items()):
        for raw_name in info.main_block_calls:
            for candidate in (f"{module_id}.{raw_name}",
                              info.aliases.get(raw_name.split(".")[0], "")):
                if candidate in graph.nodes:
                    hints.append(EntryHint(candidate, EntryReason.MAIN_BLOCK))
                    break

    for target in _console_scripts(roots):
        if target in graph.nodes:
            hints.append(EntryHint(target, EntryReason.CONSOLE_SCRIPT))

    for qualname, definition in sorted(table.definitions.items()):
        if definition.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
            continue
        for decorator in definition.decorators:
            if _is_behaviour_decorator(decorator):
                continue
            if not _decorator_is_internal(decorator, qualname, table):
                hints.append(EntryHint(qualname, EntryReason.DECORATED))
                break
        if definition.name.startswith("test_") and _looks_like_a_test_file(
            definition.location.file
        ):
            hints.append(EntryHint(qualname, EntryReason.TEST))

    return hints


#: Decorators that describe how a callable behaves, not who calls it.
#:
#: The DECORATED heuristic exists to catch framework entry points such as
#: @app.route, @click.command and @celery.task, which share the shape of
#: resolving outside the codebase. Language and stdlib decorators share that
#: shape too, and a property is not a door into a program. Measured before
#: this list existed: @property alone accounted for 84 of email's 101
#: detected entry points, and 82% to 100% of every corpus tried was noise.
BEHAVIOUR_DECORATORS = frozenset({
    "property", "staticmethod", "classmethod",
    "setter", "getter", "deleter",
    "abstractmethod", "abstractproperty",
    "cached_property", "cache", "lru_cache", "wraps",
    "singledispatch", "singledispatchmethod", "total_ordering",
    "overload", "final", "no_type_check", "runtime_checkable",
    "dataclass", "contextmanager", "asynccontextmanager",
})


def _is_behaviour_decorator(decorator: str) -> bool:
    """Match on the final segment, so `functools.cached_property`,
    `abc.abstractmethod` and a bare `property` are all caught, as is the
    `@name.setter` form where the head is a local property object."""
    return decorator.rsplit(".", 1)[-1] in BEHAVIOUR_DECORATORS


def _decorator_is_internal(decorator: str, qualname: str, table: SymbolTable) -> bool:
    module_id = _owning_module(qualname, table)
    if module_id is None:
        return False
    aliases = table.modules[module_id].aliases
    head, _, rest = decorator.partition(".")
    if head in aliases:
        target = f"{aliases[head]}.{rest}" if rest else aliases[head]
        return table.is_internal(target)
    return f"{module_id}.{decorator}" in table.definitions


def _looks_like_a_test_file(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or "tests/" in rel_path
        or rel_path.startswith("test/")
    )


_SCRIPT_LINE = re.compile(r"""^\s*[\w.-]+\s*=\s*["']([\w.]+):([\w.]+)["']""")


def _console_scripts(roots: list[Path]) -> list[str]:
    targets: list[str] = []
    for root in roots:
        pyproject = Path(root) / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            import tomllib  # noqa: PLC0415 - 3.11+ only, guarded by ImportError
        except ImportError:
            targets += _scan_scripts_section(text)
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            targets += _scan_scripts_section(text)
            continue
        scripts = data.get("project", {}).get("scripts", {})
        for value in scripts.values():
            module, _, attribute = str(value).partition(":")
            if module and attribute:
                targets.append(f"{module}.{attribute}")
    return targets


def _scan_scripts_section(text: str) -> list[str]:
    """Minimal [project.scripts] reader for Python 3.10, which lacks tomllib."""
    targets: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == "[project.scripts]"
            continue
        if not inside:
            continue
        match = _SCRIPT_LINE.match(line)
        if match:
            targets.append(f"{match.group(1)}.{match.group(2)}")
    return targets
