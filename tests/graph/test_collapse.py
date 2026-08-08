from __future__ import annotations

from codecards.graph.collapse import (
    ViewEdge,
    collapse,
    default_collapsed,
    visible_representative,
)
from codecards.graph.model import CallSite, CodeGraph, Confidence, Edge, Node, NodeKind


def build() -> CodeGraph:
    """app package, two modules, one class - a small but complete shape."""
    nodes = [
        Node(id="app", kind=NodeKind.PACKAGE, name="app"),
        Node(id="app.cli", kind=NodeKind.MODULE, name="cli", parent="app"),
        Node(id="app.cli.main", kind=NodeKind.FUNCTION, name="main", parent="app.cli"),
        Node(id="app.mail", kind=NodeKind.MODULE, name="mail", parent="app"),
        Node(id="app.mail.H", kind=NodeKind.CLASS, name="H", parent="app.mail"),
        Node(id="app.mail.H.send", kind=NodeKind.METHOD, name="send", parent="app.mail.H"),
        Node(id="app.mail.H.retry", kind=NodeKind.METHOD, name="retry", parent="app.mail.H"),
    ]
    edges = [
        Edge("app.cli.main", "app.mail.H.send", Confidence.RESOLVED, (CallSite(10), CallSite(11))),
        Edge("app.cli.main", "app.mail.H.retry", Confidence.AMBIGUOUS, (CallSite(12),)),
        Edge("app.mail.H.send", "app.mail.H.retry", Confidence.RESOLVED, (CallSite(30),)),
    ]
    return CodeGraph(nodes={n.id: n for n in nodes}, edges=edges)


def test_nothing_collapsed_keeps_every_edge():
    view = collapse(build(), collapsed=set())
    assert len(view.edges) == 3
    assert view.internal_counts == {}
    assert "app.mail.H.send" in view.visible


def test_collapsing_a_module_repoints_edges_to_it():
    view = collapse(build(), collapsed={"app.mail"})
    targets = {(e.source, e.target) for e in view.edges}
    assert targets == {("app.cli.main", "app.mail")}
    assert "app.mail.H.send" not in view.visible
    assert "app.mail" in view.visible


def test_parallel_edges_merge_with_weight_and_tiers():
    view = collapse(build(), collapsed={"app.mail"})
    edge = view.edges[0]
    # 2 resolved call sites + 1 ambiguous call site, all now main -> app.mail
    assert edge.weight == 3
    assert edge.tiers == {"resolved": 2, "ambiguous": 1}
    # Strongest tier present wins, because at least one certain call exists.
    assert edge.confidence is Confidence.RESOLVED


def test_internal_edges_become_a_count_not_an_edge():
    view = collapse(build(), collapsed={"app.mail"})
    assert view.internal_counts == {"app.mail": 1}
    assert all(e.source != e.target for e in view.edges)


def test_outermost_collapsed_ancestor_wins():
    graph = build()
    rep = visible_representative(graph, "app.mail.H.send", {"app", "app.mail"})
    assert rep == "app"


def test_undrawn_tiers_are_excluded():
    graph = build()
    graph.edges.append(
        Edge("app.cli.main", "app.mail.H.send", Confidence.UNRESOLVED, (CallSite(99),))
    )
    view = collapse(graph, collapsed=set())
    assert all(e.confidence is not Confidence.UNRESOLVED for e in view.edges)


def test_default_collapsed_is_every_module():
    assert default_collapsed(build()) == {"app.cli", "app.mail"}


def test_view_edges_are_deterministically_ordered():
    a = collapse(build(), collapsed={"app.mail"})
    b = collapse(build(), collapsed={"app.mail"})
    assert a.edges == b.edges
    assert isinstance(a.edges[0], ViewEdge)
