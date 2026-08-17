"""What one language's parse tree calls things.

Everything a language contributes to this extractor is here: the names its
grammar gives to nodes and fields, the words it paints as keywords, and the two
questions whose answers are genuinely different rather than merely differently
spelled - where a definition's prose lives, and what counts as a door into the
program.

The traversals themselves are in `syntax` and are shared. That split is the
whole bet: a second language should cost a table, not a second walker. Adding
Go is the test of it, so anything that had to become a callable here is a place
the bet did not quite pay, and is marked as such.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Grammar:
    name: str
    #: File suffixes this grammar claims. A SCIP index names its documents by
    #: path, so this is how a document finds its parser.
    suffixes: tuple[str, ...]

    #: Nodes that introduce a callable, and those that introduce a type. The
    #: two sets are disjoint and together are "things that become cards".
    callable_nodes: frozenset[str]
    type_nodes: frozenset[str]

    #: Nodes whose kind is "method" whatever they are nested in. Go attaches a
    #: method to its type through a receiver rather than by nesting it, so
    #: position cannot be what decides.
    method_nodes: frozenset[str] = frozenset()

    #: What separates one level of the namespace from the next, in the ids
    #: this language's index writes. Python nests modules with dots; Go writes
    #: an import path with slashes, and its first segment is a domain that
    #: contains a dot - so splitting a Go package on "." invents a package
    #: called "github" and mangles every name under it.
    namespace_separator: str = "."

    name_field: str = "name"
    parameters_field: str = "parameters"
    body_field: str = "body"

    call_node: str = "call"
    callee_field: str = "function"
    #: node type -> the field naming the member, for `a.b()` forms.
    member_fields: dict[str, str] = field(default_factory=dict)

    conditional_nodes: frozenset[str] = frozenset()
    loop_nodes: frozenset[str] = frozenset()

    #: node type -> the class the renderer paints it with.
    token_classes: dict[str, str] = field(default_factory=dict)
    keywords: frozenset[str] = frozenset()

    #: Import the tree-sitter binding only when this grammar is used, so a
    #: project in one language never needs the other's wheel installed.
    _module: str = ""

    #: The indexer that produces a SCIP index for this language: the name it
    #: writes into the index, what installs it, and what runs it. codecards
    #: never runs these - an indexer executes project code to resolve it - so
    #: they exist to be printed. Nothing goes here that has not been run and
    #: read, because a command that does not work costs a reader more than no
    #: command at all.
    indexer_tool: str | None = None
    indexer_install: str | None = None
    indexer_command: str | None = None

    #: The language server that resolves this language, and what installs it.
    #: Unlike an indexer, which codecards only ever prints for someone else to
    #: run, this is a process codecards starts itself - so it is looked up on
    #: PATH rather than assumed, and its absence is a reason to fall back
    #: rather than an error.
    #:
    #: Only servers that answer `textDocument/definition` usefully belong
    #: here. pylsp is deliberately absent: it advertises neither call
    #: hierarchy nor type definitions, so it would resolve less than the
    #: tree-sitter tier while looking authoritative.
    lsp_command: tuple[str, ...] = ()
    lsp_install: str | None = None

    #: What a server expects this language to be called in a didOpen.
    language_id: str = ""

    #: The prose attached to a definition. A Python docstring is the first
    #: statement inside the body; a Go doc comment is a line above the
    #: declaration. Same idea, no shared shape - hence a function.
    docstring: Callable[..., str | None] | None = None

    #: Calls that start a program. Python has `if __name__ == "__main__"`;
    #: Go has `func main` in package main, which is a definition rather than
    #: a call and so answers a different question entirely.
    entry_calls: Callable[..., list] | None = None
    #: Definitions that are themselves doors in. Go's `main` is one.
    entry_definitions: Callable[..., list] | None = None

    #: The type a method is attached to, for languages that attach by
    #: declaration rather than by nesting. With an index the symbol says this;
    #: without one it has to be read off the receiver.
    receiver_type: Callable[..., str | None] | None = None

    @cached_property
    def language(self) -> Any:
        import importlib  # noqa: PLC0415 - only when a grammar is actually used

        from tree_sitter import Language  # noqa: PLC0415

        return Language(importlib.import_module(self._module).language())


_PYTHON_KEYWORDS = frozenset({
    "def", "class", "return", "if", "elif", "else", "for", "while", "import",
    "from", "as", "with", "try", "except", "finally", "raise", "yield", "pass",
    "break", "continue", "lambda", "global", "nonlocal", "assert", "del",
    "async", "await", "not", "and", "or", "in", "is",
})

_GO_KEYWORDS = frozenset({
    "func", "type", "struct", "interface", "package", "import", "return",
    "if", "else", "for", "range", "switch", "case", "default", "select",
    "go", "defer", "chan", "map", "var", "const", "break", "continue",
    "fallthrough", "goto",
})


def _python_docstring(node, text, source: bytes) -> str | None:
    """The first string statement inside the body."""
    body = node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    if first.type != "expression_statement" or not first.children:
        return None
    literal = first.children[0]
    if literal.type != "string":
        return None
    raw = text(literal, source).strip("\"'\n ")
    lines = raw.strip().splitlines()
    return lines[0].strip() if lines else None


def _go_doc_comment(node, text, source: bytes) -> str | None:
    """The run of `//` lines immediately above the declaration.

    Go has no docstring: the convention is a comment block touching the
    declaration, conventionally opening with the name being declared. Only the
    first line is kept, matching what a Python docstring contributes.
    """
    # `type model struct{...}` names itself in a type_spec, whose previous
    # sibling is the `type` keyword. The comment sits above the declaration
    # holding it, one level up.
    if node.type == "type_spec" and node.parent is not None \
            and node.parent.type == "type_declaration":
        node = node.parent
    comment = node.prev_sibling
    if comment is None or comment.type != "comment":
        return None
    # Touching means the line directly above. A comment with a blank line
    # between it and the declaration is about something else.
    if comment.end_point[0] != node.start_point[0] - 1:
        return None
    # Walk back to the first line of the block, which is the one that reads
    # as the summary.
    while True:
        above = comment.prev_sibling
        if (above is None or above.type != "comment"
                or above.end_point[0] != comment.start_point[0] - 1):
            break
        comment = above
    line = text(comment, source).lstrip("/").strip()
    return line or None


def _go_receiver_type(node, text, source: bytes) -> str | None:
    """The type named in `func (m *model) Init()`, ignoring the pointer."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    for declaration in receiver.children:
        if declaration.type != "parameter_declaration":
            continue
        named = declaration.child_by_field_name("type")
        if named is None:
            continue
        if named.type == "pointer_type":
            for child in named.children:
                if child.type == "type_identifier":
                    return text(child, source)
            return None
        if named.type == "type_identifier":
            return text(named, source)
    return None


