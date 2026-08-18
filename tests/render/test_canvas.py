from __future__ import annotations

import pytest

from codecards.render.bundle import render_html

pytest.importorskip("playwright")

VIEWMODEL = {
    "meta": {"version": "0.0.0", "generated": "", "maxDepth": 15, "hasSource": False},
    "nodes": [], "edges": [], "entryPoints": [], "orphans": [],
    "initialView": {"collapsed": [], "visible": [], "edges": [], "internalCounts": {}},
    "goldenTrace": None,
    "stats": {"totalCalls": 0, "byConfidence": {}, "resolutionRate": 0.0,
              "callableCount": 0, "edgeCount": 0, "skipped": []},
}


@pytest.fixture
def canvas_page(tmp_path, page):
    out = tmp_path / "canvas.html"
    out.write_text(render_html(VIEWMODEL), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("window.CC && window.CC.canvas")
    return page


def test_the_view_starts_at_the_origin_and_unit_scale(canvas_page):
    assert canvas_page.evaluate("CC.canvas.getView()") == {"x": 0, "y": 0, "scale": 1}


def test_setting_the_view_writes_one_transform_on_world(canvas_page):
    canvas_page.evaluate("CC.canvas.setView({x: -40, y: 25, scale: 2})")
    transform = canvas_page.evaluate("getComputedStyle(document.getElementById('world')).transform")
    # matrix(scaleX, skewY, skewX, scaleY, translateX, translateY)
    assert transform == "matrix(2, 0, 0, 2, -40, 25)"


def test_zoom_keeps_the_point_under_the_cursor_fixed(canvas_page):
    """The property that makes zooming usable: the code you point at stays put."""
    before = canvas_page.evaluate("CC.canvas.screenToWorld(300, 200)")
    canvas_page.evaluate("CC.canvas.zoomBy(1.6, 300, 200)")
    after = canvas_page.evaluate("CC.canvas.screenToWorld(300, 200)")
    assert abs(before["x"] - after["x"]) < 0.01
    assert abs(before["y"] - after["y"]) < 0.01


def test_zoom_is_clamped_at_both_ends(canvas_page):
    canvas_page.evaluate("for (let i = 0; i < 60; i++) CC.canvas.zoomBy(2, 100, 100)")
    assert canvas_page.evaluate("CC.canvas.getView().scale") == pytest.approx(3.0)
    canvas_page.evaluate("for (let i = 0; i < 200; i++) CC.canvas.zoomBy(0.5, 100, 100)")
    assert canvas_page.evaluate("CC.canvas.getView().scale") == pytest.approx(0.05)


def test_setting_a_scale_outside_the_range_is_clamped_too(canvas_page):
    canvas_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 99})")
    assert canvas_page.evaluate("CC.canvas.getView().scale") == pytest.approx(3.0)


def test_fit_centres_the_bounds(canvas_page):
    """screenToWorld takes CLIENT coordinates. The viewport sits below the
    toolbar, so its centre is box.top + box.height/2, not height/2, and passing
    the latter silently shifts the assertion by the toolbar height."""
    canvas_page.evaluate("CC.canvas.fit({x: 0, y: 0, w: 1000, h: 500}, 40)")
    centre = canvas_page.evaluate("""() => {
        const box = document.getElementById('viewport').getBoundingClientRect();
        return CC.canvas.screenToWorld(box.left + box.width / 2,
                                       box.top + box.height / 2);
    }""")
    assert centre["x"] == pytest.approx(500, abs=1)
    assert centre["y"] == pytest.approx(250, abs=1)


def test_fit_fills_the_padded_viewport_without_overflowing_it(canvas_page):
    """Do not assert a particular scale. Whether fit zooms in or out depends on
    the window size, so assert the property instead: the bounds end up inside
    the padded area, and touch it on one axis."""
    canvas_page.evaluate("CC.canvas.fit({x: 0, y: 0, w: 1000, h: 500}, 40)")
    got = canvas_page.evaluate("""() => {
        const box = document.getElementById('viewport').getBoundingClientRect();
        const s = CC.canvas.getView().scale;
        return {w: 1000 * s, h: 500 * s,
                availW: box.width - 80, availH: box.height - 80};
    }""")
    assert got["w"] <= got["availW"] + 1
    assert got["h"] <= got["availH"] + 1
    assert (abs(got["w"] - got["availW"]) < 1
            or abs(got["h"] - got["availH"]) < 1), "fit left space unused"


def test_fit_scales_down_when_the_graph_is_bigger_than_the_viewport(canvas_page):
    canvas_page.evaluate("CC.canvas.fit({x: 0, y: 0, w: 20000, h: 12000}, 40)")
    assert canvas_page.evaluate("CC.canvas.getView().scale") < 1


def test_fit_of_an_empty_bounds_does_not_divide_by_zero(canvas_page):
    canvas_page.evaluate("CC.canvas.fit({x: 0, y: 0, w: 0, h: 0}, 40)")
    view = canvas_page.evaluate("CC.canvas.getView()")
    assert view["scale"] > 0
    assert view["x"] == view["x"]  # not NaN


