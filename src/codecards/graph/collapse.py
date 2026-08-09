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
    """The opening view: no callables on the canvas, only containers.

    Collapsing every module is not enough. A package's `__init__.py`
    definitions are parented to the package node, not to a module, so
    "collapse every module" left them on the canvas: analysing codecards put
    twelve loose functions from `extract/__init__.py` into what is meant to be
    a module map, which is the hairball this view exists to prevent.

    So the rule is every module, plus anything else that directly contains a
    callable. Modules stay uniformly collapsed, which is what makes the view a
    module map; a package whose `__init__.py` carries logic collapses too and
    becomes one card until it is opened; a package containing only modules
    stays open, so the modules inside it are visible.
    """
    holds_a_callable = {
        node.parent for node in graph.callables() if node.parent is not None
    }
    return {
        n.id for n in graph.nodes.values()
        if n.kind is NodeKind.MODULE or n.id in holds_a_callable
    }


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
