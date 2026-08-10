from __future__ import annotations

from pathlib import Path

import pytest

from codecards.cli import main
from codecards.extract import analyze
from codecards.graph.model import Confidence, validate
from codecards.render.bundle import render_html
from codecards.render.viewmodel import build_viewmodel

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "codecards"

#: The ratchet. Raise it when the resolver improves; never lower it to make a
#: build pass. A drop is a regression, not a threshold problem.
#:
#: Set just under the measured rate on this codebase (95.7% at the time of
#: writing). A floor far below the real figure is not a ratchet: at 0.80 the
#: resolver could lose fifteen points of accuracy and CI would stay green.
MIN_RESOLUTION_RATE = 0.95


@pytest.fixture(scope="module")
def dogfood():
    return analyze([SOURCE_ROOT])


def test_the_analyser_reads_its_own_source_without_skipping_anything(dogfood):
    _graph, report = dogfood
    assert report.skipped == [], f"failed to parse own source: {report.skipped}"


def test_the_resolution_rate_holds_above_the_ratchet(dogfood):
    _graph, report = dogfood
    assert report.resolution_rate >= MIN_RESOLUTION_RATE, (
        f"resolution fell to {report.resolution_rate:.1%}, below the "
        f"{MIN_RESOLUTION_RATE:.0%} ratchet. Investigate the regression rather "
        f"than lowering this number."
    )


def test_the_graph_invariants_hold_on_real_code(dogfood):
    graph, _report = dogfood
    validate(graph)


def test_known_call_edges_are_found(dogfood):
    """Spot checks that would break if resolution silently degraded."""
    graph, _report = dogfood
    edges = {(e.source, e.target) for e in graph.edges
             if e.confidence == Confidence.RESOLVED}
    assert ("codecards.render.viewmodel.build_viewmodel",
            "codecards.graph.collapse.collapse") in edges
    assert ("codecards.render.bundle.render_html",
            "codecards.render.bundle._embed_json") in edges


def test_extraction_reaches_every_module(dogfood):
    graph, _report = dogfood
    modules = {n.id for n in graph.nodes.values() if n.kind.value == "module"}
    for expected in ("codecards.extract.calls", "codecards.extract.highlight",
                     "codecards.graph.collapse", "codecards.render.viewmodel"):
        assert expected in modules


def test_the_layering_rule_holds():
    """graph/ and render/ must never learn Python syntax."""
    for package in ("graph", "render"):
        for path in (SOURCE_ROOT / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "import ast" not in text, f"{path} imports ast"
            assert "import tokenize" not in text, f"{path} imports tokenize"


def test_the_whole_pipeline_produces_a_page_for_its_own_source(dogfood, tmp_path):
    graph, report = dogfood
    html = render_html(build_viewmodel(graph, report))
    out = tmp_path / "self.html"
    out.write_text(html, encoding="utf-8")
    assert out.stat().st_size > 500_000


def test_the_cli_round_trips_on_its_own_source(tmp_path):
    out = tmp_path / "self.html"
    assert main([str(SOURCE_ROOT), "-o", str(out), "--quiet"]) == 0
    assert out.is_file()


def test_no_source_round_trips(tmp_path):
    out = tmp_path / "self.html"
    assert main([str(SOURCE_ROOT), "-o", str(out), "--no-source", "--quiet"]) == 0
    assert out.is_file()


def test_omitting_source_makes_a_materially_smaller_payload(tmp_path):
    """The flag has to earn its place; if it saves nothing, drop it.

    Compare the embedded data, not the file. elkjs is a fixed 1.6MB and is
    about three quarters of the output on a project this size, so a real
    saving in the payload barely moves the total: 41% off the data is 8% off
    the file. Asserting on file size would make this test unfalsifiable.
    """
    with_source = tmp_path / "with.html"
    without = tmp_path / "without.html"
    main([str(SOURCE_ROOT), "-o", str(with_source), "--quiet"])
    main([str(SOURCE_ROOT), "-o", str(without), "--no-source", "--quiet"])

    def payload(path: Path) -> int:
        text = path.read_text(encoding="utf-8")
        start = text.index("window.CODECARDS_DATA = ")
        return len(text[start:text.index("</script>", start)])

    assert payload(without) < payload(with_source) * 0.75


def test_the_generated_page_for_real_source_paints(tmp_path, page):
    """The end-to-end claim: real code in, a working page out."""
    pytest.importorskip("playwright")
    graph, report = analyze([SOURCE_ROOT])
    out = tmp_path / "self.html"
    out.write_text(render_html(build_viewmodel(graph, report)), encoding="utf-8")

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true", timeout=30_000)
    assert errors == []
    assert page.locator("#cards .card").count() > 0
    page.evaluate("CC.player.start('codecards.cli.main')")
    page.wait_for_function("CC.player.state.steps.length > 0")
