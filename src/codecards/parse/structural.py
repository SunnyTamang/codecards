"""A graph built from structure alone, with no index to resolve against.

This is what a language gets before anyone has run an indexer: tree-sitter says
what is defined and where the calls are, and a name match is the only thing
available to connect them. That is a guess, so nothing here is ever RESOLVED.
One definition of a name is INFERRED; several are AMBIGUOUS and carry every
candidate; none is UNRESOLVED and draws nothing.

The point is not accuracy - the indexed path is strictly better and the Python
resolver knows far more. The point is that pointing this tool at a repository
should show you something, and "no Python files found" is a poor answer to a
Go project. An index then upgrades the picture instead of being the price of
seeing one at all.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from ..graph.model import (
    CALLABLE_KINDS,
    CallSite,
    CodeGraph,
    Confidence,
    Edge,
    EntryHint,
    EntryReason,
    Location,
    Node,
    NodeKind,
    validate,
)
from ..report import AnalysisReport
from . import grammars, syntax
from .grammars import Grammar

MAX_SOURCE_LINES = 400

#: Directories that hold somebody else's code. Walking into them answers a
#: question about the project with a description of its dependencies.
SKIP_DIRECTORIES = frozenset({
    "node_modules", "vendor", "venv", ".venv", "target", "build", "dist",
    "__pycache__", "site-packages",
})

#: Past this many definitions of one name, "it could be any of these" has
#: stopped being information. Mirrors the cap the Python resolver uses.
MAX_AMBIGUOUS_CANDIDATES = 6

_KINDS = {
    "class": NodeKind.CLASS,
    "function": NodeKind.FUNCTION,
    "method": NodeKind.METHOD,
}

#: Decorators that describe how a callable behaves, not who reaches it.
#: `@staticmethod` says nothing about whether anything uses the function, so
#: it must not exempt it from the `unused` badge.
BEHAVIOUR_DECORATORS = frozenset({
    "property", "staticmethod", "classmethod",
    "setter", "getter", "deleter",
    "abstractmethod", "abstractproperty",
    "cached_property", "cache", "lru_cache", "wraps",
    "singledispatch", "singledispatchmethod", "total_ordering",
    "overload", "final", "no_type_check", "runtime_checkable",
    "dataclass", "contextmanager", "asynccontextmanager",
})

#: Of those, the ones the language itself then invokes. A property runs on
#: every attribute access, so it has no visible caller by nature.
LANGUAGE_INVOKED_DECORATORS = frozenset({
    "property", "cached_property", "setter", "getter", "deleter",
})


def is_dunder(name: str) -> bool:
    """A special method by name, called by the interpreter rather than from
    any visible call site."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def implicitly_called(definition) -> bool:
    """Whether anything reaches this without a call site a reader can see.

    Three ways that happens, and all of them mean the same for the `unused`
    badge: a reference exists, so this is not dead code.

    A dunder or a property is invoked by the language itself. Anything else
    carrying a decorator was handed to that decorator when the module was
    imported - a registry files it into a table, a framework binds it to a
    route - and a decorator receives a function rather than calling it, which
    is a reference all the same.

    Marking it rather than drawing an edge is deliberate. An edge would assert
    a call that never happens, stated at full confidence, which is exactly
    what the tiers exist to prevent.
    """
    if is_dunder(definition.name):
        return True
    for decorator in getattr(definition, "decorators", ()):
        tail = decorator.rsplit(".", 1)[-1]
        if tail in LANGUAGE_INVOKED_DECORATORS:
            return True
        if tail not in BEHAVIOUR_DECORATORS:
            return True
    return False


def module_body(nodes, module: str, grammar: Grammar, relative: str) -> str:
    """The callable a module's top-level code belongs to, created on demand.

    Import-time work is not an edge case. Decorator registration, dispatch
    tables, plugin discovery, settings modules and `__init__.py` re-exports
    all run because a module was imported rather than because a function was
    called, and an edge has to start at a callable. So the body is one: CPython
    compiles a module's top level into a code object literally named
    `<module>` and executes it once on first import. Go spells the same thing
    as package-level initialisation.

    A child of the module rather than the module itself, which is what keeps
    the change small - the edge invariant is untouched, and collapse folds it
    away like any other callable.

    Created only when something actually calls out from module scope. Most
    modules are nothing but imports and definitions, and a synthetic node on
    every one of them would double the node count to say nothing.
    """
    name = grammar.body_name
    body_id = f"{module}.{name}" if module else name
    if body_id not in nodes:
        nodes[body_id] = Node(
            id=body_id, kind=NodeKind.FUNCTION, name=name,
            parent=module or None,
            location=Location(relative, 1, 1),
            summary="Runs once, when this module is first imported.",
            # Nothing calls it - the import system runs it - so the `unused`
            # badge would be as wrong here as it is for a dunder.
            implicitly_called=True,
        )
    return body_id


