"""Detect where a reader should start tracing.

Syntax-derived hints arrive on graph.entry_hints from the extractor. This
module contributes only the structural fallback - a callable nothing calls -
and merges everything into a stable, ordered list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import CALLABLE_KINDS, DRAWN, CodeGraph, EntryReason

#: Detection reasons in priority order, used for sorting and UI grouping.
REASON_PRIORITY = (
    EntryReason.MAIN_BLOCK,
    EntryReason.CONSOLE_SCRIPT,
    EntryReason.DECORATED,
    EntryReason.TEST,
    EntryReason.NO_CALLERS,
)


@dataclass(frozen=True)
class EntryPoint:
    node_id: str
    reasons: tuple[EntryReason, ...]


def detect_entry_points(graph: CodeGraph) -> list[EntryPoint]:
    reasons: dict[str, list[EntryReason]] = {}

    for hint in graph.entry_hints:
        node = graph.nodes.get(hint.node_id)
        if node is None or node.kind not in CALLABLE_KINDS:
            continue
        bucket = reasons.setdefault(hint.node_id, [])
        if hint.reason not in bucket:
            bucket.append(hint.reason)

    called = {e.target for e in graph.edges if e.confidence in DRAWN}
    for node in graph.callables():
        if node.id not in called and node.id not in reasons:
            reasons[node.id] = [EntryReason.NO_CALLERS]

    def sort_key(item: tuple[str, list[EntryReason]]) -> tuple[int, str]:
        node_id, node_reasons = item
        best = min(REASON_PRIORITY.index(r) for r in node_reasons)
        return (best, node_id)

    return [
        EntryPoint(node_id=node_id, reasons=tuple(node_reasons))
        for node_id, node_reasons in sorted(reasons.items(), key=sort_key)
    ]
