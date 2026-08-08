from __future__ import annotations

from codecards.graph.entrypoints import EntryPoint, detect_entry_points
from codecards.graph.model import (
    CallSite,
    CodeGraph,
    Confidence,
    Edge,
    EntryHint,
    EntryReason,
    Node,
    NodeKind,
)


def fn(node_id: str) -> Node:
    return Node(id=node_id, kind=NodeKind.FUNCTION, name=node_id.rsplit(".", 1)[-1], parent="m")


def build(edges=None, hints=None) -> CodeGraph:
    nodes = [Node(id="m", kind=NodeKind.MODULE, name="m")]
    nodes += [fn("m.main"), fn("m.helper"), fn("m.orphan")]
    return CodeGraph(
        nodes={n.id: n for n in nodes},
        edges=list(edges or []),
        entry_hints=list(hints or []),
    )


def test_callable_with_no_callers_is_an_entry_point():
    g = build(edges=[Edge("m.main", "m.helper", Confidence.RESOLVED, (CallSite(1),))])
    ids = {e.node_id for e in detect_entry_points(g)}
    assert "m.main" in ids
    assert "m.orphan" in ids
    assert "m.helper" not in ids


def test_hints_are_merged_onto_the_same_node():
    g = build(
        edges=[Edge("m.helper", "m.main", Confidence.RESOLVED, (CallSite(1),))],
        hints=[
            EntryHint("m.main", EntryReason.MAIN_BLOCK),
            EntryHint("m.main", EntryReason.CONSOLE_SCRIPT),
        ],
    )
    entry = next(e for e in detect_entry_points(g) if e.node_id == "m.main")
    assert entry.reasons == (EntryReason.MAIN_BLOCK, EntryReason.CONSOLE_SCRIPT)


def test_a_hinted_node_that_has_callers_is_still_an_entry_point():
    g = build(
        edges=[Edge("m.helper", "m.main", Confidence.RESOLVED, (CallSite(1),))],
        hints=[EntryHint("m.main", EntryReason.MAIN_BLOCK)],
    )
    assert "m.main" in {e.node_id for e in detect_entry_points(g)}


def test_no_callers_is_not_added_when_a_stronger_reason_exists():
    g = build(hints=[EntryHint("m.orphan", EntryReason.TEST)])
    entry = next(e for e in detect_entry_points(g) if e.node_id == "m.orphan")
    assert entry.reasons == (EntryReason.TEST,)


def test_undrawn_edges_do_not_count_as_callers():
    g = build(edges=[Edge("m.main", "m.helper", Confidence.UNRESOLVED, (CallSite(1),))])
    assert "m.helper" in {e.node_id for e in detect_entry_points(g)}


def test_hints_for_unknown_nodes_are_ignored():
    g = build(hints=[EntryHint("m.ghost", EntryReason.MAIN_BLOCK)])
    assert "m.ghost" not in {e.node_id for e in detect_entry_points(g)}


def test_ordering_is_by_reason_priority_then_id():
    g = build(
        edges=[Edge("m.helper", "m.main", Confidence.RESOLVED, (CallSite(1),))],
        hints=[EntryHint("m.main", EntryReason.MAIN_BLOCK)],
    )
    result = detect_entry_points(g)
    assert isinstance(result[0], EntryPoint)
    assert result[0].node_id == "m.main"  # main_block outranks no_callers
    assert [e.node_id for e in result[1:]] == ["m.helper", "m.orphan"]


def test_containers_are_never_entry_points():
    g = build()
    assert "m" not in {e.node_id for e in detect_entry_points(g)}
