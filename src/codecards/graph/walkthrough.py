"""Turn the call graph into an ordered narrative from a chosen entry point.

The ordering is lexical, not executional: a call inside an `if` still gets a
step. Steps carry in_conditional and in_loop so the player can say so.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import DRAWN, CodeGraph, Confidence

DEFAULT_MAX_DEPTH = 15


@dataclass(frozen=True)
class Step:
    index: int
    caller_id: str
    callee_id: str
    line: int
    depth: int
    stack: tuple[str, ...]  # callers, outermost first, including caller_id
    confidence: Confidence
    in_conditional: bool
    in_loop: bool
    recursive: bool


def build_walkthrough(
    graph: CodeGraph,
    entry_id: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
    tiers: tuple[Confidence, ...] = DRAWN,
) -> list[Step]:
    if entry_id not in graph.nodes:
        return []

    outgoing: dict[str, list[tuple[int, str, Confidence, bool, bool]]] = {}
    for edge in graph.edges:
        if edge.confidence not in tiers:
            continue
        sites = edge.call_sites or ()
        for site in sites:
            outgoing.setdefault(edge.source, []).append(
                (site.line, edge.target, edge.confidence, site.in_conditional, site.in_loop)
            )
    for calls in outgoing.values():
        calls.sort(key=lambda c: (c[0], c[1]))

    steps: list[Step] = []

    def walk(caller: str, stack: tuple[str, ...]) -> None:
        depth = len(stack) - 1
        for line, callee, confidence, in_conditional, in_loop in outgoing.get(caller, []):
            recursive = callee in stack
            steps.append(
                Step(
                    index=len(steps),
                    caller_id=caller,
                    callee_id=callee,
                    line=line,
                    depth=depth,
                    stack=stack,
                    confidence=confidence,
                    in_conditional=in_conditional,
                    in_loop=in_loop,
                    recursive=recursive,
                )
            )
            if not recursive and depth + 1 <= max_depth:
                walk(callee, stack + (callee,))

    walk(entry_id, (entry_id,))
    return steps
