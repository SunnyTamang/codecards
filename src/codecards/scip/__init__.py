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

from collections.abc import Iterable
from dataclasses import replace
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


def stale_sources(root: Path, index_path: Path, documents: Iterable[str]) -> list[str]:
    """Files the index says it describes, modified after it was written.

    Asks the index which files it covers rather than walking the tree for
    *.py. A walk finds the virtualenv: on a 46-file project it reported 3,447
    stale files, all but a handful of them site-packages the graph never drew.
    The index's own document list is exactly the set the graph was built from.

    An index has no notion of when it was built and no checksum of what it
    read, so the file's own mtime is the only thing available. That makes this
    a smoke alarm, not a proof: it catches the ordinary case of editing code
    and forgetting to re-index, and it will miss a change that preserves the
    timestamp, or cry wolf after a checkout that rewrites mtimes without
    changing content. Both failures are acceptable in a warning that never
    blocks; neither would be in anything that did.

    A file created since the index was written is invisible here, because the
    index has no document for it. That is a different kind of staleness - a
    missing card rather than a wrong one - and this does not claim to catch it.
    """
    try:
        built = index_path.stat().st_mtime
    except OSError:
        return []
    newer: list[str] = []
    for relative in sorted(set(documents)):
        try:
            if (root / relative).stat().st_mtime > built:
                newer.append(relative)
        except OSError:  # listed in the index but not on disk now
            continue
    return newer


#: How to invoke each indexer we have actually run. A name is not a command:
#: scip-python ships on npm and is normally not on PATH at all, and its `index`
#: subcommand takes no positional project root - it reads --cwd. Printing the
#: bare name produced "command not found" for the first reader who tried to
#: follow the advice. Nothing goes in here that has not been run and read.
_INVOCATIONS = {
    "scip-python": "npx @sourcegraph/scip-python index --cwd {root} --output {output}",
}


def reindex_command(index_path: Path, root: Path, tool: str | None) -> str | None:
    """The command that would rebuild this index, when we know it exactly.

    None when we do not. An indexer we have never run has a CLI we would be
    guessing at, and a command that does not work is worse than no command:
    it spends the reader's trust and their time before failing. The warning
    still names the files and says the index is old.

    codecards never runs this itself. An indexer executes project code in
    order to resolve it, which is not something a viewer should do on
    someone's behalf.
    """
    template = _INVOCATIONS.get(tool or "")
    if template is None:
        return None
    return template.format(root=root, output=index_path)


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
        # A call written at module scope encloses to the module, which does not
        # call anything - the interpreter runs it on import. The ones that
        # matter are already carried as MAIN_BLOCK entry hints above, so an
        # edge here would say the same thing in a shape the graph forbids.
        # The call still counts as resolved; it simply has no caller to leave.
        if nodes.get(source_id) is None or nodes[source_id].kind not in CALLABLE_KINDS:
            continue
        if target in nodes:
            merged.setdefault((source_id, target), []).append(site)

    edges = [
        Edge(source=s, target=t, confidence=Confidence.RESOLVED, call_sites=tuple(sites))
        for (s, t), sites in sorted(merged.items())
    ]

    stale = stale_sources(root, Path(index_path), (d.path for d in documents))
    report = AnalysisReport(
        total_calls=sum(by_confidence.values()),
        by_confidence=by_confidence,
        skipped=[],
        stale=stale,
        reindex_command=(
            reindex_command(Path(index_path), root, index.tool) if stale else None
        ),
        node_count=len(nodes),
        callable_count=sum(
            1 for n in nodes.values()
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ),
        edge_count=len(edges),
    )
    graph = CodeGraph(nodes=nodes, edges=edges, entry_hints=hints)
    # The same check the AST extractor runs before handing a graph on. This
    # path went without it, so a graph the renderer's invariants forbid was
    # built and drawn anyway, and nothing said so.
    validate(graph)
    return graph, report


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
