"""Reading structure out of source with tree-sitter.

This is the half a SCIP index cannot supply. An index records that the name at
a position resolves to a symbol, but its occurrence roles are reads and writes
- there is no "call". Taking a reference to a function and calling it are the
same role, so syntax is the only thing that tells them apart.

Nothing here resolves anything. It reports what the file says: where the
definitions are, where the calls are, and which run of characters is a keyword
or a string. What any of it means is the index's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Node, Parser

LANGUAGE = Language(tree_sitter_python.language())

#: node type -> the class the renderer paints it with.
_TOKEN_CLASS = {
    "string": "str", "string_content": "str", "string_start": "str",
    "string_end": "str", "integer": "num", "float": "num", "comment": "com",
    "true": "kw", "false": "kw", "none": "kw",
}
_KEYWORDS = {
    "def", "class", "return", "if", "elif", "else", "for", "while", "import",
    "from", "as", "with", "try", "except", "finally", "raise", "yield", "pass",
    "break", "continue", "lambda", "global", "nonlocal", "assert", "del",
    "async", "await", "not", "and", "or", "in", "is",
}

_DEFINITION_NODES = {"function_definition", "class_definition"}


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


def parse(source: bytes):
    return Parser(LANGUAGE).parse(source)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _docstring(body: Node | None, source: bytes) -> str | None:
    if body is None or not body.children:
        return None
    first = body.children[0]
    if first.type != "expression_statement" or not first.children:
        return None
    literal = first.children[0]
    if literal.type != "string":
        return None
    raw = _text(literal, source).strip("\"'\n ")
    line = raw.strip().splitlines()
    return line[0].strip() if line else None


def definitions(tree, source: bytes) -> list[Definition]:
    """Every class and callable in the file, nested as they are written."""
    found: list[Definition] = []

    def walk(node: Node, parent: Definition | None) -> None:
        current = parent
        if node.type in _DEFINITION_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                params = node.child_by_field_name("parameters")
                is_class = node.type == "class_definition"
                kind = "class" if is_class else (
                    "method" if parent and parent.kind == "class" else "function")
                definition = Definition(
                    name=_text(name_node, source),
                    kind=kind,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    name_line=name_node.start_point[0],
                    name_char=name_node.start_point[1],
                    signature="" if is_class or params is None
                              else _text(params, source),
                    docstring=_docstring(node.child_by_field_name("body"), source),
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


def call_sites(tree, source: bytes) -> list[CallSite]:
    """Every call expression, with the identifier that names the callee.

    For `a.b.c(x)` that identifier is `c`, which is the position a SCIP
    occurrence will sit at. A call on something that is not a name at all,
    such as `funcs[0](x)`, has nothing to look up and is skipped.
    """
    sites: list[CallSite] = []

    def callee_name(node: Node) -> Node | None:
        function = node.child_by_field_name("function")
        if function is None:
            return None
        if function.type == "identifier":
            return function
        if function.type == "attribute":
            return function.child_by_field_name("attribute")
        return None

    def walk(node: Node, conditional: bool, loop: bool) -> None:
        in_conditional = conditional or node.type in (
            "if_statement", "conditional_expression", "case_clause")
        in_loop = loop or node.type in (
            "for_statement", "while_statement", "list_comprehension",
            "set_comprehension", "dictionary_comprehension", "generator_expression")

        if node.type == "call":
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


def highlight(tree, source: bytes, first_line: int, last_line: int):
    """Token runs per line, in the shape the renderer already paints.

    Only non-plain runs are emitted, which is what keeps the payload small.
    """
    runs: dict[int, list[tuple[int, int, str]]] = {}

    def classify(node: Node) -> str | None:
        if node.type in _TOKEN_CLASS:
            return _TOKEN_CLASS[node.type]
        if node.type in _KEYWORDS:
            return "kw"
        if node.type == "identifier":
            parent = node.parent
            if parent is None:
                return None
            if parent.type in _DEFINITION_NODES and \
                    parent.child_by_field_name("name") == node:
                return "def"
            if parent.type == "call" and parent.child_by_field_name("function") == node:
                return "call"
            if parent.type == "attribute" and \
                    parent.child_by_field_name("attribute") == node and \
                    parent.parent is not None and parent.parent.type == "call":
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
