"""Reading structure out of source with tree-sitter.

This is the half a SCIP index cannot supply. An index records that the name at
a position resolves to a symbol, but its occurrence roles are reads and writes
- there is no "call". Taking a reference to a function and calling it are the
same role, so syntax is the only thing that tells them apart.

Nothing here resolves anything. It reports what the file says: where the
definitions are, where the calls are, and which run of characters is a keyword
or a string. What any of it means is the index's job.

Nothing here knows what language it is reading either. Every node type, field
name and keyword arrives in a `Grammar`; this module only walks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from .grammars import Grammar


@dataclass
class Definition:
    name: str
    kind: str                    # "class", "function" or "method"
    line_start: int              # one based
    line_end: int
    name_line: int               # zero based, to match a SCIP occurrence
    name_char: int
    signature: str
    docstring: str | None
    parent: Definition | None = None
    children: list[Definition] = field(default_factory=list)


@dataclass(frozen=True)
class CallSite:
    """A call, and the identifier naming what is being called."""
    line: int                    # one based, for the card gutter
    name_line: int               # zero based, to match a SCIP occurrence
    name_char: int
    text: str
    in_conditional: bool
    in_loop: bool


def parse(grammar: Grammar, source: bytes):
    return Parser(grammar.language).parse(source)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def definitions(grammar: Grammar, tree, source: bytes) -> list[Definition]:
    """Every type and callable in the file, nested as they are written.

    Nesting is only what the source shows. A language that attaches a method
    to its type by declaring a receiver rather than by nesting it reports that
    method with no parent here, and the index supplies the containment.
    """
    found: list[Definition] = []
    wanted = grammar.callable_nodes | grammar.type_nodes

    def walk(node: Node, parent: Definition | None) -> None:
        current = parent
        if node.type in wanted:
            name_node = node.child_by_field_name(grammar.name_field)
            if name_node is not None:
                params = node.child_by_field_name(grammar.parameters_field)
                is_type = node.type in grammar.type_nodes
                if is_type:
                    kind = "class"
                elif node.type in grammar.method_nodes:
                    kind = "method"
                else:
                    kind = "method" if parent and parent.kind == "class" else "function"
                definition = Definition(
                    name=_text(name_node, source),
                    kind=kind,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    name_line=name_node.start_point[0],
                    name_char=name_node.start_point[1],
                    signature="" if is_type or params is None
                              else _text(params, source),
                    docstring=(grammar.docstring(node, _text, source)
                               if grammar.docstring else None),
                    parent=parent,
                )
                if parent is not None:
                    parent.children.append(definition)
                found.append(definition)
                current = definition
        for child in node.children:
            walk(child, current)

    walk(tree.root_node, None)
    return found


def call_sites(grammar: Grammar, tree, source: bytes) -> list[CallSite]:
    """Every call expression, with the identifier that names the callee.

    For `a.b.c(x)` that identifier is `c`, which is the position a SCIP
    occurrence will sit at. A call on something that is not a name at all,
    such as `funcs[0](x)`, has nothing to look up and is skipped.
    """
    sites: list[CallSite] = []

    def callee_name(node: Node) -> Node | None:
        function = node.child_by_field_name(grammar.callee_field)
        if function is None:
            return None
        if function.type == "identifier":
            return function
        member_field = grammar.member_fields.get(function.type)
        if member_field is not None:
            return function.child_by_field_name(member_field)
        return None

    def walk(node: Node, conditional: bool, loop: bool) -> None:
        in_conditional = conditional or node.type in grammar.conditional_nodes
        in_loop = loop or node.type in grammar.loop_nodes

        if node.type == grammar.call_node:
            name = callee_name(node)
            if name is not None:
                sites.append(CallSite(
                    line=name.start_point[0] + 1,
                    name_line=name.start_point[0],
                    name_char=name.start_point[1],
                    text=_text(name, source),
                    in_conditional=in_conditional,
                    in_loop=in_loop,
                ))
        for child in node.children:
            walk(child, in_conditional, in_loop)

    walk(tree.root_node, False, False)
    return sites


def entry_calls(grammar: Grammar, tree, source: bytes) -> list[CallSite]:
    """Calls that start the program, when the language marks them as such.

    A door into the program is the one signal that makes the entry-point menu
    a menu. Without it the fallback is "nothing calls this", which on a real
    project lists every test and every helper and never mentions main.
    """
    if grammar.entry_calls is None:
        return []
    return grammar.entry_calls(grammar, tree, source)


def entry_definitions(grammar: Grammar, tree, source: bytes) -> list[Definition]:
    """Definitions that are themselves doors in, for languages that have them.

    Go's `func main` is not a call anywhere - the runtime invokes it - so no
    amount of looking at call sites will find it.
    """
    if grammar.entry_definitions is None:
        return []
    return grammar.entry_definitions(grammar, tree, source)


def call_sites_in(grammar: Grammar, node: Node, source: bytes) -> list[CallSite]:
    """The calls inside one subtree, rather than the whole file."""
    holder = type("Holder", (), {"root_node": node})
    return call_sites(grammar, holder, source)


def highlight(grammar: Grammar, tree, source: bytes, first_line: int, last_line: int):
    """Token runs per line, in the shape the renderer already paints.

    Only non-plain runs are emitted, which is what keeps the payload small.
    """
    runs: dict[int, list[tuple[int, int, str]]] = {}
    definition_nodes = grammar.callable_nodes | grammar.type_nodes

    def classify(node: Node) -> str | None:
        if node.type in grammar.token_classes:
            return grammar.token_classes[node.type]
        if node.type in grammar.keywords:
            return "kw"
        if node.type == "identifier":
            parent = node.parent
            if parent is None:
                return None
            if parent.type in definition_nodes and \
                    parent.child_by_field_name(grammar.name_field) == node:
                return "def"
            if parent.type == grammar.call_node and \
                    parent.child_by_field_name(grammar.callee_field) == node:
                return "call"
            member_field = grammar.member_fields.get(parent.type)
            if member_field is not None and \
                    parent.child_by_field_name(member_field) == node and \
                    parent.parent is not None and \
                    parent.parent.type == grammar.call_node:
                return "call"
        return None

    def walk(node: Node) -> None:
        if node.start_point[0] > last_line:
            return
        cls = classify(node)
        if cls and node.start_point[0] == node.end_point[0]:
            line = node.start_point[0]
            if first_line <= line <= last_line:
                runs.setdefault(line, []).append(
                    (node.start_point[1], node.end_point[1] - node.start_point[1], cls))
        for child in node.children:
            if child.end_point[0] < first_line:
                continue
            walk(child)

    walk(tree.root_node)
    return runs


def read(path: Path) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None
