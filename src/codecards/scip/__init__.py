"""An extractor built from a SCIP index and a tree-sitter parse.

The division of labour is the whole idea. Tree-sitter says *this is a call,
and here is the identifier naming what it calls*. The index says *the name at
that position is this definition*. Neither can answer the other's question:
an index's occurrence roles are reads and writes, with nothing distinguishing
a call from a mention, and a parse tree knows nothing about what a name means.

Nothing in here resolves anything itself, which is the point. The resolver in
`extract/` is about nine hundred lines of Python-specific rules - scoping,
imports, MRO walking, type inference - and this replaces all of it with a
lookup. What it costs is that somebody has to run an indexer first, and that
the index is only as good as the environment it was built in.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..graph.model import (
    CallSite,
    CodeGraph,
    Confidence,
    Edge,
    EntryHint,
    EntryReason,
    Location,
    Node,
    NodeKind,
)
from ..report import AnalysisReport
from . import index as scip
from . import syntax

MAX_SOURCE_LINES = 400

_KINDS = {
    "class": NodeKind.CLASS,
    "function": NodeKind.FUNCTION,
    "method": NodeKind.METHOD,
}


class IndexUnusable(Exception):
    """The index holds nothing, which an indexer emits without failing."""


def _module_id(symbol: str) -> str | None:
    parsed = scip.parse(symbol)
    if parsed is None or not parsed.descriptors:
        return None
    return parsed.descriptors[0]


def _qualname(symbol: str) -> str | None:
    """The dotted name a symbol describes, ignoring parameters and locals."""
    parsed = scip.parse(symbol)
    if parsed is None:
        return None
    keep = [d for d, k in zip(parsed.descriptors, parsed.kinds, strict=False)
            if k in ("namespace", "type", "method", "term")]
    return ".".join(part for part in keep if part) or None


def analyze(
    roots: list[Path],
    index_path: Path,
    *,
    embed_source: bool = True,
) -> tuple[CodeGraph, AnalysisReport]:
    root = Path(roots[0]).resolve()
    index = scip.read(Path(index_path))
    documents = list(index.documents)
    if scip.is_empty(documents):
        raise IndexUnusable(
            f"{index_path} contains no occurrences. An indexer that cannot load "
            "the project writes a valid but empty index and exits successfully; "
            "check that its dependencies are installed."
        )
    _check_root_matches(index, root, index_path)

    nodes: dict[str, Node] = {}
    parents: dict[str, str | None] = {}
    #: (file, line, char) -> symbol, for every name the index resolved.
    resolved: dict[tuple[str, int, int], str] = {}
    #: symbol -> the qualname of the definition it names.
    defines: dict[str, str] = {}
    module_of: dict[str, str] = {}

    by_confidence: dict[str, int] = {}
    call_records: list[tuple[str, str, CallSite]] = []

    for doc in documents:
        for occ in doc.occurrences:
            if occ.is_local:
                continue
            resolved[(doc.path, occ.line, occ.start_char)] = occ.symbol

    # -- pass 1: what exists, and what each symbol names --------------------
    parsed_files: dict[str, tuple] = {}
    for doc in documents:
        source_bytes = syntax.read(root / doc.path)
        if source_bytes is None:
            continue
        tree = syntax.parse(source_bytes)
        parsed_files[doc.path] = (tree, source_bytes)

        module = None
        for occ in doc.occurrences:
            module = _module_id(occ.symbol)
            if module:
                break
        if module is None:
            continue

        if module not in nodes:
            nodes[module] = Node(id=module, kind=NodeKind.MODULE,
                                 name=module.rsplit(".", 1)[-1], parent=None,
                                 location=Location(doc.path, 1, 1))
            parents[module] = None
        module_of[doc.path] = module

        text = source_bytes.decode("utf-8", "replace").splitlines()
        for definition in syntax.definitions(tree, source_bytes):
            symbol = resolved.get((doc.path, definition.name_line, definition.name_char))
            qualname = _qualname(symbol) if symbol else None
            if qualname is None:
                # A local helper: the index numbers it per file, so name it
                # from the chain of definitions holding it.
                chain, cursor = [definition.name], definition.parent
                while cursor is not None:
                    chain.append(cursor.name)
                    cursor = cursor.parent
                qualname = ".".join([module, *reversed(chain)])
            if symbol:
                defines[symbol] = qualname

            parent_id = module
            if definition.parent is not None:
                parent_symbol = resolved.get(
                    (doc.path, definition.parent.name_line, definition.parent.name_char))
                parent_id = (_qualname(parent_symbol) if parent_symbol else None) \
                    or qualname.rsplit(".", 1)[0]

            body = None
            tokens = None
            truncated = False
            if embed_source:
                last = min(definition.line_end, definition.line_start + MAX_SOURCE_LINES - 1)
                truncated = last < definition.line_end
                body = "\n".join(text[definition.line_start - 1:last])
                runs = syntax.highlight(tree, source_bytes,
                                        definition.line_start - 1, last - 1)
                tokens = tuple(
                    tuple(runs.get(line, ()))
                    for line in range(definition.line_start - 1, last)
                )

            nodes[qualname] = Node(
                id=qualname,
                kind=_KINDS[definition.kind],
                name=definition.name,
                parent=parent_id,
                location=Location(doc.path, definition.line_start, definition.line_end),
                signature=definition.signature or None,
                summary=definition.docstring,
                source=body,
                source_tokens=tokens,
                source_truncated=truncated,
                is_dunder=definition.name.startswith("__") and definition.name.endswith("__"),
            )
            parents[qualname] = parent_id

    # -- pass 2: every call tree-sitter found, looked up in the index -------
    for path, (tree, source_bytes) in parsed_files.items():
        module = module_of.get(path)
        if module is None:
            continue
        spans = sorted(
            ((n.location.line_start, n.location.line_end, n.id)
             for n in nodes.values()
             if n.location and n.location.file == path and n.id != module),
            key=lambda s: s[0])

        def enclosing(line: int, spans=spans, module=module) -> str:
            best = module
            for start, end, node_id in spans:
                if start <= line <= end:
                    best = node_id
            return best

        for site in syntax.call_sites(tree, source_bytes):
            symbol = resolved.get((path, site.name_line, site.name_char))
            if symbol is None:
                by_confidence["unresolved"] = by_confidence.get("unresolved", 0) + 1
                continue
            target = defines.get(symbol)
            if target is None:
                by_confidence["external"] = by_confidence.get("external", 0) + 1
                continue
            source_id = enclosing(site.line)
            if source_id == target:
                continue
            by_confidence["resolved"] = by_confidence.get("resolved", 0) + 1
            call_records.append((source_id, target, CallSite(
                line=site.line,
                in_conditional=site.in_conditional,
                in_loop=site.in_loop,
            )))

    # Packages, so a deep module chain still collapses the way the view expects.
    for module_id in [n.id for n in nodes.values() if n.kind is NodeKind.MODULE]:
        parts = module_id.split(".")
        for depth in range(1, len(parts)):
            package = ".".join(parts[:depth])
            if package not in nodes:
                nodes[package] = Node(id=package, kind=NodeKind.PACKAGE,
                                      name=parts[depth - 1],
                                      parent=".".join(parts[:depth - 1]) or None)
        if len(parts) > 1:
            nodes[module_id] = _reparent(nodes[module_id], ".".join(parts[:-1]))

    # -- doors into the program --------------------------------------------
    # Everything meaningful about an entry point comes from the extractor;
    # the graph layer only ever adds "nothing calls this" on its own. Leaving
    # these out is what makes the menu a list of every uncalled helper.
    hints: list[EntryHint] = []
    for path, (tree, source_bytes) in parsed_files.items():
        for site in syntax.main_block_calls(tree, source_bytes):
            symbol = resolved.get((path, site.name_line, site.name_char))
            target = defines.get(symbol) if symbol else None
            if target:
                hints.append(EntryHint(target, EntryReason.MAIN_BLOCK))

    for node in nodes.values():
        if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD) \
                and node.name.startswith("test_") \
                and node.location and _looks_like_a_test_file(node.location.file):
            hints.append(EntryHint(node.id, EntryReason.TEST))

    merged: dict[tuple[str, str], list[CallSite]] = {}
    for source_id, target, site in call_records:
        if source_id in nodes and target in nodes:
            merged.setdefault((source_id, target), []).append(site)

    edges = [
        Edge(source=s, target=t, confidence=Confidence.RESOLVED, call_sites=tuple(sites))
        for (s, t), sites in sorted(merged.items())
    ]

    report = AnalysisReport(
        total_calls=sum(by_confidence.values()),
        by_confidence=by_confidence,
        skipped=[],
        node_count=len(nodes),
        callable_count=sum(
            1 for n in nodes.values()
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ),
        edge_count=len(edges),
    )
    return CodeGraph(nodes=nodes, edges=edges, entry_hints=hints), report


def _check_root_matches(index: scip.Index, root: Path, index_path: Path) -> None:
    """An index is tied to the directory it was built against.

    Every path in it is relative to that root, so pointing at anything else
    resolves none of them. Without this the run ends at "no Python files
    found", which reads as an empty directory rather than a mismatched pair.
    """
    present = sum(1 for doc in index.documents if (root / doc.path).exists())
    if present:
        return
    built_for = index.project_root.removeprefix("file://") or "an unknown directory"
    raise IndexUnusable(
        f"{index_path} was built for {built_for}, and none of its "
        f"{len(index.documents)} files are under {root}. An index records "
        "paths relative to the directory it was indexed from, so it has to be "
        "read from that same directory. Re-index this project, or point "
        "codecards at the one the index covers."
    )


def _looks_like_a_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in path


def _reparent(node: Node, parent: str) -> Node:
    return replace(node, parent=parent)
