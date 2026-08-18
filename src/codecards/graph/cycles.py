"""Which calls take part in a cycle.

Layout puts a caller above its callee, so a ring of calls has no consistent
top-to-bottom order and something has to give. ELK resolves that by reversing
edges until the graph is acyclic, which means the finished picture never shows
one: by the time coordinates exist, every line points politely downward and
the fact that a ring was there has been laid out away.

So this is computed from the graph rather than read off the drawing. An edge
belongs to a cycle exactly when its two ends lie in the same strongly
connected component, which catches a ring of any length - `a` calling `b`
calling `c` calling `a` is found without looking for a mutual pair.

Rare enough to be worth saying: 0.9% of the edges in this project's own graph,
3.2% of bubbletea's.
"""

from __future__ import annotations

from .model import CodeGraph


def strongly_connected(graph: CodeGraph) -> list[list[str]]:
    """Every group of nodes that can all reach each other, largest first.

    Tarjan's algorithm, written iteratively. A graph deep enough to matter is
    deep enough to exhaust the interpreter's stack, and a crash while drawing
    somebody's codebase is a poor way to report a cycle.
    """
    out: dict[str, list[str]] = {}
    for edge in graph.edges:
        out.setdefault(edge.source, []).append(edge.target)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = 0

    for root in graph.nodes:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child = work[-1]
            if child == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)

            descended = False
            children = out.get(node, ())
            for position in range(child, len(children)):
                target = children[position]
                if target not in index:
                    work[-1] = (node, position + 1)
                    work.append((target, 0))
                    descended = True
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            if descended:
                continue

            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                found.append(sorted(component))

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    return sorted((c for c in found if len(c) > 1), key=len, reverse=True)


def in_a_cycle(graph: CodeGraph) -> set[tuple[str, str]]:
    """The (source, target) pairs that take part in one.

    A self-call counts. A function that calls itself is the shortest cycle
    there is, and it is the one case the component test cannot see, since a
    single node is never a component of more than one.
    """
    rings = strongly_connected(graph)
    members = {node for ring in rings for node in ring}
    marked = {
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.source in members and edge.target in members
    }
    marked.update(
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.source == edge.target
    )
    return marked
