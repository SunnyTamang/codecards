"""The run summary. This is a product feature, not debug output - a reader
who can see the resolution rate knows how far to trust the picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .graph.model import CONFIDENCE_ORDER

if TYPE_CHECKING:
    # Importing this at runtime would be circular: `extract/__init__` returns
    # an AnalysisReport and so imports this module, while reaching
    # `extract.discovery` initialises the `extract` package first. Only the
    # annotation needs the name, and `from __future__ import annotations`
    # keeps annotations unevaluated. tests/test_imports.py pins this.
    from .extract.discovery import SkippedFile


@dataclass
class AnalysisReport:
    total_calls: int = 0
    by_confidence: dict[str, int] = field(default_factory=dict)
    skipped: list[SkippedFile] = field(default_factory=list)
    node_count: int = 0
    callable_count: int = 0
    edge_count: int = 0
    #: Source files modified after the index that resolved them was built, so
    #: the graph describes code that has since changed. Only ever populated on
    #: a path that reads an index; empty everywhere else.
    stale: list[str] = field(default_factory=list)
    #: What to run to rebuild that index. Shown alongside stale, never alone.
    reindex_command: str | None = None

    @property
    def resolution_rate(self) -> float:
        drawn = sum(
            self.by_confidence.get(c.value, 0)
            for c in CONFIDENCE_ORDER[:3]  # resolved, inferred, ambiguous
        )
        if drawn == 0:
            return 0.0
        return self.by_confidence.get("resolved", 0) / drawn

    def format(self) -> str:
        parts = [
            f"{self.by_confidence.get(c.value, 0):,} {c.value}"
            for c in CONFIDENCE_ORDER
            if self.by_confidence.get(c.value, 0)
        ]
        lines = [f"{self.total_calls:,} calls: " + ", ".join(parts or ["none resolved"])]
        lines.append(
            f"{self.callable_count:,} callables, {self.edge_count:,} edges drawn"
        )
        if self.skipped:
            reasons: dict[str, int] = {}
            for item in self.skipped:
                key = item.reason.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
            detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
            lines.append(f"{len(self.skipped)} files skipped: {detail}")
        return "\n".join(lines)
