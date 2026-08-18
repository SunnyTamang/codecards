"""Reading a SCIP index without a protobuf dependency.

The index is built by hand here rather than fetched, so the test says exactly
which bytes it expects to understand and fails loudly if the assumptions about
field numbers ever stop holding.
"""

from __future__ import annotations

import pytest

from codecards.graph.model import CALLABLE_KINDS, validate
from codecards.scip import index as scip


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def tag(number: int, wire: int) -> bytes:
    return varint(number << 3 | wire)


def delimited(number: int, payload: bytes) -> bytes:
    return tag(number, 2) + varint(len(payload)) + payload


def occurrence(line: int, start: int, end: int, symbol: str, roles: int = 0) -> bytes:
    span = varint(line) + varint(start) + varint(end)
    body = delimited(1, span) + delimited(2, symbol.encode())
    if roles:
        body += tag(3, 0) + varint(roles)
    return delimited(2, body)


def document(path: str, *occurrences: bytes) -> bytes:
    return delimited(2, delimited(1, path.encode()) + b"".join(occurrences))


SYMBOL = "scip-python python comp-harness 0.1.0 `pkg.mod`/Thing#run()."


def test_an_index_round_trips_through_the_reader(tmp_path):
    raw = document(
        "src/pkg/mod.py",
        occurrence(12, 4, 7, SYMBOL, roles=scip.DEFINITION),
        occurrence(30, 8, 11, SYMBOL),
    )
    path = tmp_path / "index.scip"
    path.write_bytes(raw)

    docs = scip.read(path).documents
    assert len(docs) == 1
    assert docs[0].path == "src/pkg/mod.py"

    definition, reference = docs[0].occurrences
    assert definition.is_definition and not reference.is_definition
    assert (definition.line, definition.start_char, definition.end_char) == (12, 4, 7)
    assert reference.symbol == SYMBOL


def test_an_empty_index_is_detected(tmp_path):
    """An indexer that cannot load the project writes a valid index holding
    nothing, and exits successfully. Trusting the exit code reports a healthy
    repository as having no code in it."""
    path = tmp_path / "empty.scip"
    path.write_bytes(document("src/pkg/mod.py"))
    assert scip.is_empty(scip.read(path).documents) is True

    path.write_bytes(document("src/pkg/mod.py", occurrence(1, 0, 3, SYMBOL)))
    assert scip.is_empty(scip.read(path).documents) is False


def test_locals_are_recognised_as_meaningless_outside_their_file(tmp_path):
    path = tmp_path / "index.scip"
    path.write_bytes(document("m.py", occurrence(1, 0, 3, "local 4")))
    assert scip.read(path).documents[0].occurrences[0].is_local is True


def test_a_symbol_splits_into_its_package_and_descriptors():
    parsed = scip.parse(SYMBOL)
    assert parsed is not None
    assert parsed.package == "comp-harness"
    assert parsed.version == "0.1.0"
    assert parsed.descriptors == ("pkg.mod", "Thing", "run")
    assert parsed.kinds == ("namespace", "type", "method")


def test_a_backtick_quoted_module_keeps_its_dots():
    """`pkg.mod` is one descriptor, not three. Splitting on the dot would name
    every module's members as though they lived in a package chain."""
    parsed = scip.parse(SYMBOL)
    assert parsed.descriptors[0] == "pkg.mod"


def test_a_stdlib_symbol_reports_itself_as_external():
    parsed = scip.parse("scip-python python python-stdlib 3.11 builtins/dict#get().")
    assert parsed is not None
    assert parsed.is_external is True
    assert scip.parse(SYMBOL).is_external is False


def test_a_local_symbol_has_nothing_to_parse():
    assert scip.parse("local 12") is None
    assert scip.parse("") is None


def metadata(project_root: str) -> bytes:
    return delimited(1, delimited(3, project_root.encode()))


def test_the_directory_the_index_was_built_for_is_recorded(tmp_path):
    """It is the only thing in the file that says which project this is."""
    path = tmp_path / "index.scip"
    path.write_bytes(metadata("file:///home/me/thing")
                     + document("src/thing/core.py", occurrence(1, 0, 3, SYMBOL)))
    index = scip.read(path)
    assert index.project_root == "file:///home/me/thing"
    assert len(index.documents) == 1


def test_an_index_read_from_the_wrong_directory_says_so(tmp_path):
    """Every path in an index is relative to the root it was built from, so
    reading it anywhere else resolves nothing. Reporting that as "no Python
    files found" reads as an empty directory rather than a mismatched pair."""
    from codecards.scip import IndexUnusable, analyze

    path = tmp_path / "index.scip"
    path.write_bytes(metadata("file:///somewhere/else")
                     + document("src/thing/core.py", occurrence(1, 0, 3, SYMBOL)))

    elsewhere = tmp_path / "a-different-project"
    elsewhere.mkdir()
    with pytest.raises(IndexUnusable) as caught:
        analyze([elsewhere], path)
    message = str(caught.value)
    assert "/somewhere/else" in message
    assert str(elsewhere) in message


def tool_info(name: str) -> bytes:
    return delimited(1, delimited(2, delimited(1, name.encode())))