@dataclass(frozen=True)
class SourceUnit:
    """One parsed file and the few facts every pass needs about it.

    A tuple grew to six fields and then seven, which is the point at which
    the reader has to count commas to know what they are holding.
    """

    tree: object
    source: bytes
    grammar: Grammar
    module: str
    relative: str
    is_package: bool
    path: Path | None = None


def imported_modules(unit: SourceUnit, known: set[str]) -> list[tuple[int, str]]:
    """(line, module id) for every import that lands inside the analysed tree.

    Nothing in `from x import a` says whether `a` is a submodule or an
    ordinary name, so both readings are tried against the modules that
    actually exist: `x.a` if that is one, otherwise `x` itself, which is the
    module whose body defines the name either way.

    A library import resolves to nothing here and draws nothing. Naming those
    would put `os` and `__future__` among the largest nodes on the canvas,
    since size is fan-in and they are imported everywhere.
    """
    if unit.grammar.imports is None:
        return []

    def text(node, src):
        return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    separator = unit.grammar.namespace_separator
    # A package's __init__ sits inside the package; any other module sits
    # beside its siblings, one level down from it.
    package = (unit.module if unit.is_package
               else unit.module.rpartition(separator)[0])

    found: list[tuple[int, str]] = []
    for line, level, dotted, names in unit.grammar.imports(
            unit.tree, text, unit.source):
        if level:
            base = package
            for _ in range(level - 1):
                base = base.rpartition(separator)[0]
        else:
            base = ""
        prefix = separator.join(p for p in (base, dotted) if p)

        # `from . import length, naming` imports two modules and has to draw
        # two edges. Taking only the first is how `from . import x, y` loses
        # y - which on the fixture silently dropped one of the two checks the
        # program runs, the exact failure this whole change exists to fix.
        submodules = [c for c in (separator.join((prefix, n)) for n in names)
                      if prefix and c in known and c != unit.module]
        if submodules:
            found.extend((line, target) for target in submodules)
        elif prefix in known and prefix != unit.module:
            # The names were ordinary names, so the edge lands on the module
            # whose body defines them.
            found.append((line, prefix))
    return found


def _file_of(nodes, module: str) -> str:
    """The file a module node was read from, for a body created on its behalf.

    An import needs somewhere to point, and the target's body may not exist
    yet - most modules only define things, so nothing had created one.
    """
    node = nodes.get(module)
    return node.location.file if node is not None and node.location else ""


def source_files(
    roots: list[Path], excludes: tuple[str, ...] | list[str] = ()
) -> list[tuple[Path, Path, Grammar]]:
    """(root, file, grammar) for everything a grammar claims, vendored code out.

    `excludes` are globs matched against the path relative to its root, the
    same way --exclude has always worked on the Python path.
    """
    patterns = tuple(excludes)
    found: list[tuple[Path, Path, Grammar]] = []
    for root in roots:
        base = Path(root)
        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            relative = path.relative_to(base)
            if any(part in SKIP_DIRECTORIES or part.startswith(".")
                   for part in relative.parts[:-1]):
                continue
            if any(fnmatch.fnmatch(relative.as_posix(), pattern)
                   for pattern in patterns):
                continue
            grammar = grammars.for_path(path.name)
            if grammar is not None:
                found.append((base, path, grammar))
    return found


def module_id_for(root: Path, path: Path, grammar: Grammar) -> str:
    """A name for the file, built from where it sits.

    For Go the path relative to the root is all there is: an import path lives
    in go.mod, and a package is a directory whatever it is called.

    Python says more. A package is the run of directories that keep having an
    `__init__.py`, so measuring from above that run recovers the name the
    interpreter would use. Measuring from the analysed root instead makes the
    ids depend on where you happened to point the tool - `codecards src` and
    `codecards src/codecards` would disagree about what everything is called,
    and the second would drop the package name entirely.
    """
    if grammar.name == "go":
        # A Go package is a directory; the file inside it is not a namespace.
        parent = path.relative_to(root).parent.as_posix()
        # A file sitting directly in the analysed directory has no relative
        # path to be named by, and an empty name means no module node at all -
        # so every definition in it becomes a root-level card. bubbletea keeps
        # 31 files there, which opened the graph on 174 loose cards instead of
        # a handful of packages. The directory's own name is what a reader
        # calls that package anyway.
        return root.name if parent == "." else parent
    base = _package_root(path)
    try:
        relative = path.relative_to(base)
    except ValueError:  # the file sits above its own package root
        relative = path.relative_to(root)
    stem = relative.with_suffix("")
    parts = [p for p in stem.parts if p != "__init__"]
    return ".".join(parts)


