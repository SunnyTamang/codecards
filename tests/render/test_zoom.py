from __future__ import annotations

import pytest


def tier_of(page, node_id):
    cls = page.locator(f".card[data-id='{node_id}']").get_attribute("class")
    for tier in ("block", "card", "source"):
        if f"tier-{tier}" in cls:
            return tier
    return None


def test_the_thresholds_are_the_documented_ones(graph_page):
    assert graph_page.evaluate("CC.zoom.tierFor(0.2)") == "block"
    assert graph_page.evaluate("CC.zoom.tierFor(0.34)") == "block"
    assert graph_page.evaluate("CC.zoom.tierFor(0.35)") == "card"
    assert graph_page.evaluate("CC.zoom.tierFor(0.79)") == "card"
    assert graph_page.evaluate("CC.zoom.tierFor(0.8)") == "source"
    assert graph_page.evaluate("CC.zoom.tierFor(2.5)") == "source"


def test_zooming_swaps_the_tier_class(graph_page):
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.2})")
    graph_page.wait_for_timeout(80)
    assert tier_of(graph_page, "app.cli") == "block"
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.5})")
    graph_page.wait_for_timeout(80)
    assert tier_of(graph_page, "app.cli") == "source"


def test_block_tier_hides_everything_but_the_name(graph_page):
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.2})")
    graph_page.wait_for_timeout(80)
    card = graph_page.locator(".card[data-id='app.cli']")
    assert card.locator(".card-name").is_visible()
    assert not card.locator(".card-badges").is_visible()


def test_zooming_never_triggers_a_relayout(graph_page):
    """The layout is computed at card dimensions once. Zoom is pure CSS."""
    # The trailing `void 0` is load-bearing. Playwright INVOKES the completion
    # value of evaluate() when it is a function, passing Python's implicit
    # arg as null, so ending on the assignment calls layout(null) immediately.
    graph_page.evaluate("window.__layouts = 0;"
                        "const real = CC.view.layout;"
                        "CC.view.layout = function (c) { window.__layouts++; return real(c); };"
                        "void 0;")
    for scale in (0.1, 0.3, 0.5, 0.9, 2.0, 0.2):
        graph_page.evaluate(f"CC.canvas.setView({{x: 0, y: 0, scale: {scale}}})")
        graph_page.wait_for_timeout(40)
    assert graph_page.evaluate("window.__layouts") == 0


def test_boxes_are_identical_before_and_after_zooming(graph_page):
    before = graph_page.evaluate("CC.view.boxes()")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 2.4})")
    graph_page.wait_for_timeout(80)
    assert graph_page.evaluate("CC.view.boxes()") == before


def test_a_pinned_card_stays_at_source_tier_when_zoomed_out(graph_page):
    """Read one function in full while the architecture stays visible."""
    graph_page.evaluate("CC.zoom.pin('app.cli')")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.15})")
    graph_page.wait_for_timeout(80)
    assert tier_of(graph_page, "app.cli") == "source"
    assert tier_of(graph_page, "app.mail") == "block"


def test_unpinning_returns_the_card_to_the_zoom_tier(graph_page):
    graph_page.evaluate("CC.zoom.pin('app.cli'); CC.canvas.setView({x:0,y:0,scale:0.15})")
    graph_page.wait_for_timeout(80)
    graph_page.evaluate("CC.zoom.unpin('app.cli')")
    graph_page.wait_for_timeout(80)
    assert tier_of(graph_page, "app.cli") == "block"


def test_the_pin_button_toggles_the_pin(graph_page):
    """Driven with a real mouse on purpose. The canvas captures the pointer to
    pan, and capture retargets the following click to the viewport, so a
    scripted element.click() passed while no user could ever pin a card."""
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.0})")
    graph_page.wait_for_timeout(80)
    pin = graph_page.locator(".card[data-id='app.cli'] .card-pin")
    box = pin.bounding_box()
    graph_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert graph_page.evaluate("CC.zoom.isPinned('app.cli')") is True
    box = pin.bounding_box()
    graph_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert graph_page.evaluate("CC.zoom.isPinned('app.cli')") is False


def test_clicking_a_card_control_does_not_pan_the_canvas(graph_page):
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.0})")
    graph_page.wait_for_timeout(80)
    before = graph_page.evaluate("CC.canvas.getView()")
    box = graph_page.locator(".card[data-id='app.cli'] .card-pin").bounding_box()
    graph_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    graph_page.mouse.down()
    graph_page.mouse.move(box["x"] + 60, box["y"] + 40)
    graph_page.mouse.up()
    assert graph_page.evaluate("CC.canvas.getView()") == before


def test_a_pin_survives_the_card_being_culled_and_remounted(graph_page):
    graph_page.evaluate("CC.zoom.pin('app.cli')")
    graph_page.evaluate("CC.canvas.setView({x: -90000, y: -90000, scale: 1})")
    graph_page.wait_for_timeout(120)
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.15})")
    graph_page.wait_for_timeout(120)
    assert tier_of(graph_page, "app.cli") == "source"


