from __future__ import annotations

from codecards.graph.model import (
    CallSite,
    CodeGraph,
    Confidence,
    Edge,
    Node,
    NodeKind,
)
from codecards.graph.walkthrough import Step, build_walkthrough


def fn(node_id: str) -> Node:
    return Node(id=node_id, kind=NodeKind.FUNCTION, name=node_id.rsplit(".", 1)[-1], parent="m")


def graph_of(edges: list[Edge], extra: list[str] = ()) -> CodeGraph:
    ids = {"m.main"} | {e.source for e in edges} | {e.target for e in edges} | set(extra)
    nodes = [Node(id="m", kind=NodeKind.MODULE, name="m")] + [fn(i) for i in sorted(ids)]
    return CodeGraph(nodes={n.id: n for n in nodes}, edges=edges)


def test_calls_are_ordered_by_call_site_line():
    g = graph_of([
        Edge("m.main", "m.b", Confidence.RESOLVED, (CallSite(20),)),
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10),)),
    ])
    steps = build_walkthrough(g, "m.main")
    assert [s.callee_id for s in steps] == ["m.a", "m.b"]
    assert [s.index for s in steps] == [0, 1]


def test_traversal_is_depth_first():
    g = graph_of([
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10),)),
        Edge("m.a", "m.deep", Confidence.RESOLVED, (CallSite(50),)),
        Edge("m.main", "m.b", Confidence.RESOLVED, (CallSite(20),)),
    ])
    assert [s.callee_id for s in build_walkthrough(g, "m.main")] == ["m.a", "m.deep", "m.b"]


def test_each_call_site_produces_its_own_step():
    g = graph_of([Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10), CallSite(30)))])
    steps = build_walkthrough(g, "m.main")
    assert [s.line for s in steps] == [10, 30]


def test_stack_and_depth_track_the_call_chain():
    g = graph_of([
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10),)),
        Edge("m.a", "m.deep", Confidence.RESOLVED, (CallSite(50),)),
    ])
    deep = build_walkthrough(g, "m.main")[1]
    assert deep.stack == ("m.main", "m.a")
    assert deep.depth == 1


def test_recursion_is_marked_and_not_re_entered():
    g = graph_of([Edge("m.main", "m.main", Confidence.RESOLVED, (CallSite(5),))])
    steps = build_walkthrough(g, "m.main")
    assert len(steps) == 1
    assert steps[0].recursive is True


def test_cycles_terminate():
    g = graph_of([
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10),)),
        Edge("m.a", "m.main", Confidence.RESOLVED, (CallSite(20),)),
    ])
    steps = build_walkthrough(g, "m.main")
    assert [s.callee_id for s in steps] == ["m.a", "m.main"]
    assert steps[1].recursive is True


def test_max_depth_stops_descent():
    g = graph_of([
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(1),)),
        Edge("m.a", "m.b", Confidence.RESOLVED, (CallSite(2),)),
        Edge("m.b", "m.c", Confidence.RESOLVED, (CallSite(3),)),
    ])
    assert [s.callee_id for s in build_walkthrough(g, "m.main", max_depth=1)] == ["m.a", "m.b"]


def test_conditional_and_loop_flags_carry_through():
    g = graph_of([
        Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10, in_conditional=True),)),
        Edge("m.main", "m.b", Confidence.RESOLVED, (CallSite(20, in_loop=True),)),
    ])
    steps = build_walkthrough(g, "m.main")
    assert steps[0].in_conditional is True and steps[0].in_loop is False
    assert steps[1].in_loop is True


def test_undrawn_tiers_are_skipped():
    g = graph_of([Edge("m.main", "m.a", Confidence.UNRESOLVED, (CallSite(10),))])
    assert build_walkthrough(g, "m.main") == []


def test_unknown_entry_returns_no_steps():
    assert build_walkthrough(graph_of([]), "m.ghost") == []


def test_steps_are_step_instances():
    g = graph_of([Edge("m.main", "m.a", Confidence.RESOLVED, (CallSite(10),))])
    assert isinstance(build_walkthrough(g, "m.main")[0], Step)
