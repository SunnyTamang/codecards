"""A call through an interface, and what the graph says runs.

The index resolves such a call to the interface's own method - a signature
with nothing behind it. What actually runs is one of the types satisfying it,
and only the index knows which those are.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_go")

from test_index import (
    delimited,
    metadata,
    occurrence,
    relationship,
    symbol_information,
)

from codecards.graph.model import Confidence
from codecards.scip import analyze

PKG = "scip-go gomod m v `pkg`"
IFACE_FLUSH = f"{PKG}/renderer#flush."
NIL_FLUSH = f"{PKG}/nilRenderer#flush()."
REAL_FLUSH = f"{PKG}/realRenderer#flush()."
CALLER = f"{PKG}/Program#run()."

SOURCE = """package pkg

type renderer interface {
	flush() error
}

type nilRenderer struct{}

func (n nilRenderer) flush() error { return nil }

type realRenderer struct{}

func (r realRenderer) flush() error { return nil }

type Program struct{ renderer renderer }

func (p Program) run() {
	p.renderer.flush()
}
"""


def project(tmp_path):
    """Positions come from the parse, not from counting columns by hand.

    A definition is only recorded when its occurrence lands exactly on the
    identifier tree-sitter found, so hand-written coordinates silently produce
    an index that resolves nothing.
    """
    from codecards.parse import syntax
    from codecards.parse.grammars import GO

    source = SOURCE.encode()
    (tmp_path / "r.go").write_text(SOURCE)
    tree = syntax.parse(GO, source)
    at = {d.name if d.kind != "method" else f"{d.name}@{d.line_start}": d
          for d in syntax.definitions(GO, tree, source)}

    # The two `flush` methods and the interface's own, in source order.
    flushes = sorted((d for d in syntax.definitions(GO, tree, source)
                      if d.name == "flush"), key=lambda d: d.line_start)
    iface_flush, nil_flush, real_flush = flushes
    run = at["run@17"]
    call = next(c for c in syntax.call_sites(GO, tree, source) if c.text == "flush")

    def define(definition, symbol):
        return occurrence(definition.name_line, definition.name_char,
                          definition.name_char + len(definition.name), symbol, roles=1)

    index = tmp_path / "index.scip"
    index.write_bytes(
        metadata(f"file://{tmp_path}")
        + delimited(2, delimited(1, b"r.go")
                    + define(iface_flush, IFACE_FLUSH)
                    + define(nil_flush, NIL_FLUSH)
                    + define(real_flush, REAL_FLUSH)
                    + define(run, CALLER)
                    + occurrence(call.name_line, call.name_char,
                                 call.name_char + len(call.text), IFACE_FLUSH)
                    + symbol_information(NIL_FLUSH,
                                         relationship(IFACE_FLUSH, implementation=True))
                    + symbol_information(REAL_FLUSH,
                                         relationship(IFACE_FLUSH, implementation=True)))
    )
    return index


def test_the_call_reaches_every_type_that_could_run(tmp_path):
    graph, _ = analyze(roots=[tmp_path], index_path=project(tmp_path), embed_source=False)
    by_target = {e.target: e for e in graph.edges if e.source == "pkg.Program.run"}

    # The interface's own method: what the call statically names.
    assert by_target["pkg.renderer.flush"].confidence is Confidence.RESOLVED
    # And what might actually run, named rather than guessed.
    assert by_target["pkg.nilRenderer.flush"].confidence is Confidence.AMBIGUOUS
    assert by_target["pkg.realRenderer.flush"].confidence is Confidence.AMBIGUOUS


def test_an_interface_method_is_a_card_so_the_edge_has_somewhere_to_land(tmp_path):
    """Without a node for the interface's own method the call resolves to
    nothing and is dropped, silently losing every polymorphic call."""
    graph, _ = analyze(roots=[tmp_path], index_path=project(tmp_path), embed_source=False)
    assert "pkg.renderer.flush" in graph.nodes


def test_the_fan_out_is_counted_as_ambiguous_in_the_report(tmp_path):
    _, report = analyze(roots=[tmp_path], index_path=project(tmp_path), embed_source=False)
    assert report.by_confidence.get("ambiguous") == 2