def test_an_edge_leaves_from_its_calling_line_at_source_tier(graph_page):
    """The promise of showing code: the arrow starts at the line that calls.

    Compared against measured geometry, not against the layout box. A
    source-tier card deliberately grows past the box layout reserved, so the
    box tells you nothing about where line 5 actually is."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.0})")
    # Source tier only opens a card you are pointing at, have selected, or
    # have pinned, so an edge is line-anchored exactly when its source card is
    # actually showing the line.
    graph_page.evaluate("CC.zoom.pin('app.cli.main')")
    graph_page.wait_for_timeout(200)

    got = graph_page.evaluate("""() => {
        const card = document.querySelector('.card[data-id="app.cli.main"]');
        const row = card.querySelector('.src-line[data-line="5"]');
        const path = document.querySelector(
            '#edges path[data-edge="app.cli.main->app.cli.load_config"]');
        const start = path.getAttribute('d').slice(1).split(' ')[0]
                          .split(',').map(Number);
        const box = CC.view.boxes()['app.cli.main'];
        const cardRect = card.getBoundingClientRect();
        const rowRect = row.getBoundingClientRect();
        const scale = CC.canvas.getView().scale;
        return {
            startX: start[0], startY: start[1],
            wantX: box.x + cardRect.width / scale,
            wantY: box.y + (rowRect.top + rowRect.height / 2 - cardRect.top) / scale,
            boxBottom: box.y + box.h,
        };
    }""")
    assert got["startY"] == pytest.approx(got["wantY"], abs=2)
    assert got["startX"] == pytest.approx(got["wantX"], abs=2)
    # And it is genuinely line-anchored, not the bottom-edge fallback.
    assert abs(got["startY"] - got["boxBottom"]) > 5


def test_edges_fall_back_to_the_card_edge_below_source_tier(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.5})")
    graph_page.wait_for_timeout(200)

    got = graph_page.evaluate("""() => {
        const path = document.querySelector(
            '#edges path[data-edge="app.cli.main->app.cli.load_config"]');
        const start = path.getAttribute('d').slice(1).split(' ')[0]
                          .split(',').map(Number);
        const box = CC.view.boxes()['app.cli.main'];
        return {startX: start[0], startY: start[1],
                wantX: box.x + box.w / 2, wantY: box.y + box.h};
    }""")
    assert got["startX"] == pytest.approx(got["wantX"], abs=1)
    assert got["startY"] == pytest.approx(got["wantY"], abs=1)



def test_source_tier_is_absent_when_no_source_was_embedded(
    tmp_path, page, sample_viewmodel
):
    """--no-source means the card tier is the ceiling."""
    from codecards.render.bundle import render_html

    payload = sample_viewmodel
    payload["meta"]["hasSource"] = False
    for node in payload["nodes"]:
        node.pop("source", None)
        node.pop("tokens", None)
    out = tmp_path / "nosrc.html"
    out.write_text(render_html(payload), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 2.5})")
    page.wait_for_timeout(80)
    assert tier_of(page, "app.cli") == "card"

def test_zooming_in_does_not_open_every_card_at_once(graph_page):
    """Reported from the demo: crossing the source threshold expanded every
    visible card simultaneously and they piled on top of each other, which
    buried the graph. Source tier means a card MAY open, not that it must."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.5})")
    graph_page.wait_for_timeout(200)

    open_bodies = graph_page.evaluate(
        "Array.from(document.querySelectorAll('#cards .card .card-body'))"
        ".filter(b => b.getClientRects().length).length")
    assert open_bodies == 0


def test_selecting_or_pinning_opens_one_card(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.5})")
    graph_page.evaluate("CC.zoom.pin('app.cli.main')")
    graph_page.wait_for_timeout(200)

    open_ids = graph_page.evaluate(
        "Array.from(document.querySelectorAll('#cards .card'))"
        ".filter(c => c.querySelector('.card-body')"
        "          && c.querySelector('.card-body').getClientRects().length)"
        ".map(c => c.dataset.id)")
    assert open_ids == ["app.cli.main"]


def test_hovering_a_card_opens_it(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.5})")
    graph_page.wait_for_timeout(200)

    card = graph_page.locator(".card[data-id='app.cli.main']")
    card.hover()
    graph_page.wait_for_timeout(150)
    assert card.locator(".card-body").is_visible()

def test_an_expanded_container_never_opens_its_own_source(graph_page):
    """A class card that has been opened already shows its methods as child
    cards. Drawing the whole class body over them buries them."""
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1.5})")
    graph_page.evaluate("CC.zoom.pin('app.mail.Mailer')")
    graph_page.wait_for_timeout(200)

    container = graph_page.locator(".card[data-id='app.mail.Mailer']")
    assert "container" in container.get_attribute("class")
    assert container.locator("> .card-body").count() == 0 or not (
        container.locator("> .card-body").is_visible())

