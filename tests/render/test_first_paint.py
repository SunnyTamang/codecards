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

def test_arrowheads_and_connector_dots_are_actually_visible(graph_page):
    """Heads are drawn geometry, not SVG markers, because a marker is sized
    from stroke-width and so grows with the canvas transform while the lines
    carry non-scaling-stroke and do not. A head needs a stroke and a dot needs
    a fill; `#edges path { fill: none }` covers the head and would erase a dot
    that relied on the same declaration."""
    got = graph_page.evaluate("""(() => {
        const heads = Array.from(document.querySelectorAll('#edges .edge-head'));
        const dots = Array.from(document.querySelectorAll('#edges .edge-dot'));
        const look = (n) => {
            const cs = getComputedStyle(n);
            return {cls: n.getAttribute('class'), fill: cs.fill, stroke: cs.stroke};
        };
        return {heads: heads.map(look), dots: dots.map(look)};
    })()""")
    assert got["heads"], "no arrowheads were drawn"
    assert got["dots"], "no connector dots were drawn"
    for head in got["heads"]:
        assert head["stroke"] != "none", f"{head['cls']} has no stroke and cannot be seen"
    for dot in got["dots"]:
        assert dot["fill"] != "none", f"{dot['cls']} has no fill and cannot be seen"


def test_edge_decoration_does_not_match_the_tier_selectors(graph_page):
    """Three marks are drawn per edge. If the head or the dot carried a bare
    tier name it would inflate every count of drawn edges at that tier, which
    is what made the confidence-toggle assertions unfalsifiable before."""
    for tier in ("resolved", "inferred", "ambiguous", "active"):
        drawn = graph_page.locator(f"#edges path.{tier}").count()
        anchored = graph_page.locator(f"#edges path.{tier}[data-edge]").count()
        assert drawn == anchored, f"something other than a line carries .{tier}"
        assert graph_page.locator(f"#edges .edge-head.{tier}").count() == 0
        assert graph_page.locator(f"#edges .edge-dot.{tier}").count() == 0


def test_the_arrowhead_stays_one_weight_with_the_line_at_any_zoom(graph_page):
    """The defect this replaced: markers scale with the transform and
    non-scaling strokes do not, so zooming in grew the head while the line it
    belonged to stayed a hairline."""

    def head_span():
        return graph_page.evaluate("""(() => {
            const h = document.querySelector('#edges .edge-head');
            const b = h.getBBox();
            const s = CC.canvas.getView().scale;
            return Math.max(b.width, b.height) * s;   // on-screen size
        })()""")

    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.7})")
    graph_page.wait_for_timeout(80)
    small = head_span()
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 2.4})")
    graph_page.wait_for_timeout(80)
    large = head_span()
    assert abs(large - small) < 2, (
        f"arrowhead is {small:.1f}px on screen at 0.7 and {large:.1f}px at 2.4"
    )


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


def test_a_card_is_always_wide_enough_for_its_own_name(tmp_path, page, sample_viewmodel):
    """The name is the one thing a card exists to say, and it kept losing the
    argument. Magnitude sets the width from fan-in, while the count readouts
    and the entry/unused chips claim their room first, so a long name beside
    "OUT 24 INT 106" or an UNUSED chip was the part that got clipped. Plates
    are measured on a separate path and carry the same trimmings.
    """
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    long_name = "reconcile_conflicting_signals_for_review"
    vm["nodes"].append({
        "id": f"app.mail.{long_name}", "kind": "function", "name": long_name,
        "parent": "app.mail", "file": "app/mail.py", "lineStart": 40,
        "lineEnd": 41, "signature": "()", "summary": None, "decorators": [],
        "implicit": False, "dunder": False,
    })
    # No callers, so it wears an UNUSED chip on top of its counts.
    vm["orphans"] = [*vm.get("orphans", []), f"app.mail.{long_name}"]

    out = tmp_path / "wide.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    page.evaluate("CC.view.layout(new Set())")
    page.wait_for_function("CC.view.ready === true")
    page.wait_for_timeout(250)

    clipped = page.evaluate("""() => {
        const bad = [];
        document.querySelectorAll('#cards .card .card-name').forEach((n) => {
            if (n.scrollWidth > n.clientWidth + 1) bad.push(n.textContent);
        });
        return bad;
    }""")
    assert clipped == []


def test_a_name_fits_at_the_far_tier_too(tmp_path, page, sample_viewmodel):
    """Block tier sets the name at display size, several points larger than
    the size the box was measured for, so a box wide enough at card tier cut
    the same name at block tier. That is the one tier whose entire content is
    the name."""
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    long_name = "test_not_defensible_routes_to_fallback_even_with_signal"
    vm["nodes"].append({
        "id": f"app.mail.{long_name}", "kind": "function", "name": long_name,
        "parent": "app.mail", "file": "app/mail.py", "lineStart": 40,
        "lineEnd": 41, "signature": "()", "summary": None, "decorators": [],
        "implicit": False, "dunder": False,
    })

    out = tmp_path / "block.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    page.evaluate("CC.view.layout(new Set())")
    page.wait_for_function("CC.view.ready === true")
    # Below 0.6 is block tier, where the name is the whole card.
    page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.5})")
    page.wait_for_timeout(250)

    clipped = page.evaluate("""() => {
        const bad = [];
        document.querySelectorAll('#cards .card').forEach((c) => {
            if (!c.classList.contains('tier-block')) return;
            const n = c.querySelector('.card-name');
            if (n && n.scrollWidth > n.clientWidth + 1) bad.push(n.textContent);
        });
        return bad;
    }""")
    assert clipped == []