def test_dragging_the_viewport_pans_the_world(canvas_page):
    canvas_page.mouse.move(400, 300)
    canvas_page.mouse.down()
    canvas_page.mouse.move(340, 260)
    canvas_page.mouse.up()
    view = canvas_page.evaluate("CC.canvas.getView()")
    assert view["x"] == pytest.approx(-60, abs=2)
    assert view["y"] == pytest.approx(-40, abs=2)


def test_the_visible_rect_grows_with_the_margin(canvas_page):
    tight = canvas_page.evaluate("CC.canvas.visibleWorldRect(0)")
    loose = canvas_page.evaluate("CC.canvas.visibleWorldRect(200)")
    assert loose["w"] > tight["w"] and loose["h"] > tight["h"]
    assert loose["x"] < tight["x"] and loose["y"] < tight["y"]


def test_the_visible_rect_covers_more_world_when_zoomed_out(canvas_page):
    canvas_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 1})")
    near = canvas_page.evaluate("CC.canvas.visibleWorldRect(0)")
    canvas_page.evaluate("CC.canvas.setView({x: 0, y: 0, scale: 0.25})")
    far = canvas_page.evaluate("CC.canvas.visibleWorldRect(0)")
    assert far["w"] == pytest.approx(near["w"] * 4, rel=0.02)


def test_view_changes_notify_once_per_change(canvas_page):
    canvas_page.evaluate("window.__seen = []; "
                         "CC.canvas.init({viewport: document.getElementById('viewport'),"
                         " world: document.getElementById('world'),"
                         " onViewChange: v => window.__seen.push(v.scale)})")
    canvas_page.evaluate("CC.canvas.zoomBy(1.2, 100, 100); CC.canvas.zoomBy(1.2, 100, 100)")
    assert len(canvas_page.evaluate("window.__seen")) == 2

def test_a_click_reaches_a_card_rather_than_being_swallowed_by_panning(canvas_page):
    """Capturing the pointer on pointerdown retargets the following click to
    the viewport. That silently killed every card interaction while scripted
    element.click() kept working, so this drives a real mouse."""
    canvas_page.evaluate("""
        const probe = document.createElement('div');
        probe.id = 'click-probe';
        probe.style.cssText = 'position:absolute;left:40px;top:40px;width:120px;height:60px';
        window.__hits = 0;
        probe.addEventListener('click', () => { window.__hits++; });
        document.getElementById('cards').appendChild(probe);
        void 0;
    """)
    box = canvas_page.locator("#click-probe").bounding_box()
    canvas_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert canvas_page.evaluate("window.__hits") == 1


def test_a_press_that_barely_moves_is_still_a_click(canvas_page):
    """Hands shake. A two pixel wobble must not become a pan."""
    canvas_page.evaluate("""
        const probe = document.createElement('div');
        probe.id = 'jitter-probe';
        probe.style.cssText = 'position:absolute;left:40px;top:40px;width:120px;height:60px';
        window.__hits = 0;
        probe.addEventListener('click', () => { window.__hits++; });
        document.getElementById('cards').appendChild(probe);
        void 0;
    """)
    before = canvas_page.evaluate("CC.canvas.getView()")
    box = canvas_page.locator("#jitter-probe").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    canvas_page.mouse.move(x, y)
    canvas_page.mouse.down()
    canvas_page.mouse.move(x + 2, y + 1)
    canvas_page.mouse.up()
    assert canvas_page.evaluate("window.__hits") == 1
    assert canvas_page.evaluate("CC.canvas.getView()") == before


def test_a_drag_past_the_threshold_still_pans(canvas_page):
    before = canvas_page.evaluate("CC.canvas.getView()")
    canvas_page.mouse.move(500, 400)
    canvas_page.mouse.down()
    canvas_page.mouse.move(430, 330)
    canvas_page.mouse.up()
    after = canvas_page.evaluate("CC.canvas.getView()")
    assert after["x"] < before["x"] and after["y"] < before["y"]


def test_a_container_laid_out_differently_breaks_edges_that_cross_it(graph_page):
    """Why the ribbon in a flat package is still unsolved.

    rectpacking gives that container a usable shape - 59,083 by 534 becomes
    4,505 by 4,881 - but ELK cannot route an edge between two subtrees laid
    out by different algorithms, and something outside almost always calls in.
    This pins the failure so the fix is not attempted that way twice: it must
    throw, and any future approach has to keep one algorithm throughout.
    """
    failed = graph_page.evaluate("""async () => {
        const tree = {
            id: 'root',
            layoutOptions: {'elk.algorithm': 'layered', 'elk.direction': 'DOWN',
                            'elk.hierarchyHandling': 'INCLUDE_CHILDREN'},
            children: [
                {id: 'flat', layoutOptions: {'elk.algorithm': 'rectpacking'},
                 children: [{id: 'a', width: 210, height: 54}]},
                {id: 'other', children: [{id: 'b', width: 210, height: 54}]},
            ],
            edges: [{id: 'e0', sources: ['b'], targets: ['a']}],
        };
        try { await new ELK().layout(tree); return null; }
        catch (e) { return String(e); }
    }""")
    assert failed and "could not be found" in failed