def _package_root(path: Path) -> Path:
    """The directory a Python module's dotted name should be measured from."""
    base = path.parent
    while (base / "__init__.py").is_file() and base.parent != base:
        base = base.parent
    return base


def analyze(
    roots: list[Path],
    *,
    excludes: tuple[str, ...] | list[str] = (),
    embed_source: bool = True,
) -> tuple[CodeGraph, AnalysisReport]:
    files = source_files([Path(r) for r in roots], excludes)
    nodes: dict[str, Node] = {}
    #: simple name -> the qualnames defining it, for the name match below.
    by_name: dict[str, list[str]] = {}
    #: module id -> the grammar that named it, since only the grammar knows
    #: what separates one level of its namespace from the next.
    module_grammars: dict[str, Grammar] = {}
    parsed: list[tuple] = []

    # -- pass 1: what exists ------------------------------------------------
    for root, path, grammar in files:
        source_bytes = syntax.read(path)
        if source_bytes is None:
            continue
        tree = syntax.parse(grammar, source_bytes)
        module = module_id_for(root, path, grammar)
        relative = path.relative_to(root).as_posix()
        parsed.append(SourceUnit(
            tree=tree, source=source_bytes, grammar=grammar, module=module,
            relative=relative, is_package=path.name == "__init__.py"))

        if module and module not in nodes:
            separator = grammar.namespace_separator
            holder, _, leaf = module.rpartition(separator)
            nodes[module] = Node(
                id=module, kind=NodeKind.MODULE,
                name=leaf or module, parent=holder or None,
                location=Location(relative, 1, 1))
            module_grammars[module] = grammar

        text = source_bytes.decode("utf-8", "replace").splitlines()
        for definition in syntax.definitions(grammar, tree, source_bytes):
            holder = _holder(definition, grammar, source_bytes, module)
            qualname = f"{holder}.{definition.name}" if holder else definition.name
            if qualname in nodes:
                continue

            body = tokens = None
            truncated = False
            if embed_source:
                last = min(definition.line_end,
                           definition.line_start + MAX_SOURCE_LINES - 1)
                truncated = last < definition.line_end
                body = "\n".join(text[definition.line_start - 1:last])
                runs = syntax.highlight(grammar, tree, source_bytes,
                                        definition.line_start - 1, last - 1)
                tokens = tuple(tuple(runs.get(line, ()))
                               for line in range(definition.line_start - 1, last))

            nodes[qualname] = Node(
                id=qualname,
                kind=_KINDS[definition.kind],
                name=definition.name,
                parent=holder or None,
                location=Location(relative, definition.line_start, definition.line_end),
                signature=definition.signature or None,
                summary=definition.docstring,
                source=body,
                source_tokens=tokens,
                source_truncated=truncated,
                implicitly_called=implicitly_called(definition),
                is_dunder=is_dunder(definition.name),
            )
            if definition.kind != "class":
                by_name.setdefault(definition.name, []).append(qualname)

    _add_package_ancestors(nodes, module_grammars)
    _add_missing_holders(nodes)

    # -- pass 2: calls, matched by name and never trusted -------------------
    by_confidence: dict[str, int] = {}
    merged: dict[tuple[str, str, Confidence], list[CallSite]] = {}

    for unit in parsed:
        tree, source_bytes, grammar = unit.tree, unit.source, unit.grammar
        module, relative = unit.module, unit.relative
        spans = sorted(((n.location.line_start, n.location.line_end, n.id)
                        for n in nodes.values()
                        if n.location and n.location.file == relative and n.id != module),
                       key=lambda s: s[0])

        def enclosing(line: int, spans=spans, module=module) -> str | None:
            best = None
            for start, end, node_id in spans:
                if start <= line <= end and nodes[node_id].kind in CALLABLE_KINDS:
                    best = node_id
            return best

        for site in syntax.call_sites(grammar, tree, source_bytes):
            caller = enclosing(site.line)
            if caller is None:
                # Not inside any function, so it runs on import. That has a
                # callable to leave from now.
                caller = module_body(nodes, module, grammar, relative)
            candidates = [q for q in by_name.get(site.text, ()) if q != caller]
            if not candidates:
                by_confidence["unresolved"] = by_confidence.get("unresolved", 0) + 1
                continue
            confidence = (Confidence.INFERRED if len(candidates) == 1
                          else Confidence.AMBIGUOUS)
            by_confidence[confidence.value] = by_confidence.get(confidence.value, 0) + 1
            if len(candidates) > MAX_AMBIGUOUS_CANDIDATES:
                continue
            call_site = CallSite(line=site.line, in_conditional=site.in_conditional,
                                 in_loop=site.in_loop)
            for target in candidates:
                merged.setdefault((caller, target, confidence), []).append(call_site)

        # An import runs the imported module's top level. Which module it
        # names is decided by the syntax, not guessed from a name, so these
        # are resolved even on the tier where nothing else is.
        for line, target_module in imported_modules(unit, set(module_grammars)):
            source_body = module_body(nodes, module, grammar, relative)
            target_body = module_body(nodes, target_module, grammar,
                                      _file_of(nodes, target_module))
            by_confidence["resolved"] = by_confidence.get("resolved", 0) + 1
            merged.setdefault(
                (source_body, target_body, Confidence.RESOLVED), []).append(
                    CallSite(line=line, in_conditional=False, in_loop=False))

    edges = [Edge(source=s, target=t, confidence=c, call_sites=tuple(sites))
             for (s, t, c), sites in sorted(merged.items(),
                                            key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value))]

    hints: list[EntryHint] = []
    for unit in parsed:
        tree, source_bytes, grammar, module = (
            unit.tree, unit.source, unit.grammar, unit.module)
        for definition in syntax.entry_definitions(grammar, tree, source_bytes):
            qualname = f"{module}.{definition.name}" if module else definition.name
            if qualname in nodes:
                hints.append(EntryHint(qualname, EntryReason.MAIN_BLOCK))
        for site in syntax.entry_calls(grammar, tree, source_bytes):
            for candidate in by_name.get(site.text, ()):
                hints.append(EntryHint(candidate, EntryReason.MAIN_BLOCK))

    graph = CodeGraph(nodes=nodes, edges=edges, entry_hints=hints)
    validate(graph)
    report = AnalysisReport(
        total_calls=sum(by_confidence.values()),
        by_confidence=by_confidence,
        skipped=[],
        node_count=len(nodes),
        callable_count=sum(1 for n in nodes.values() if n.kind in CALLABLE_KINDS),
        edge_count=len(edges),
    )
    return graph, report


