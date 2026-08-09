"""Language-neutral graph model. Nothing here knows what Python is."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeKind(str, Enum):
    PACKAGE = "package"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


CALLABLE_KINDS = frozenset({NodeKind.FUNCTION, NodeKind.METHOD})
CONTAINER_KINDS = frozenset({NodeKind.PACKAGE, NodeKind.MODULE, NodeKind.CLASS})


class Confidence(str, Enum):
    RESOLVED = "resolved"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


#: Best first. Used when merging parallel edges - the strongest tier wins.
CONFIDENCE_ORDER = (
    Confidence.RESOLVED,
    Confidence.INFERRED,
    Confidence.AMBIGUOUS,
    Confidence.EXTERNAL,
    Confidence.UNRESOLVED,
)

#: Tiers that become visible edges in the graph.
DRAWN = (Confidence.RESOLVED, Confidence.INFERRED, Confidence.AMBIGUOUS)


class EntryReason(str, Enum):
    MAIN_BLOCK = "main_block"
    CONSOLE_SCRIPT = "console_script"
    DECORATED = "decorated"
    TEST = "test"
    NO_CALLERS = "no_callers"


@dataclass(frozen=True)
class Location:
    file: str  # path relative to the analysis root, POSIX separators
    line_start: int
    line_end: int


@dataclass(frozen=True)
class CallSite:
    line: int
    in_conditional: bool = False
    in_loop: bool = False


@dataclass(frozen=True)
class Node:
    id: str  # qualified name, e.g. "app.mailer.EmailHandler.send"
    kind: NodeKind
    name: str
    parent: str | None = None
    location: Location | None = None
    signature: str | None = None
    summary: str | None = None  # first line of the docstring
    decorators: tuple[str, ...] = ()
    source: str | None = None  # populated only with --embed-source
    source_tokens: tuple[tuple[tuple[int, int, str], ...], ...] | None = None
    source_truncated: bool = False


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    confidence: Confidence
    call_sites: tuple[CallSite, ...] = ()


@dataclass(frozen=True)
class EntryHint:
    node_id: str
    reason: EntryReason


class GraphInvariantError(Exception):
    """Raised when a CodeGraph breaks a structural guarantee."""


@dataclass
class CodeGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    entry_hints: list[EntryHint] = field(default_factory=list)

    def children(self, node_id: str | None) -> list[Node]:
        return [n for n in self.nodes.values() if n.parent == node_id]

    def ancestors(self, node_id: str) -> list[str]:
        """Containment chain, nearest first, excluding the node itself."""
        out: list[str] = []
        seen = {node_id}
        current = self.nodes[node_id].parent
        while current is not None and current not in seen:
            out.append(current)
            seen.add(current)
            node = self.nodes.get(current)
            current = node.parent if node else None
        return out

    def callables(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind in CALLABLE_KINDS]


def validate(graph: CodeGraph) -> None:
    """Assert the two structural invariants. Raises GraphInvariantError."""
    for node in graph.nodes.values():
        if node.parent is not None and node.parent not in graph.nodes:
            raise GraphInvariantError(f"{node.id!r} has unknown parent {node.parent!r}")

    # Containment must be a forest.
    for node in graph.nodes.values():
        seen = {node.id}
        current = node.parent
        while current is not None:
            if current in seen:
                raise GraphInvariantError(f"containment cycle involving {node.id!r}")
            seen.add(current)
            current = graph.nodes[current].parent

    for edge in graph.edges:
        for endpoint in (edge.source, edge.target):
            if endpoint not in graph.nodes:
                raise GraphInvariantError(f"edge references unknown node {endpoint!r}")
            if graph.nodes[endpoint].kind not in CALLABLE_KINDS:
                raise GraphInvariantError(
                    f"edge endpoint {endpoint!r} is {graph.nodes[endpoint].kind.value},"
                    " but edges must connect callables only"
                )
