from __future__ import annotations

import json

from codecards.graph.model import (
    CallSite,
    CodeGraph,
    Confidence,
    Edge,
    EntryHint,
    EntryReason,
    Location,
    Node,
    NodeKind,
)
from codecards.render.viewmodel import build_viewmodel
from codecards.report import AnalysisReport


def build() -> CodeGraph:
    nodes = [
        Node(id="app", kind=NodeKind.PACKAGE, name="app"),
        Node(id="app.cli", kind=NodeKind.MODULE, name="cli", parent="app",
             location=Location("app/cli.py", 1, 20)),
        Node(id="app.cli.main", kind=NodeKind.FUNCTION, name="main", parent="app.cli",
             location=Location("app/cli.py", 3, 8), signature="(argv)",
             summary="Run it.", decorators=("click.command",)),
        Node(id="app.mail", kind=NodeKind.MODULE, name="mail", parent="app",
             location=Location("app/mail.py", 1, 30)),
        Node(id="app.mail.send", kind=NodeKind.FUNCTION, name="send", parent="app.mail",
             location=Location("app/mail.py", 4, 9)),
        # Nothing calls this and it is not an entry point, so it is inert code.
        Node(id="app.mail.unused", kind=NodeKind.FUNCTION, name="unused",
             parent="app.mail", location=Location("app/mail.py", 11, 12)),
    ]
    edges = [Edge("app.cli.main", "app.mail.send", Confidence.RESOLVED,
                  (CallSite(5, in_conditional=True, in_loop=False),))]
    return CodeGraph(
        nodes={n.id: n for n in nodes},
        edges=edges,
        entry_hints=[EntryHint("app.cli.main", EntryReason.DECORATED)],
    )


def model():
    report = AnalysisReport(total_calls=1, by_confidence={"resolved": 1},
                            node_count=5, callable_count=2, edge_count=1)
    return build_viewmodel(build(), report, max_depth=15)


def test_viewmodel_is_json_serialisable():
    json.dumps(model())


def test_nodes_carry_the_fields_the_ui_needs():
    node = next(n for n in model()["nodes"] if n["id"] == "app.cli.main")
    assert node["kind"] == "function"
    assert node["parent"] == "app.cli"
    assert node["file"] == "app/cli.py"
    assert node["lineStart"] == 3
    assert node["signature"] == "(argv)"
    assert node["summary"] == "Run it."
    assert node["decorators"] == ["click.command"]


def test_edges_carry_sites_with_context_flags():
    edge = model()["edges"][0]
    assert edge["source"] == "app.cli.main"
    assert edge["confidence"] == "resolved"
    assert edge["sites"] == [{"line": 5, "cond": True, "loop": False}]


def test_entry_points_are_listed_with_reasons():
    entries = model()["entryPoints"]
    main = next(e for e in entries if e["id"] == "app.cli.main")
    assert main["reasons"] == ["decorated"]


def test_orphans_are_flagged_for_the_muted_badge():
    """Callables nothing calls, that are not entry points, are inert code."""
    assert model()["orphans"] == ["app.mail.unused"]


def test_a_called_function_is_not_an_orphan():
    """send() is called by main(), so it is reachable and must not be muted."""
    assert "app.mail.send" not in model()["orphans"]


def test_an_entry_point_with_no_callers_is_not_an_orphan():
    """main() has no callers by design. That makes it the front door, not
    dead code, and the two must not look the same on the canvas."""
    assert "app.cli.main" not in model()["orphans"]


def test_initial_view_is_the_collapsed_module_view():
    initial = model()["initialView"]
    assert sorted(initial["collapsed"]) == ["app.cli", "app.mail"]
    assert "app.cli.main" not in initial["visible"]
    assert initial["edges"] == [{
        "source": "app.cli", "target": "app.mail",
        "confidence": "resolved", "weight": 1, "tiers": {"resolved": 1},
    }]


def test_golden_trace_covers_the_first_entry_point():
    golden = model()["goldenTrace"]
    assert golden["entryId"] == "app.cli.main"
    assert golden["steps"][0]["calleeId"] == "app.mail.send"
    assert golden["steps"][0]["stack"] == ["app.cli.main"]


def test_stats_are_included():
    stats = model()["stats"]
    assert stats["totalCalls"] == 1
    assert stats["byConfidence"]["resolved"] == 1
    assert stats["resolutionRate"] == 1.0


def test_meta_records_max_depth_version_and_whether_source_is_present():
    meta = model()["meta"]
    assert meta["maxDepth"] == 15
    assert meta["version"]
    assert meta["hasSource"] is False


def test_source_is_omitted_when_not_embedded():
    assert all("source" not in n for n in model()["nodes"])


def test_source_and_tokens_round_trip_when_present():
    graph = build()
    graph.nodes["app.mail.send"] = Node(
        id="app.mail.send", kind=NodeKind.FUNCTION, name="send", parent="app.mail",
        location=Location("app/mail.py", 4, 9),
        source="def send():\n    pass",
        source_tokens=(((0, 3, "kw"), (4, 4, "def")), ()),
    )
    result = build_viewmodel(graph, AnalysisReport(), max_depth=15)
    node = next(n for n in result["nodes"] if n["id"] == "app.mail.send")
    assert node["source"] == "def send():\n    pass"
    assert node["tokens"] == [[[0, 3, "kw"], [4, 4, "def"]], []]
    assert result["meta"]["hasSource"] is True


def test_truncated_source_is_marked_so_the_card_can_say_so():
    graph = build()
    graph.nodes["app.mail.send"] = Node(
        id="app.mail.send", kind=NodeKind.FUNCTION, name="send", parent="app.mail",
        location=Location("app/mail.py", 4, 900), source="x = 1",
        source_tokens=((),), source_truncated=True,
    )
    node = next(n for n in build_viewmodel(graph, AnalysisReport())["nodes"]
                if n["id"] == "app.mail.send")
    assert node["truncated"] is True


def test_empty_graph_has_no_golden_trace():
    assert build_viewmodel(CodeGraph(), AnalysisReport(), max_depth=15)["goldenTrace"] is None
