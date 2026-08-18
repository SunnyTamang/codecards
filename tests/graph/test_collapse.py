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


def test_a_merged_edge_is_drawn_at_its_weakest_call():
    """One certain call must not speak for the uncertain ones beside it.

    This fixture is the argument in miniature: two resolved calls and one
    ambiguous, which used to draw solid. On this project's own structural
    graph the same rule made 14 of 24 module edges claim certainty while at
    least half of what each stood for was a guess, one of them 90%.

    Nothing is hidden by being cautious here. Which tiers get drawn is
    decided on the individual calls before they are merged, so a real
    dependency cannot vanish because its aggregate is styled carefully.
    """
    edge = collapse(build(), collapsed={"app.mail"}).edges[0]
    assert edge.confidence is Confidence.AMBIGUOUS
    # and the count survives, so the panel can say how much is a guess
    assert edge.tiers == {"resolved": 2, "ambiguous": 1}


def test_an_edge_of_one_tier_is_unchanged():
    """The common case, and the whole of any graph a language server built:
    a server either knows or says nothing, so nothing it produces mixes."""
    graph = build()
    graph.edges = [e for e in graph.edges if e.confidence is Confidence.RESOLVED]
    edge = collapse(graph, collapsed={"app.mail"}).edges[0]
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


def test_the_opening_view_contains_no_callables():
    """The point of the opening view. A package's __init__.py definitions are
    parented to the package, not to a module, so collapsing only modules left
    them loose on the canvas."""
    graph = build()
    view = collapse(graph, default_collapsed(graph))
    kinds = {graph.nodes[i].kind for i in view.visible}
    assert not (kinds & {NodeKind.FUNCTION, NodeKind.METHOD})


def test_default_collapsed_holds_every_container_of_a_callable():
    """Modules, plus anything else that directly holds a callable. The class
    is included even though its module hides it, so that expanding the module
    reveals one class card rather than every method at once."""
    assert default_collapsed(build()) == {"app.cli", "app.mail", "app.mail.H"}


def test_a_package_below_the_top_level_is_collapsed():
    """The concern that motivated this: a package whose __init__.py carries
    logic used to drop its loose functions onto the module map."""
    nodes = [
        Node(id="app", kind=NodeKind.PACKAGE, name="app"),
        Node(id="app.one", kind=NodeKind.PACKAGE, name="one", parent="app"),
        Node(id="app.one.helper", kind=NodeKind.FUNCTION, name="helper", parent="app.one"),
        Node(id="app.one.sub", kind=NodeKind.MODULE, name="sub", parent="app.one"),
        Node(id="app.two", kind=NodeKind.MODULE, name="two", parent="app"),
    ]
    graph = CodeGraph(nodes={n.id: n for n in nodes}, edges=[])
    visible = collapse(graph, default_collapsed(graph)).visible
    assert "app.one" in visible
    assert "app.one.helper" not in visible
    assert "app.one.sub" not in visible


def test_the_view_descends_through_single_container_chains():
    """A project that is one package must not open on one card."""
    nodes = [
        Node(id="pkg", kind=NodeKind.PACKAGE, name="pkg"),
        Node(id="pkg.a", kind=NodeKind.MODULE, name="a", parent="pkg"),
        Node(id="pkg.b", kind=NodeKind.MODULE, name="b", parent="pkg"),
    ]
    graph = CodeGraph(nodes={n.id: n for n in nodes}, edges=[])
    visible = collapse(graph, default_collapsed(graph)).visible
    assert {"pkg", "pkg.a", "pkg.b"} <= set(visible)


def test_one_loose_function_does_not_hide_the_whole_project():
    """scikit-learn's root defines 47 subpackages and one helper. Collapsing
    the root to hide that helper hid all 47 and opened the page on a single
    card."""
    nodes = [Node(id="root", kind=NodeKind.PACKAGE, name="root"),
             Node(id="root.helper", kind=NodeKind.FUNCTION, name="helper", parent="root")]
    for i in range(20):
        nodes.append(Node(id=f"root.p{i}", kind=NodeKind.PACKAGE, name=f"p{i}", parent="root"))
        nodes.append(Node(id=f"root.p{i}.m", kind=NodeKind.MODULE, name="m", parent=f"root.p{i}"))
    graph = CodeGraph(nodes={n.id: n for n in nodes}, edges=[])
    visible = collapse(graph, default_collapsed(graph)).visible
    assert len([i for i in visible if graph.nodes[i].kind is NodeKind.PACKAGE]) == 21
    assert "root.p0.m" not in visible

def test_view_edges_are_deterministically_ordered():
    a = collapse(build(), collapsed={"app.mail"})
    b = collapse(build(), collapsed={"app.mail"})
    assert a.edges == b.edges
    assert isinstance(a.edges[0], ViewEdge)
