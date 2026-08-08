"""Compute a visible view of a CodeGraph for a given set of collapsed containers.

One algorithm serves every zoom level. The module-level default view is just
this function applied with every module collapsed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    CONFIDENCE_ORDER,
    DRAWN,
    CodeGraph,
    Confidence,
    NodeKind,
)


@dataclass(frozen=True)
class ViewEdge:
    source: str
    target: str
    confidence: Confidence  # strongest tier present among merged call sites
    weight: int  # total call sites merged into this edge
    tiers: dict[str, int]  # confidence value -> call-site count

    def __eq__(self, other: object) -> bool:  # dict field breaks the frozen default
        if not isinstance(other, ViewEdge):
            return NotImplemented
        return (
            self.source == other.source
            and self.target == other.target
            and self.confidence == other.confidence
            and self.weight == other.weight
            and self.tiers == other.tiers
        )

    def __hash__(self) -> int:
        return hash((self.source, self.target, self.confidence, self.weight))


@dataclass(frozen=True)
class ViewGraph:
    visible: tuple[str, ...]
    edges: tuple[ViewEdge, ...]
    internal_counts: dict[str, int]


def visible_representative(graph: CodeGraph, node_id: str, collapsed: set[str]) -> str:
    """The card a node is drawn as: its outermost collapsed ancestor, else itself."""
    representative = node_id
    for ancestor in graph.ancestors(node_id):
        if ancestor in collapsed:
            representative = ancestor
    return representative


def is_visible(graph: CodeGraph, node_id: str, collapsed: set[str]) -> bool:
    """A node is visible when no ancestor is collapsed. Collapsed nodes are visible."""
    return not any(a in collapsed for a in graph.ancestors(node_id))


def default_collapsed(graph: CodeGraph) -> set[str]:
    """The opening view: every module collapsed into a single card."""
    return {n.id for n in graph.nodes.values() if n.kind is NodeKind.MODULE}


def collapse(graph: CodeGraph, collapsed: set[str]) -> ViewGraph:
    visible = tuple(
        node_id for node_id in graph.nodes if is_visible(graph, node_id, collapsed)
    )

    merged: dict[tuple[str, str], dict[str, int]] = {}
    internal: dict[str, int] = {}

    for edge in graph.edges:
        if edge.confidence not in DRAWN:
            continue
        count = len(edge.call_sites) or 1
        source = visible_representative(graph, edge.source, collapsed)
        target = visible_representative(graph, edge.target, collapsed)
        if source == target:
            internal[source] = internal.get(source, 0) + count
            continue
        tiers = merged.setdefault((source, target), {})
        tiers[edge.confidence.value] = tiers.get(edge.confidence.value, 0) + count

    view_edges = []
    for (source, target), tiers in sorted(merged.items()):
        best = next(c for c in CONFIDENCE_ORDER if c.value in tiers)
        view_edges.append(
            ViewEdge(
                source=source,
                target=target,
                confidence=best,
                weight=sum(tiers.values()),
                tiers=dict(sorted(tiers.items())),
            )
        )

    return ViewGraph(
        visible=visible,
        edges=tuple(view_edges),
        internal_counts=dict(sorted(internal.items())),
    )
