"""Compute a visible view of a CodeGraph for a given set of collapsed containers.

One algorithm serves every zoom level. The module-level default view is just
this function applied with every module collapsed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    CONFIDENCE_ORDER,
    CONTAINER_KINDS,
    DRAWN,
    CodeGraph,
    Confidence,
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
    """The opening view: the top level of containers, everything below shut.

    Two rules that look reasonable both fail badly on real projects.

    Collapsing anything that holds a callable sounds right, but a package
    whose `__init__.py` defines a single function then collapses the whole
    package: scikit-learn opens as one card with its 47 subpackages hidden
    behind one loose helper.

    Collapsing only what has no container children fails the other way, and
    opens scikit-learn on 2,964 cards, 1,714 of them loose functions.

    So the view descends from the roots to the first level that holds more
    than one container, and shuts everything from there down. That is the
    project's top-level structure, which is what someone opening an
    unfamiliar codebase wants first. It descends through single-container
    chains so a project that is one package does not open on one card.
    """
    children: dict[str, list] = {}
    for node in graph.nodes.values():
        if node.parent is not None:
            children.setdefault(node.parent, []).append(node)

    def containers(nodes):
        return [n for n in nodes if n.kind in CONTAINER_KINDS]

    level = [n for n in graph.nodes.values() if n.parent is None]
    while True:
        here = containers(level)
        if len(here) != 1:
            break
        below = children.get(here[0].id, [])
        if not containers(below):
            break
        level = below

    frontier = [n.id for n in containers(level)]
    collapsed = set(frontier)
    stack = list(frontier)
    while stack:
        for child in children.get(stack.pop(), []):
            if child.kind in CONTAINER_KINDS and child.id not in collapsed:
                collapsed.add(child.id)
                stack.append(child.id)
    return collapsed

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
        # The weakest tier present, not the strongest. Folding a module up
        # used to let one certain call speak for every uncertain one beneath
        # it: on this project's own structural graph, 14 of 24 module edges
        # drew as resolved while at least half of what they stood for was a
        # guess, and one was 90% guesses. An edge that contains a guess has
        # to look like it contains a guess.
        #
        # It costs nothing in visibility. Which tiers are drawn is decided on
        # the individual calls before they are merged, so styling the
        # aggregate more cautiously cannot make a real dependency disappear.
        weakest = next(
            c for c in reversed(CONFIDENCE_ORDER) if c.value in tiers
        )
        view_edges.append(
            ViewEdge(
                source=source,
                target=target,
                confidence=weakest,
                weight=sum(tiers.values()),
                tiers=dict(sorted(tiers.items())),
            )
        )

    return ViewGraph(
        visible=visible,
        edges=tuple(view_edges),
        internal_counts=dict(sorted(internal.items())),
    )
