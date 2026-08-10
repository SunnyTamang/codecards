from __future__ import annotations

"""The drift alarm.

`graph/collapse.py` and `graph/walkthrough.py` are the reference
implementations; `collapse.js` and `trace.js` are their browser twins. The
viewmodel ships one golden output of each. If these tests fail, the two
implementations have diverged and the browser is lying about the graph.

Do not fix a failure here by regenerating the golden. Find which side is wrong.
"""


def test_js_collapse_reproduces_the_shipped_initial_view(graph_page):
    computed = graph_page.evaluate("""
        CC.collapse.aggregate(
          CODECARDS_DATA.edges,
          CC.view.state.data.parentIndex,
          new Set(CODECARDS_DATA.initialView.visible),
          new Set(CODECARDS_DATA.initialView.collapsed))
          .map(e => ({source: e.source, target: e.target,
                      confidence: e.confidence, weight: e.weight, tiers: e.tiers}))
          .sort((a, b) => (a.source + a.target).localeCompare(b.source + b.target))
    """)
    expected = sorted(graph_page.evaluate("CODECARDS_DATA.initialView.edges"),
                      key=lambda e: e["source"] + e["target"])
    assert computed == expected


def test_js_collapse_reproduces_the_shipped_internal_counts(graph_page):
    computed = graph_page.evaluate("""
        CC.collapse.internalCounts(
          CODECARDS_DATA.edges,
          CC.view.state.data.parentIndex,
          new Set(CODECARDS_DATA.initialView.visible),
          new Set(CODECARDS_DATA.initialView.collapsed))
    """)
    assert computed == graph_page.evaluate("CODECARDS_DATA.initialView.internalCounts")


def test_js_trace_reproduces_the_shipped_golden_trace(graph_page):
    computed = graph_page.evaluate("""
        CC.trace.build(CODECARDS_DATA.edges,
                       CODECARDS_DATA.goldenTrace.entryId,
                       CODECARDS_DATA.meta.maxDepth)
    """)
    expected = graph_page.evaluate("CODECARDS_DATA.goldenTrace.steps")
    assert len(computed) == len(expected)
    for got, want in zip(computed, expected, strict=True):
        for field in ("callerId", "calleeId", "line", "depth", "stack",
                      "confidence", "cond", "loop", "recursive"):
            assert got[field] == want[field], f"{field} differs at step {got['index']}"


def test_the_goldens_are_actually_present(graph_page):
    """A golden that silently became empty would make the tests above vacuous."""
    assert graph_page.evaluate("CODECARDS_DATA.initialView.edges.length") > 0
    assert graph_page.evaluate("CODECARDS_DATA.goldenTrace.steps.length") > 0
