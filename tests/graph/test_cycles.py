"""Finding rings of calls, which the finished layout can never show.

ELK reverses edges until the graph is acyclic, so by the time there are
coordinates every line points downward and the ring has been laid out away.
These read the graph instead.
"""

from __future__ import annotations

from codecards.graph.cycles import in_a_cycle, strongly_connected
from codecards.graph.model import CodeGraph, Confidence, Edge, Node, NodeKind


def graph_of(*pairs: tuple[str, str]) -> CodeGraph:
    names = {n for pair in pairs for n in pair}
    nodes = {
        n: Node(id=n, kind=NodeKind.FUNCTION, name=n, parent=None)
        for n in sorted(names)
    }
    edges = [
        Edge(source=s, target=t, confidence=Confidence.RESOLVED)
        for s, t in pairs
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def test_a_straight_chain_has_no_ring():
    assert strongly_connected(graph_of(("a", "b"), ("b", "c"))) == []
    assert in_a_cycle(graph_of(("a", "b"), ("b", "c"))) == set()


def test_two_functions_calling_each_other_are_a_ring():
    assert strongly_connected(graph_of(("a", "b"), ("b", "a"))) == [["a", "b"]]


def test_a_ring_of_three_is_found_without_looking_for_a_mutual_pair():
    """The reason this is a component search rather than a check for pairs:
    a -> b -> c -> a has no two functions that call each other."""
    rings = strongly_connected(graph_of(("a", "b"), ("b", "c"), ("c", "a")))
    assert rings == [["a", "b", "c"]]


def test_only_the_edges_inside_the_ring_are_marked():
    """An edge leading into or out of a cycle is not itself circular."""
    graph = graph_of(("start", "a"), ("a", "b"), ("b", "a"), ("b", "end"))
    assert in_a_cycle(graph) == {("a", "b"), ("b", "a")}


def test_a_function_calling_itself_counts():
    """The shortest cycle there is, and the one case a component search cannot
    see: a single node is never a component of more than one."""
    assert in_a_cycle(graph_of(("a", "a"))) == {("a", "a")}


def test_two_separate_rings_are_reported_separately():
    graph = graph_of(("a", "b"), ("b", "a"), ("x", "y"), ("y", "x"))
    assert strongly_connected(graph) == [["a", "b"], ["x", "y"]]


def test_a_deep_chain_does_not_exhaust_the_stack():
    """Written iteratively for this. A crash while drawing somebody's
    codebase is a poor way to report that it has a cycle."""
    pairs = [(f"n{i}", f"n{i + 1}") for i in range(3000)]
    pairs.append(("n3000", "n0"))
    rings = strongly_connected(graph_of(*pairs))
    assert len(rings) == 1
    assert len(rings[0]) == 3001
