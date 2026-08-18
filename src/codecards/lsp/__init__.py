"""A graph resolved by asking a language server where each name is defined.

The same tier as a SCIP index and a different way of reaching it. An index is
a file: portable, reproducible, and stale the moment anyone edits. A server
reads the working tree, so nothing can be out of date, and nothing has to be
built first - but it has to be installed, it has to be running, and the graph
it produces cannot be handed to anyone else.

Structurally this is `parse.structural` with one thing replaced. Structural
matches a call site's name against every definition of that name and marks the
result a guess. This asks the server what the name at that exact position
refers to, and gets an answer or nothing. Every other decision - which files
are read, what becomes a node, how a method finds its holder, how packages
nest - is imported from there, so the two tiers cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

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
from ..parse import grammars, syntax
from ..parse.structural import (
    _KINDS,
    MAX_SOURCE_LINES,
    SourceUnit,
    _add_missing_holders,
    _add_package_ancestors,
    _file_of,
    _holder,
    implicitly_called,
    imported_modules,
    is_dunder,
    module_body,
    module_id_for,
    source_files,
)
from ..report import AnalysisReport
from .client import Client, ServerUnusable, find

__all__ = ["ServerUnusable", "analyze", "server_for"]


def server_for(paths) -> tuple[str, ...] | None:
    """The grammar-declared server command for the language of a tree, if it
    is actually installed. None means fall back rather than fail."""
    for path in paths:
        found = grammars.for_tree(Path(path)) if Path(path).is_dir() else []
        for grammar, _count in found:
            if grammar.lsp_command and find(grammar.lsp_command):
                return grammar.lsp_command
    return None


#: Synthetic container grouping stdlib and third-party leaf nodes, matching
#: what the Python path has always called it.
EXTERNAL_ROOT_ID = "<external>"


def _path_of(uri: str) -> str:
    return unquote(urlparse(uri).path)


def _external_name(uri: str, called: str) -> str:
    """A readable name for something defined outside the analysed tree.

    The server says where the definition lives and the call site says what
    was named, so `os.getcwd()` becomes `os.getcwd` - the same shape the
    Python resolver produces, rather than an absolute path into a virtualenv.
    """
    where = Path(_path_of(uri))
    holder = where.parent.name if where.stem == "__init__" else where.stem
    return f"{holder}.{called}" if holder else called


def analyze(
    roots,
    *,
    server: tuple[str, ...] | None = None,
    excludes: tuple[str, ...] | list[str] = (),
    include_external: bool = False,
    embed_source: bool = True,
) -> tuple[CodeGraph, AnalysisReport]:
    # Resolved before anything else touches them. The protocol addresses every
    # file by URI, and a relative path has no URI - `codecards src --lsp`
    # otherwise dies inside pathlib with nothing to connect it to the flag.
    absolute = [Path(r).resolve() for r in roots]
    root = absolute[0]
    files = source_files(absolute, excludes)
    if not files:
        raise ServerUnusable(f"no files a grammar recognises under {root}")

    if server:
        # A named server that is missing is an error, not a reason to quietly
        # run a different one. Someone who asked for a specific server wants
        # that server's answers, and substituting silently would make the
        # graph depend on what happened to be installed.
        command = find(server)
        if command is None:
            raise ServerUnusable(f"{server[0]} is not installed or not on PATH")
    else:
        command = _discover(files)

    nodes: dict[str, Node] = {}
    parsed: list[tuple] = []
    module_grammars: dict[str, object] = {}
    #: (absolute file, zero-based line of the defining name) -> node id. This
    #: is how a location the server hands back becomes a card.
    by_position: dict[tuple[str, int], str] = {}

    with Client(command, root) as client:
        capabilities = client.initialize()
        if not capabilities.get("definitionProvider"):
            raise ServerUnusable(
                f"{command[0]} does not answer textDocument/definition, so it "
                f"cannot say what any call resolves to")

        for base, path, grammar in files:
            source_bytes = syntax.read(path)
            if source_bytes is None:
                continue
            tree = syntax.parse(grammar, source_bytes)
            module = module_id_for(base, path, grammar)
            relative = path.relative_to(base).as_posix()
            parsed.append(SourceUnit(
                tree=tree, source=source_bytes, grammar=grammar, module=module,
                relative=relative, is_package=path.name == "__init__.py",
                path=path))
            client.open(path, grammar.language_id or grammar.name)

            if module and module not in nodes:
                holder, _, leaf = module.rpartition(grammar.namespace_separator)
                nodes[module] = Node(
                    id=module, kind=NodeKind.MODULE, name=leaf or module,
                    parent=holder or None, location=Location(relative, 1, 1))
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
                    id=qualname, kind=_KINDS[definition.kind], name=definition.name,
                    parent=holder or None,
                    location=Location(relative, definition.line_start,
                                      definition.line_end),
                    signature=definition.signature or None,
                    summary=definition.docstring,
                    source=body, source_tokens=tokens, source_truncated=truncated,
                    implicitly_called=implicitly_called(definition),
                    is_dunder=is_dunder(definition.name),
                )
                where = str(path.resolve())
                by_position[(where, definition.name_line)] = qualname
                # Servers disagree about whether a definition is the name or
                # the statement introducing it, so accept either.
                by_position.setdefault(
                    (where, definition.line_start - 1), qualname)

        _add_package_ancestors(nodes, module_grammars)
        _add_missing_holders(nodes)

        by_confidence: dict[str, int] = {}
        merged: dict[tuple[str, str], list[CallSite]] = {}
        external_edges: dict[tuple[str, str], list[CallSite]] = {}

        for unit in parsed:
            tree, source_bytes, grammar = unit.tree, unit.source, unit.grammar
            module, relative, path = unit.module, unit.relative, unit.path
            enclosing = _enclosing_index(nodes, relative, module)
            for site in syntax.call_sites(grammar, tree, source_bytes):
                source_id = enclosing(site.line)
                if source_id is None:
                    # Not inside any function, so it runs on import.
                    source_id = module_body(nodes, module, grammar, relative)
                found = _resolve(client, path, site, by_position)
                if found is None:
                    _count(by_confidence, "unresolved")
                    continue
                where, target = found
                call_site = CallSite(line=site.line,
                                     in_conditional=site.in_conditional,
                                     in_loop=site.in_loop)
                if where == "external":
                    _count(by_confidence, "external")
                    if not include_external:
                        continue
                    target = _external_name(target, site.text)
                    _add_external_node(nodes, target)
                    external_edges.setdefault((source_id, target), []).append(call_site)
                    continue
                _count(by_confidence, "resolved")
                merged.setdefault((source_id, target), []).append(call_site)

            # An import runs the imported module's top level, which the
            # syntax names outright - no server round trip needed.
            for line, target_module in imported_modules(
                    unit, set(module_grammars)):
                _count(by_confidence, "resolved")
                merged.setdefault(
                    (module_body(nodes, module, grammar, relative),
                     module_body(nodes, target_module, grammar,
                                 _file_of(nodes, target_module))), []).append(
                        CallSite(line=line, in_conditional=False, in_loop=False))

    edges = [Edge(source=s, target=t, confidence=Confidence.RESOLVED,
                  call_sites=tuple(sites))
             for (s, t), sites in sorted(merged.items())]
    edges += [Edge(source=s, target=t, confidence=Confidence.EXTERNAL,
                   call_sites=tuple(sites))
              for (s, t), sites in sorted(external_edges.items())]

    hints = _entry_hints(parsed, nodes)
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


def _count(tally: dict[str, int], key: str) -> None:
    tally[key] = tally.get(key, 0) + 1


def _discover(files) -> list[str]:
    for _base, _path, grammar in files:
        command = find(grammar.lsp_command)
        if command:
            return command
    grammar = files[0][2]
    hint = f" Install it with: {grammar.lsp_install}" if grammar.lsp_install else ""
    raise ServerUnusable(
        f"no language server found for {grammar.name}."
        f"{hint}")


def _enclosing_index(nodes, relative, module):
    spans = sorted(((n.location.line_start, n.location.line_end, n.id)
                    for n in nodes.values()
                    if n.location and n.location.file == relative and n.id != module),
                   key=lambda s: s[0])

    def enclosing(line: int) -> str | None:
        best = None
        for start, end, node_id in spans:
            if start <= line <= end and nodes[node_id].kind in CALLABLE_KINDS:
                best = node_id
        return best

    return enclosing


def _add_external_node(nodes, qualname: str) -> None:
    if EXTERNAL_ROOT_ID not in nodes:
        nodes[EXTERNAL_ROOT_ID] = Node(
            id=EXTERNAL_ROOT_ID, kind=NodeKind.PACKAGE, name="external")
    if qualname not in nodes:
        nodes[qualname] = Node(
            id=qualname, kind=NodeKind.FUNCTION,
            name=qualname.rsplit(".", 1)[-1], parent=EXTERNAL_ROOT_ID)


def _resolve(client, path, site, by_position):
    """Where the name at this call site is defined.

    ("internal", node id) when it lands on a card, ("external", uri) when the
    server named somewhere outside the analysed tree, and None when it had no
    answer. The last two both draw nothing by default, but only one of them is
    a gap in the analysis, so the report must not conflate them.
    """
    reply = client.request("textDocument/definition", {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": site.name_line, "character": site.name_char},
    })
    if "error" in reply:
        return None
    locations = reply.get("result") or []
    if isinstance(locations, dict):
        locations = [locations]
    if not locations:
        return None
    first = locations[0]
    uri = first.get("uri") or first.get("targetUri")
    span = first.get("range") or first.get("targetSelectionRange")
    if not uri or not span:
        return None
    target = by_position.get((_path_of(uri), span["start"]["line"]))
    return ("internal", target) if target else ("external", uri)


def _entry_hints(parsed, nodes) -> list[EntryHint]:
    hints: list[EntryHint] = []
    for unit in parsed:
        tree, source_bytes, grammar, module = (
            unit.tree, unit.source, unit.grammar, unit.module)
        for definition in syntax.entry_definitions(grammar, tree, source_bytes):
            qualname = f"{module}.{definition.name}" if module else definition.name
            if qualname in nodes:
                hints.append(EntryHint(qualname, EntryReason.MAIN_BLOCK))
        for site in syntax.entry_calls(grammar, tree, source_bytes):
            qualname = f"{module}.{site.text}" if module else site.text
            if qualname in nodes:
                hints.append(EntryHint(qualname, EntryReason.MAIN_BLOCK))
    return hints