def _python_main_block(grammar, tree, source: bytes):
    """Calls under `if __name__ == "__main__":`, at module level."""
    from . import syntax  # noqa: PLC0415 - circular by nature, resolved at call time

    def text(node):
        return source[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def is_main_guard(node) -> bool:
        condition = node.child_by_field_name("condition")
        if condition is None or condition.type != "comparison_operator":
            return False
        body = text(condition)
        return "__name__" in body and "__main__" in body

    for node in tree.root_node.children:
        if node.type != "if_statement" or not is_main_guard(node):
            continue
        consequence = node.child_by_field_name("consequence")
        if consequence is None:
            continue
        return syntax.call_sites_in(grammar, consequence, source)
    return []


def _go_main_function(grammar, tree, source: bytes):
    """`func main` in `package main`, which no call site ever mentions.

    Go's entry point is invoked by the runtime, so unlike Python's main block
    there is nothing calling it to find. It has to be recognised by name, and
    only in the one package where the name means this.
    """
    from . import syntax  # noqa: PLC0415

    def text(node):
        return source[node.start_byte:node.end_byte].decode("utf-8", "replace")

    package = None
    for node in tree.root_node.children:
        if node.type == "package_clause":
            name = node.child_by_field_name("name")
            package = text(name) if name is not None else text(node).split()[-1]
            break
    if package != "main":
        return []
    return [d for d in syntax.definitions(grammar, tree, source)
            if d.name == "main" and d.kind == "function"]


PYTHON = Grammar(
    name="python",
    suffixes=(".py", ".pyi"),
    callable_nodes=frozenset({"function_definition"}),
    type_nodes=frozenset({"class_definition"}),
    member_fields={"attribute": "attribute"},
    conditional_nodes=frozenset({
        "if_statement", "conditional_expression", "case_clause"}),
    loop_nodes=frozenset({
        "for_statement", "while_statement", "list_comprehension",
        "set_comprehension", "dictionary_comprehension", "generator_expression"}),
    token_classes={
        "string": "str", "string_content": "str", "string_start": "str",
        "string_end": "str", "integer": "num", "float": "num",
        "comment": "com", "true": "kw", "false": "kw", "none": "kw",
    },
    keywords=_PYTHON_KEYWORDS,
    _module="tree_sitter_python",
    indexer_tool="scip-python",
    indexer_command="npx @sourcegraph/scip-python index --cwd {root} --output {output}",
    lsp_command=("pyright-langserver", "--stdio"),
    lsp_install="pip install pyright",
    language_id="python",
    docstring=_python_docstring,
    entry_calls=_python_main_block,
)

GO = Grammar(
    name="go",
    suffixes=(".go",),
    namespace_separator="/",
    # A type_spec is the `Name struct{...}` inside `type ( ... )`, which is
    # where the name lives whether or not the declaration is parenthesised.
    # A method_elem is a method named inside an interface body. It has no
    # implementation, but a call through the interface resolves to it, so
    # without a card here that edge lands on nothing and is dropped - which
    # silently loses every polymorphic call in the program.
    callable_nodes=frozenset({
        "function_declaration", "method_declaration", "method_elem"}),
    type_nodes=frozenset({"type_spec"}),
    method_nodes=frozenset({"method_declaration", "method_elem"}),
    call_node="call_expression",
    member_fields={"selector_expression": "field"},
    conditional_nodes=frozenset({
        "if_statement", "expression_switch_statement", "type_switch_statement",
        "select_statement"}),
    # Go spells every loop `for`.
    loop_nodes=frozenset({"for_statement"}),
    token_classes={
        "interpreted_string_literal": "str", "raw_string_literal": "str",
        "rune_literal": "str", "int_literal": "num", "float_literal": "num",
        "imaginary_literal": "num", "comment": "com",
        "true": "kw", "false": "kw", "nil": "kw", "iota": "kw",
    },
    keywords=_GO_KEYWORDS,
    _module="tree_sitter_go",
    indexer_tool="scip-go",
    indexer_install="go install github.com/scip-code/scip-go/cmd/scip-go@latest",
    # Two things this spells out rather than assumes. `go install` puts the
    # binary in GOPATH/bin, which is not on PATH unless someone put it there -
    # so naming the tool alone earns a "command not found" from anyone who
    # just followed the line above. And scip-go resolves package patterns
    # rather than paths, so it has to run from the project root.
    indexer_command=(
        'cd {root} && "$(go env GOPATH)/bin/scip-go" index ./... --output {output}'
    ),
    lsp_command=("gopls",),
    lsp_install="go install golang.org/x/tools/gopls@latest",
    language_id="go",
    docstring=_go_doc_comment,
    entry_definitions=_go_main_function,
    receiver_type=_go_receiver_type,
)

ALL = (PYTHON, GO)


def for_path(path: str) -> Grammar | None:
    """The grammar claiming this file, by suffix. None when nothing does."""
    for grammar in ALL:
        if any(path.endswith(suffix) for suffix in grammar.suffixes):
            return grammar
    return None


def for_tree(root: Path, *, limit: int = 4000) -> list[tuple[Grammar, int]]:
    """Which languages a directory holds, and how many files of each.

    Used to answer "there is no Python here, so what did you mean?". Skips
    hidden directories and vendored trees, which otherwise answer with a
    virtualenv or a node_modules rather than with the project.
    """
    skip = {"node_modules", "vendor", "venv", "target", "build", "dist"}
    counts: dict[str, int] = {}
    seen = 0
    for path in Path(root).rglob("*"):
        if seen >= limit:
            break
        if not path.is_file():
            continue
        if any(part.startswith(".") or part in skip for part in path.parts):
            continue
        grammar = for_path(path.name)
        if grammar is not None:
            counts[grammar.name] = counts.get(grammar.name, 0) + 1
            seen += 1
    by_name = {g.name: g for g in ALL}
    return sorted(((by_name[n], c) for n, c in counts.items()),
                  key=lambda pair: -pair[1])
