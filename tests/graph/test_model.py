from __future__ import annotations

import pytest

from codecards.graph.model import (
    CodeGraph,
    Confidence,
    Edge,
    GraphInvariantError,
    Location,
    Node,
    NodeKind,
    validate,
)


def fn(node_id: str, parent: str | None = None) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=node_id.rsplit(".", 1)[-1],
        parent=parent,
        location=Location(file="a.py", line_start=1, line_end=2),
    )


def mod(node_id: str, parent: str | None = None) -> Node:
    return Node(id=node_id, kind=NodeKind.MODULE, name=node_id.rsplit(".", 1)[-1], parent=parent)


def cls(node_id: str, parent: str | None = None) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.CLASS,
        name=node_id.rsplit(".", 1)[-1],
        parent=parent,
        location=Location(file="a.py", line_start=1, line_end=2),
    )


def graph_of(*nodes: Node, edges: list[Edge] | None = None) -> CodeGraph:
    return CodeGraph(nodes={n.id: n for n in nodes}, edges=list(edges or []))


def test_valid_graph_passes():
    g = graph_of(
        mod("app"),
        fn("app.a", parent="app"),
        fn("app.b", parent="app"),
        edges=[Edge(source="app.a", target="app.b", confidence=Confidence.RESOLVED)],
    )
    validate(g)


def test_missing_parent_is_rejected():
    g = graph_of(fn("app.a", parent="app"))
    with pytest.raises(GraphInvariantError, match="unknown parent"):
        validate(g)


def test_containment_cycle_is_rejected():
    a = Node(id="a", kind=NodeKind.MODULE, name="a", parent="b")
    b = Node(id="b", kind=NodeKind.MODULE, name="b", parent="a")
    with pytest.raises(GraphInvariantError, match="cycle"):
        validate(graph_of(a, b))


def test_edge_to_missing_node_is_rejected():
    g = graph_of(mod("app"), fn("app.a", parent="app"),
                 edges=[Edge(source="app.a", target="app.ghost", confidence=Confidence.RESOLVED)])
    with pytest.raises(GraphInvariantError, match="unknown node"):
        validate(g)


def test_edge_to_a_module_is_rejected():
    g = graph_of(mod("app"), fn("app.a", parent="app"),
                 edges=[Edge(source="app.a", target="app", confidence=Confidence.RESOLVED)])
    with pytest.raises(GraphInvariantError, match="callable"):
        validate(g)


def test_an_edge_may_end_on_a_class():
    """Constructing a class is a call, and a class with no __init__ has nothing
    more specific to point at. Only the target may be one: a class does not
    make calls, its methods do."""
    g = graph_of(mod("app"), fn("app.a", parent="app"), cls("app.H", parent="app"),
                 edges=[Edge(source="app.a", target="app.H", confidence=Confidence.RESOLVED)])
    validate(g)


def test_an_edge_may_not_start_at_a_class():
    g = graph_of(mod("app"), fn("app.a", parent="app"), cls("app.H", parent="app"),
                 edges=[Edge(source="app.H", target="app.a", confidence=Confidence.RESOLVED)])
    with pytest.raises(GraphInvariantError, match="callable"):
        validate(g)


def test_ancestors_are_nearest_first():
    g = graph_of(
        Node(id="app", kind=NodeKind.PACKAGE, name="app"),
        mod("app.m", parent="app"),
        Node(id="app.m.C", kind=NodeKind.CLASS, name="C", parent="app.m"),
        Node(id="app.m.C.go", kind=NodeKind.METHOD, name="go", parent="app.m.C"),
    )
    assert g.ancestors("app.m.C.go") == ["app.m.C", "app.m", "app"]


def test_children_and_callables():
    g = graph_of(mod("app"), fn("app.a", parent="app"), fn("app.b", parent="app"))
    assert [n.id for n in g.children("app")] == ["app.a", "app.b"]
    assert [n.id for n in g.callables()] == ["app.a", "app.b"]