def test_the_tool_that_built_the_index_is_recorded(tmp_path):
    """Named in the re-index instruction, so the advice is a command the reader
    can run rather than "rebuild it somehow"."""
    path = tmp_path / "index.scip"
    path.write_bytes(tool_info("scip-python")
                     + document("m.py", occurrence(1, 0, 3, SYMBOL)))
    assert scip.read(path).tool == "scip-python"


def test_an_index_that_does_not_name_its_tool_says_nothing(tmp_path):
    path = tmp_path / "index.scip"
    path.write_bytes(document("m.py", occurrence(1, 0, 3, SYMBOL)))
    assert scip.read(path).tool is None


def relationship(target: str, *, implementation: bool) -> bytes:
    body = delimited(1, target.encode())
    if implementation:
        body += tag(3, 0) + varint(1)
    return delimited(4, body)


def symbol_information(symbol: str, *relationships: bytes) -> bytes:
    return delimited(3, delimited(1, symbol.encode()) + b"".join(relationships))


IFACE = "scip-go gomod m v `pkg`/renderer#flush."
IMPL = "scip-go gomod m v `pkg`/nilRenderer#flush()."


def test_which_types_satisfy_an_interface_is_read(tmp_path):
    """The one thing in an index that reading source cannot reproduce. It takes
    a type checker to know that this method satisfies that interface, and it is
    what turns a call through an interface from a dead end into a fan-out."""
    path = tmp_path / "index.scip"
    path.write_bytes(delimited(2, delimited(1, b"r.go")
                               + symbol_information(IMPL,
                                                    relationship(IFACE, implementation=True))))
    assert scip.read(path).implementations == {IFACE: (IMPL,)}


def test_a_relationship_that_is_not_an_implementation_is_ignored(tmp_path):
    """A Relationship also spells reference, definition and type-definition.
    Only implementation says what might run in place of what."""
    path = tmp_path / "index.scip"
    path.write_bytes(delimited(2, delimited(1, b"r.go")
                               + symbol_information(IMPL,
                                                    relationship(IFACE, implementation=False))))
    assert scip.read(path).implementations == {}


def test_documentation_is_not_mistaken_for_a_relationship(tmp_path):
    """Documentation is field 3 and relationships field 4. Reading 3 as the
    relationship list finds prose where symbols should be, and silently
    reports that nothing implements anything."""
    path = tmp_path / "index.scip"
    prose = delimited(3, delimited(1, IMPL.encode())
                      + delimited(3, b"nilRenderer implements renderer."))
    path.write_bytes(delimited(2, delimited(1, b"r.go") + prose))
    assert scip.read(path).implementations == {}


# -- the graph this reader hands on -------------------------------------
def test_a_call_at_module_scope_leaves_no_edge(tmp_path):
    """A call written at module scope encloses to the module, and a module
    does not call anything - the interpreter runs it on import. Drawing it
    would break the invariant that an edge starts at a callable, which is
    exactly what went unnoticed while this path never validated its graph.
    """
    # `helper()` is called from inside main - a real edge, so the assertions
    # below are not passing on an empty graph - and again at module scope,
    # where there is no caller to leave.
    helper = SYMBOL.replace("run()", "helper()")
    (tmp_path / "run.py").write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "\n"
        "def main():\n"
        "    helper()\n"
        "\n"
        "\n"
        "helper()\n"
    )
    index = tmp_path / "index.scip"
    index.write_bytes(
        metadata(f"file://{tmp_path}")
        + document("run.py",
                   occurrence(0, 4, 10, helper, roles=1),   # def helper
                   occurrence(4, 4, 8, SYMBOL, roles=1),    # def main
                   occurrence(5, 4, 10, helper),            # called inside main
                   occurrence(8, 0, 6, helper))             # called at module scope
    )

    from codecards.scip import analyze
    graph, _ = analyze(roots=[tmp_path], index_path=index, embed_source=False)

    # Ids come from the index's symbols, not the file name.
    validate(graph)
    assert [(e.source, e.target) for e in graph.edges] == [
        ("pkg.mod.Thing.run", "pkg.mod.Thing.helper")
    ]
    for edge in graph.edges:
        assert graph.nodes[edge.source].kind in CALLABLE_KINDS


def test_a_recursive_call_is_an_edge_like_any_other(tmp_path):
    """A function calling itself was discarded outright, and not counted
    either, so it vanished from the totals as well as the graph. Collapse
    already folds a self-edge into the card's internal count - the badge that
    says "calls that stay inside" - so nothing downstream needed a change;
    this path simply never let one through. Ten of them on this project's own
    source, which is the only respect in which an index lost to reading the
    source directly.
    """
    from codecards.scip import analyze

    walk = SYMBOL.replace("run()", "walk()")
    (tmp_path / "run.py").write_text(
        "def walk(n):\n"
        "    if n:\n"
        "        walk(n - 1)\n"
        "    return n\n"
    )
    index = tmp_path / "index.scip"
    index.write_bytes(
        metadata(f"file://{tmp_path}")
        + document("run.py",
                   occurrence(0, 4, 8, walk, roles=1),   # def walk
                   occurrence(2, 8, 12, walk))           # walk(n - 1)
    )
    graph, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    validate(graph)
    assert [(e.source, e.target) for e in graph.edges] == [
        ("pkg.mod.Thing.walk", "pkg.mod.Thing.walk")
    ]
    assert report.by_confidence.get("resolved") == 1
