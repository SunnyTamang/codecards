"""Finding one circular dependency on the canvas.

A heavier stroke marks a line; it does not find one. A ring's members can sit
at opposite corners of a graph with two hundred other lines between them, and
tracing it by eye is exactly what the reader cannot do. So the info panel's
list of rings is the control, and picking one takes the reader there.
"""

from __future__ import annotations

import copy

import pytest

from codecards.render.bundle import render_html

pytest.importorskip("playwright")

#: `main` calls `send`, `send` calls `retry`, and `retry` calls back. Three
#: cards, two of them inside a class inside a module, so nothing about the
#: ring is visible until the plates are opened.
RING = ["app.cli.main", "app.mail.Mailer.send", "app.mail.Mailer.retry"]


@pytest.fixture
def ring_page(tmp_path, page, sample_viewmodel):
    data = copy.deepcopy(sample_viewmodel)
    data["edges"].append({
        "source": "app.mail.Mailer.retry", "target": "app.cli.main",
        "confidence": "resolved", "circular": True,
        "sites": [{"line": 8, "cond": False, "loop": False}],
    })
    for edge in data["edges"]:
        if (edge["source"], edge["target"]) in {
            ("app.cli.main", "app.mail.Mailer.send"),
            ("app.mail.Mailer.send", "app.mail.Mailer.retry"),
        }:
            edge["circular"] = True
    data["stats"]["cycles"] = [RING]
    out = tmp_path / "ring.html"
    out.write_text(render_html(data), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("window.CC && CC.view && CC.view.ready === true")
    # Left as it opens, ambiguous edges hidden: one leg of this ring is
    # ambiguous, which is the case the control has to survive.
    return page


def pick_the_ring(page):
    page.click("#info")
    page.click("#info-panel .ring-row")
    page.wait_for_function("CC.view.ready === true")
    page.wait_for_function("document.querySelectorAll('#edges .ringed').length > 0")


def test_the_panel_offers_each_ring_as_a_control(ring_page):
    ring_page.click("#info")
    rows = ring_page.locator("#info-panel .ring-row")
    assert rows.count() == 1
    assert "main" in rows.first.inner_text()


def test_picking_a_ring_opens_the_plates_hiding_it(ring_page):
    """Two of the three members are a method inside a class inside a module.
    Lighting them while the module stays shut produces one lit card and no
    lines, which says less than the panel's own text already did."""
    pick_the_ring(ring_page)
    for member in RING:
        assert ring_page.locator(
            "#cards .card[data-id='" + member + "']").count() == 1


def test_the_rings_own_calls_are_the_only_ones_left_lit(ring_page):
    pick_the_ring(ring_page)
    lit = ring_page.eval_on_selector_all(
        "#edges path[data-edge]:not(.dimmed)",
        "els => els.map(e => e.dataset.edge).sort()")
    assert lit == sorted([
        "app.cli.main->app.mail.Mailer.send",
        "app.mail.Mailer.send->app.mail.Mailer.retry",
        "app.mail.Mailer.retry->app.cli.main",
    ])


def test_a_ring_is_drawn_whole_even_where_the_filters_hide_a_leg(ring_page):
    """`send -> retry` is ambiguous, and ambiguous calls are off by default.
    Lighting three cards and two of the three lines between them draws a
    broken ring, which is worse than not offering the control: the reader
    would be looking at a shape that does not close and told it does."""
    assert ring_page.locator("#show-ambiguous").is_checked() is False
    pick_the_ring(ring_page)
    assert ring_page.locator(
        "#edges path[data-edge='app.mail.Mailer.send->app.mail.Mailer.retry']"
    ).count() == 1


def test_a_ring_of_guesses_still_looks_like_a_guess(ring_page):
    """Amber and weight answer "which line did I ask for". The dash answers
    "is this call a guess", and highlighting must not launder one into the
    other - a whole ring can be built out of guesses."""
    pick_the_ring(ring_page)
    leg = ring_page.locator(
        "#edges path[data-edge='app.mail.Mailer.send->app.mail.Mailer.retry']")
    assert "ambiguous" in (leg.get_attribute("class") or "")
    dashes = leg.evaluate("el => getComputedStyle(el).strokeDasharray")
    assert dashes and dashes != "none"


def test_leaving_the_ring_puts_the_hidden_leg_back_out_of_sight(ring_page):
    """The override is scoped to the ring the reader asked for. Left in place
    it would quietly turn the ambiguous filter off for the whole canvas."""
    pick_the_ring(ring_page)
    ring_page.click("#info-panel .ring-row")
    ring_page.wait_for_function(
        "document.querySelectorAll('#edges .ringed').length === 0")
    assert ring_page.locator("#edges path.ambiguous").count() == 0


def test_a_lit_call_keeps_its_arrowhead(ring_page):
    """An arrow is three marks - dot, line, head - and only the line carried
    the edge id. The other two matched no rule, so every lit edge in an
    isolated view arrived at a head faded to 7%."""
    pick_the_ring(ring_page)
    heads = ring_page.eval_on_selector_all(
        "#edges .edge-head", "els => els.filter(e => !e.classList.contains('dimmed')).length")
    assert heads == 3


def test_everything_outside_the_ring_is_dimmed(ring_page):
    pick_the_ring(ring_page)
    undimmed = ring_page.eval_on_selector_all(
        "#cards .card:not(.dimmed)", "els => els.map(e => e.dataset.id)")
    # The members, plus the plates they sit in: a dimmed frame drawn around a
    # lit region reads as though the region had come loose from its module.
    assert set(RING) <= set(undimmed)
    assert "app.cli.load_config" not in undimmed


def test_picking_the_same_ring_again_puts_the_canvas_back(ring_page):
    pick_the_ring(ring_page)
    ring_page.click("#info-panel .ring-row")
    ring_page.wait_for_function(
        "document.querySelectorAll('#edges .ringed, #cards .dimmed').length === 0")
    assert ring_page.evaluate("CC.focus.ring()") is None


def test_selecting_a_card_replaces_the_ring_rather_than_joining_it(ring_page):
    """Both modes write the same `dimmed` class. Two isolations layered on top
    of each other leave a canvas dimmed with no single thing to clear."""
    pick_the_ring(ring_page)
    ring_page.evaluate("CC.view.select('app.cli.load_config')")
    assert ring_page.evaluate("CC.focus.ring()") is None
    assert ring_page.locator("#edges .ringed").count() == 0