def _holder(definition, grammar: Grammar, source: bytes, module: str) -> str:
    """The qualified name of whatever contains this definition."""
    def text(node, src):
        return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    chain: list[str] = []
    cursor = definition.parent
    while cursor is not None:
        chain.append(cursor.name)
        cursor = cursor.parent
    if chain:
        return ".".join([module, *reversed(chain)]) if module else ".".join(reversed(chain))

    # Nothing encloses it in the source, but a method may still belong to a
    # type by naming it as a receiver.
    if grammar.receiver_type is not None and definition.kind == "method":
        owner = grammar.receiver_type(definition.node, text, source) \
            if getattr(definition, "node", None) is not None else None
        if owner:
            return f"{module}.{owner}" if module else owner
    return module


def _add_package_ancestors(
    nodes: dict[str, Node], module_grammars: dict[str, Grammar]
) -> None:
    """Create the levels a module id implies but no file declares.

    A directory is a namespace whether or not anything sits directly in it:
    `cmd/tui` needs a `cmd` above it, and a Python package without an
    `__init__.py` never produces a module of its own. Without these the tree
    is a list, every module is a root, and a package is drawn beside the
    modules it holds rather than around them.
    """
    for module, grammar in sorted(module_grammars.items()):
        separator = grammar.namespace_separator
        parts = module.split(separator)
        for depth in range(1, len(parts)):
            ancestor = separator.join(parts[:depth])
            if ancestor in nodes:
                continue
            nodes[ancestor] = Node(
                id=ancestor, kind=NodeKind.PACKAGE, name=parts[depth - 1],
                parent=separator.join(parts[:depth - 1]) or None,
            )


def _add_missing_holders(nodes: dict[str, Node]) -> None:
    """Create any container a qualified name refers to but nothing declared.

    A receiver can name a type defined in another file of the same package,
    which is ordinary in Go and would otherwise leave a node pointing at a
    parent that does not exist.
    """
    for node in list(nodes.values()):
        parent = node.parent
        while parent and parent not in nodes:
            nodes[parent] = Node(
                id=parent, kind=NodeKind.CLASS,
                name=parent.rsplit(".", 1)[-1],
                parent=parent.rsplit(".", 1)[0] if "." in parent else None,
            )
            parent = nodes[parent].parent
