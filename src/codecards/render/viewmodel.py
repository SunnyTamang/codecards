"""Flatten a CodeGraph into the JSON payload the page consumes.

Two fields exist specifically to keep the browser honest: `initialView` and
`goldenTrace` are computed here by the reference Python implementations, and
the Playwright test asserts the JS reproduces them exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import __version__
from ..graph.collapse import collapse, default_collapsed
from ..graph.cycles import in_a_cycle, strongly_connected
from ..graph.entrypoints import detect_entry_points
from ..graph.model import CodeGraph, EntryReason
from ..graph.walkthrough import build_walkthrough
from ..report import AnalysisReport


def build_viewmodel(
    graph: CodeGraph, report: AnalysisReport, *, max_depth: int = 15
) -> dict:
    collapsed = default_collapsed(graph)
    view = collapse(graph, collapsed)
    entry_points = detect_entry_points(graph)

    golden = None
    if entry_points:
        entry_id = entry_points[0].node_id
        steps = build_walkthrough(graph, entry_id, max_depth=max_depth)
        golden = {"entryId": entry_id, "steps": [_step(s) for s in steps]}

    nodes = [_node(n) for n in graph.nodes.values()]
    # Computed here rather than read off the drawing: layout reverses edges
    # until the graph is acyclic, so by the time coordinates exist no line
    # runs backwards and the ring has been laid out away.
    circular = in_a_cycle(graph)
    rings = strongly_connected(graph)
    return {
        "meta": {
            "version": __version__,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "maxDepth": max_depth,
            "hasSource": any("source" in n for n in nodes),
        },
        "nodes": nodes,
        "edges": [_edge(e, circular) for e in graph.edges],
        "entryPoints": [
            {"id": e.node_id, "reasons": [r.value for r in e.reasons]}
            for e in entry_points
        ],
        "orphans": _orphans(entry_points),
        "initialView": {
            "collapsed": sorted(collapsed),
            "visible": list(view.visible),
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "confidence": e.confidence.value,
                    "weight": e.weight,
                    "tiers": e.tiers,
                }
                for e in view.edges
            ],
            "internalCounts": view.internal_counts,
        },
        "goldenTrace": golden,
        "stats": {
            "totalCalls": report.total_calls,
            "byConfidence": report.by_confidence,
            "resolutionRate": round(report.resolution_rate, 4),
            "callableCount": report.callable_count,
            "edgeCount": report.edge_count,
            "skipped": [{"path": s.path, "reason": s.reason} for s in report.skipped],
            # Rare and load-bearing: one ring in this project's own graph,
            # eight in bubbletea's. Named rather than merely counted, since
            # "there is a cycle somewhere" only sends the reader hunting.
            "cycles": [list(r) for r in rings],
            # Carried into the page so the caveat travels with the artefact.
            # A graph gets shared, opened weeks later, and shown to people who
            # never saw the terminal that built it.
            "stale": list(report.stale),
            "reindexCommand": report.reindex_command,
        },
    }


def _orphans(entry_points) -> list[str]:
    """Callables whose only claim to being an entry point is that nothing
    calls them.

    Entry-point detection already computes this: in-degree zero is the
    structural fallback, so an orphan arrives here as an EntryPoint carrying
    NO_CALLERS and nothing else. A function that is also decorated, or a
    console script, or a test has a real reason to exist without callers and
    is the front door rather than dead code.

    Excluding every entry point instead would exclude precisely the set this
    is meant to return.
    """
    return sorted(
        entry.node_id
        for entry in entry_points
        if set(entry.reasons) == {EntryReason.NO_CALLERS}
    )


def _node(node) -> dict:
    payload = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "parent": node.parent,
        "file": node.location.file if node.location else None,
        "lineStart": node.location.line_start if node.location else None,
        "lineEnd": node.location.line_end if node.location else None,
        "signature": node.signature,
        "summary": node.summary,
        "decorators": list(node.decorators),
    }
    if node.implicitly_called:
        payload["implicit"] = True
    if node.is_dunder:
        payload["dunder"] = True
    if node.source is not None:
        payload["source"] = node.source
        payload["tokens"] = [[list(run) for run in line]
                             for line in (node.source_tokens or ())]
        if node.source_truncated:
            payload["truncated"] = True
    return payload


def _edge(edge, circular) -> dict:
    payload = {
        "source": edge.source,
        "target": edge.target,
        "confidence": edge.confidence.value,
        "sites": [
            {"line": s.line, "cond": s.in_conditional, "loop": s.in_loop}
            for s in edge.call_sites
        ],
    }
    # Only on the few that are, so the common case costs no bytes in a file
    # that is already megabytes of embedded source.
    if (edge.source, edge.target) in circular:
        payload["circular"] = True
    return payload


def _step(step) -> dict:
    return {
        "index": step.index,
        "callerId": step.caller_id,
        "calleeId": step.callee_id,
        "line": step.line,
        "depth": step.depth,
        "stack": list(step.stack),
        "confidence": step.confidence.value,
        "cond": step.in_conditional,
        "loop": step.in_loop,
        "recursive": step.recursive,
    }
