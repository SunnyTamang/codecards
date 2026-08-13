from __future__ import annotations


def test_the_default_view_shows_module_cards_not_functions(graph_page):
    assert graph_page.locator("#cards .card[data-id='app.cli']").count() == 1
    assert graph_page.locator("#cards .card[data-id='app.cli.main']").count() == 0


def test_module_cards_show_their_name(graph_page):
    assert graph_page.locator(".card[data-id='app.cli'] .card-name").inner_text() == "cli"


def test_the_aggregated_edge_is_drawn_at_its_merged_confidence(graph_page):
    path = graph_page.locator("#edges path[data-edge='app.cli->app.mail']")
    assert path.count() == 1
    assert "inferred" in path.get_attribute("class")


def test_internal_call_counts_appear_as_a_badge(graph_page):
    badge = graph_page.locator(".card[data-id='app.cli'] .badge-internal")
    assert "1" in badge.inner_text()


def test_no_card_lands_on_top_of_another(graph_page):
    """ELK gives non-overlapping boxes. Mis-accumulated offsets break this."""
    boxes = graph_page.evaluate("CC.view.boxes()")
    ids = [i for i in boxes if i != "app"]  # the package contains the others
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            a, b = boxes[a_id], boxes[b_id]
            overlap = (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                       and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])
            assert not overlap, f"{a_id} overlaps {b_id}"


def test_children_sit_inside_their_container(graph_page):
    """The offset-accumulation check: a nested card must be inside its parent."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    boxes = graph_page.evaluate("CC.view.boxes()")
    child, parent = boxes["app.mail.Mailer.send"], boxes["app.mail.Mailer"]
    assert parent["x"] <= child["x"]
    assert parent["y"] <= child["y"]
    assert child["x"] + child["w"] <= parent["x"] + parent["w"] + 1
    assert child["y"] + child["h"] <= parent["y"] + parent["h"] + 1


def test_callers_are_laid_out_above_callees(graph_page):
    """Vertical position is call depth. That is the whole layout decision."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    boxes = graph_page.evaluate("CC.view.boxes()")
    assert boxes["app.cli.main"]["y"] < boxes["app.cli.load_config"]["y"]


def test_expanded_cards_render_their_source_with_token_classes(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    card = graph_page.locator(".card[data-id='app.cli.main']")
    assert card.locator(".src-line").count() == 6
    assert card.locator(".src-text .kw").first.inner_text() == "def"
    assert card.locator(".src-text .call").first.inner_text() == "load_config"


def test_source_text_survives_highlighting_exactly(graph_page):
    """Painting spans must not drop or reorder a single character."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    rendered = graph_page.evaluate(
        "Array.from(document.querySelectorAll("
        "  \".card[data-id='app.cli.main'] .src-text\")).map(e => e.textContent)")
    assert rendered == [
        "def main(argv=None):",
        "    cfg = load_config()",
        "    for user in cfg.users:",
        "        if user.active:",
        "            mailer.send(user)",
        "    return 0",
    ]


def test_html_in_source_is_not_interpreted(graph_page):
    """Source is text. A comment containing a tag must not become an element.

    The probe has to be attached to the document: a detached element matches
    no locator, so an unattached version of this test passes even when
    renderSource uses innerHTML."""
    graph_page.evaluate("""
        const probe = CC.cards.renderSource(
          {source: "x = '<img src=x onerror=alert(1)>'", tokens: [[]]}, null
        );
        probe.id = 'xss-probe';
        document.body.appendChild(probe);
    """)
    assert graph_page.locator("#xss-probe").count() == 1, "probe was not attached"
    assert graph_page.locator("#xss-probe img").count() == 0
    assert "<img" in graph_page.locator("#xss-probe .src-text").inner_text()


def test_call_site_lines_are_marked_with_their_context(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    card = graph_page.locator(".card[data-id='app.cli.main']")
    # main() spans lines 4-9, so the call at line 8 is the fifth rendered line.
    line = card.locator(".src-line").nth(4)
    assert "call-site" in line.get_attribute("class")
    assert line.locator(".src-gutter .mark[data-kind='loop']").count() == 1
    assert line.locator(".src-gutter .mark[data-kind='cond']").count() == 1


def test_orphans_carry_the_muted_badge(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    card = graph_page.locator(".card[data-id='app.mail.Mailer.retry']")
    assert "orphan" in card.get_attribute("class")


def test_cards_outside_the_viewport_are_not_in_the_dom(graph_page):
    """Culling is what holds the node ceiling, so it has to actually cull."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    before = graph_page.locator("#cards .card").count()
    graph_page.evaluate("CC.canvas.setView({x: -90000, y: -90000, scale: 1})")
    graph_page.wait_for_timeout(120)
    assert graph_page.locator("#cards .card").count() < before


def test_scrolled_away_cards_come_back(graph_page):
    graph_page.evaluate("CC.canvas.setView({x: -90000, y: -90000, scale: 1})")
    graph_page.wait_for_timeout(120)
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1})")
    graph_page.wait_for_timeout(120)
    assert graph_page.locator("#cards .card[data-id='app.cli']").count() == 1


