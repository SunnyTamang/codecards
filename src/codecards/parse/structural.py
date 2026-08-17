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


def source_files(roots: list[Path]) -> list[tuple[Path, Path, Grammar]]:
    """(root, file, grammar) for everything a grammar claims, vendored code out."""
    found: list[tuple[Path, Path, Grammar]] = []
    for root in roots:
        base = Path(root)
        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            if any(part in SKIP_DIRECTORIES or part.startswith(".")
                   for part in path.relative_to(base).parts[:-1]):
                continue
            grammar = grammars.for_path(path.name)
            if grammar is not None:
                found.append((base, path, grammar))
    return found


def module_id_for(root: Path, path: Path, grammar: Grammar) -> str:
    """A name for the file, built from where it sits.

    Without an index there is no authority on what a module is called - a Go
    import path lives in go.mod, a Python package name in the directory that
    stops having an __init__.py. The path relative to the root is the one
    thing always available, and it is what a reader recognises anyway.
    """
    relative = path.relative_to(root)
    if grammar.name == "go":
        # A Go package is a directory; the file inside it is not a namespace.
        parent = relative.parent.as_posix()
        return "" if parent == "." else parent
    stem = relative.with_suffix("")
    parts = [p for p in stem.parts if p != "__init__"]
    return ".".join(parts)


def analyze(
    roots: list[Path],
    *,
    embed_source: bool = True,
) -> tuple[CodeGraph, AnalysisReport]:
    files = source_files([Path(r) for r in roots])
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
        parsed.append((tree, source_bytes, grammar, module, relative))

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
                is_dunder=definition.name.startswith("__")
                          and definition.name.endswith("__"),
            )
            if definition.kind != "class":
                by_name.setdefault(definition.name, []).append(qualname)

    _add_package_ancestors(nodes, module_grammars)
    _add_missing_holders(nodes)

    # -- pass 2: calls, matched by name and never trusted -------------------
    by_confidence: dict[str, int] = {}
    merged: dict[tuple[str, str, Confidence], list[CallSite]] = {}

    for tree, source_bytes, grammar, module, relative in parsed:
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
                # A call at module scope has no calling callable, the way the
                # indexed path also declines to draw one.
                by_confidence["unresolved"] = by_confidence.get("unresolved", 0) + 1
                continue
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

    edges = [Edge(source=s, target=t, confidence=c, call_sites=tuple(sites))
             for (s, t, c), sites in sorted(merged.items(),
                                            key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value))]

    hints: list[EntryHint] = []
    for tree, source_bytes, grammar, module, _relative in parsed:
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
