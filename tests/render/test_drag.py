"""Moving things on the canvas.

Driven with a real mouse rather than scripted events. The canvas captures the
pointer to pan and a card sits inside the panning surface, so a drag that works
when dispatched directly can still pan the background under the user's hand.
"""

from __future__ import annotations


def _open_cli(page):
    page.evaluate("CC.view.toggle('app.cli')")
    page.wait_for_function("CC.view.ready === true")
    page.evaluate(
        """() => {
            const box = CC.view.state.boxes['app.cli'];
            CC.canvas.setView({x: 80 - box.x, y: 80 - box.y, scale: 1.0});
        }"""
    )
    page.wait_for_timeout(120)


def _box(page, node_id):
    return page.evaluate("(id) => ({...CC.view.state.boxes[id]})", node_id)


def _drag(page, selector, dx, dy):
    box = page.locator(selector).bounding_box()
    start_x = box["x"] + min(box["width"] / 2, 40)
    start_y = box["y"] + 10
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + dx, start_y + dy, steps=8)
    page.mouse.up()


def _contains(outer, inner):
    return (
        inner["x"] >= outer["x"]
        and inner["y"] >= outer["y"]
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"]
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"]
    )


def test_dragging_a_card_moves_it(graph_page):
    _open_cli(graph_page)
    before = _box(graph_page, "app.cli.main")
    _drag(graph_page, ".card[data-id='app.cli.main']", 90, 70)
    after = _box(graph_page, "app.cli.main")
    assert round(after["x"] - before["x"]) == 90
    assert round(after["y"] - before["y"]) == 70


def test_the_plate_refits_so_a_moved_card_stays_inside_its_module(graph_page):
    """The whole reason containment is recomputed rather than asserted: drag a
    function anywhere and "inside app.cli" has to still be literally true."""
    _open_cli(graph_page)
    _drag(graph_page, ".card[data-id='app.cli.main']", 160, 130)
    assert _contains(_box(graph_page, "app.cli"), _box(graph_page, "app.cli.main"))


def test_dragging_a_plate_moves_everything_it_holds(graph_page):
    _open_cli(graph_page)
    members = graph_page.evaluate(
        "CC.view.movableUnder('app.cli').filter(id => CC.view.state.boxes[id])"
    )
    assert len(members) > 1, "app.cli should hold more than one card"
    before = {m: _box(graph_page, m) for m in members}

    plate = graph_page.locator(".card[data-id='app.cli']").bounding_box()
    # The plate's own ground, clear of its label and of any member card.
    start_x = plate["x"] + plate["width"] - 14
    start_y = plate["y"] + plate["height"] - 8
    page = graph_page
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 100, start_y + 60, steps=8)
    page.mouse.up()

    shifts = {m: round(_box(page, m)["x"] - before[m]["x"]) for m in members}
    assert set(shifts.values()) == {100}, f"members moved apart: {shifts}"


def test_a_click_still_expands_rather_than_counting_as_a_drag(graph_page):
    """A press that wanders a pixel or two is a click, not a move. Without a
    threshold, expanding a module by clicking it would stop working for anyone
    whose hand is not perfectly still."""
    graph_page.evaluate("CC.view.layout(new Set(['app.cli', 'app.mail']))")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 0

    box = graph_page.locator(".card[data-id='app.cli'] .card-head").bounding_box()
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    graph_page.mouse.move(x, y)
    graph_page.mouse.down()
    graph_page.mouse.move(x + 2, y + 1)
    graph_page.mouse.up()
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 1


def test_dragging_a_card_does_not_pan_the_canvas(graph_page):
    _open_cli(graph_page)
    before = graph_page.evaluate("CC.canvas.getView()")
    _drag(graph_page, ".card[data-id='app.cli.main']", 70, 50)
    assert graph_page.evaluate("CC.canvas.getView()") == before


def test_reset_returns_every_card_to_the_computed_layout(graph_page):
    _open_cli(graph_page)
    original = _box(graph_page, "app.cli.main")
    _drag(graph_page, ".card[data-id='app.cli.main']", 140, 110)
    assert _box(graph_page, "app.cli.main")["x"] != original["x"]

    graph_page.click("#reset-layout")
    graph_page.wait_for_function("CC.view.ready === true")
    restored = _box(graph_page, "app.cli.main")
    assert round(restored["x"]) == round(original["x"])
    assert round(restored["y"]) == round(original["y"])