def test_an_empty_graph_paints_without_error(tmp_path, page):
    from codecards.render.bundle import render_html
    empty = {
        "meta": {"version": "0", "generated": "", "maxDepth": 15, "hasSource": False},
        "nodes": [], "edges": [], "entryPoints": [], "orphans": [],
        "initialView": {"collapsed": [], "visible": [], "edges": [], "internalCounts": {}},
        "goldenTrace": None,
        "stats": {"totalCalls": 0, "byConfidence": {}, "resolutionRate": 0.0,
                  "callableCount": 0, "edgeCount": 0, "skipped": []},
    }
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    out = tmp_path / "empty.html"
    out.write_text(render_html(empty), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("window.CC && CC.view && CC.view.ready === true")
    assert errors == []

def test_arrowheads_are_actually_visible(graph_page):
    """`#edges path { fill: none }` applies to the marker paths in <defs> too,
    so an arrowhead with no fill of its own renders as nothing at all. The
    marker classes are prefixed to avoid colliding with the tier selectors
    that style and count real edges, which is what removed their fill."""
    got = graph_page.evaluate("""(() => {
        return Array.from(document.querySelectorAll('#edges marker path')).map(h => {
            const cs = getComputedStyle(h);
            return {cls: h.getAttribute('class'), fill: cs.fill, stroke: cs.stroke};
        });
    })()""")
    assert got, "no arrowhead markers were defined"
    for head in got:
        assert head["fill"] != "none", f"{head['cls']} has no fill and cannot be seen"


def test_marker_paths_do_not_match_the_edge_tier_selectors(graph_page):
    """A marker carrying a bare tier name inflates every count of drawn edges
    at that tier, which made the confidence-toggle assertions unfalsifiable."""
    for tier in ("resolved", "inferred", "ambiguous", "active"):
        assert graph_page.locator(f"#edges marker path.{tier}").count() == 0


def test_the_pin_never_overlaps_the_badges(graph_page):
    """The pin is positioned absolutely in the corner while the badges are
    laid out to the card's right edge, so without reserved space the counts
    render underneath it and become unreadable."""
    result = graph_page.evaluate("""() => {
        const overlaps = [];
        let checked = 0;
        document.querySelectorAll('#cards .card').forEach(card => {
            const pin = card.querySelector('.card-pin');
            const badges = card.querySelector('.card-badges');
            if (!pin || !badges) return;
            if (!pin.getClientRects().length || !badges.getClientRects().length) return;
            checked += 1;
            const a = pin.getBoundingClientRect();
            const b = badges.getBoundingClientRect();
            if (a.left < b.right && b.left < a.right
                && a.top < b.bottom && b.top < a.bottom) {
                overlaps.push(card.dataset.id);
            }
        });
        return {checked: checked, overlaps: overlaps};
    }""")
    assert result["checked"] > 0, "no card had both a pin and badges to compare"
    assert result["overlaps"] == []


def test_a_real_entry_point_is_marked_on_the_canvas(graph_page):
    """The tool exists for someone asking where to start, so the answer
    belongs on the canvas and not only in a menu."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    marked = graph_page.evaluate(
        "Array.from(document.querySelectorAll('#cards .card.entry')).map(c => c.dataset.id)")
    assert marked == ["app.cli.main"]


def test_structural_and_test_entry_points_are_not_marked(tmp_path, page, sample_viewmodel):
    """"nothing calls it" matches thousands of functions in a large library and
    a test is a way into the suite, not the program. Marking either would mark
    half the canvas: on scikit-learn it was 4,444 of 8,734 callables."""
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    vm["entryPoints"] = [
        {"id": "app.cli.main", "reasons": ["no_callers"]},
        {"id": "app.cli.load_config", "reasons": ["test"]},
        {"id": "app.mail.Mailer.send", "reasons": ["console_script"]},
    ]
    out = tmp_path / "entries.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    page.evaluate("CC.view.layout(new Set())")
    page.wait_for_function("CC.view.ready === true")
    marked = page.evaluate(
        "Array.from(document.querySelectorAll('#cards .card.entry')).map(c => c.dataset.id)")
    assert marked == ["app.mail.Mailer.send"]
