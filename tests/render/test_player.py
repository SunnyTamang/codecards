from __future__ import annotations


def test_the_trace_matches_the_shipped_golden(graph_page):
    """The drift alarm. If this fails, trace.js and walkthrough.py disagree."""
    golden = graph_page.evaluate("CODECARDS_DATA.goldenTrace")
    computed = graph_page.evaluate(
        "CC.trace.build(CODECARDS_DATA.edges, CODECARDS_DATA.goldenTrace.entryId,"
        " CODECARDS_DATA.meta.maxDepth)")
    assert [s["calleeId"] for s in computed] == [s["calleeId"] for s in golden["steps"]]
    assert [s["depth"] for s in computed] == [s["depth"] for s in golden["steps"]]
    assert [s["stack"] for s in computed] == [s["stack"] for s in golden["steps"]]


def test_steps_are_ordered_by_call_site_line(graph_page):
    steps = graph_page.evaluate(
        "CC.trace.build(CODECARDS_DATA.edges, 'app.cli.main', 15)")
    assert [s["line"] for s in steps][:2] == [5, 8]


def test_recursion_is_marked_and_not_re_entered(graph_page):
    steps = graph_page.evaluate("""
        CC.trace.build([{source: 'a', target: 'a', confidence: 'resolved',
                         sites: [{line: 2, cond: false, loop: false}]}], 'a', 15)
    """)
    assert len(steps) == 1
    assert steps[0]["recursive"] is True


def test_the_depth_cap_is_honoured(graph_page):
    steps = graph_page.evaluate("""
        CC.trace.build([
          {source: 'a', target: 'b', confidence: 'resolved', sites: [{line: 1}]},
          {source: 'b', target: 'c', confidence: 'resolved', sites: [{line: 1}]},
          {source: 'c', target: 'd', confidence: 'resolved', sites: [{line: 1}]},
        ], 'a', 2)
    """)
    assert [s["calleeId"] for s in steps] == ["b", "c"]


def test_starting_the_player_shows_the_transport(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    assert graph_page.locator("#transport").is_visible()
    assert graph_page.locator("#breadcrumb").is_visible()


def test_the_first_step_activates_the_caller_and_the_callee(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    assert graph_page.locator(".card.active").count() >= 1


def test_a_step_pins_the_active_card_to_source_tier(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    assert graph_page.evaluate("CC.zoom.isPinned('app.cli.main')") is True


def test_a_step_highlights_the_exact_calling_line(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    active = graph_page.locator(".card[data-id='app.cli.main'] .src-line.step-active")
    assert active.count() == 1
    assert active.get_attribute("data-line") == "5"


def test_stepping_forward_moves_the_highlight(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main'); CC.player.next()")
    graph_page.wait_for_function("CC.player.state.index === 1")
    active = graph_page.locator(".card[data-id='app.cli.main'] .src-line.step-active")
    assert active.get_attribute("data-line") == "8"


def test_the_breadcrumb_shows_the_call_stack(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.evaluate("CC.player.next(); CC.player.next()")
    graph_page.wait_for_function("CC.player.state.index === 2")
    assert "app.cli.main" in graph_page.locator("#breadcrumb").inner_text()
    assert "Mailer.send" in graph_page.locator("#breadcrumb").inner_text()


def test_the_caption_names_the_site_and_marks_loop_and_conditional(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main'); CC.player.next()")
    graph_page.wait_for_function("CC.player.state.index === 1")
    caption = graph_page.locator("#caption").inner_text()
    assert "app/cli.py:8" in caption
    assert "loop" in caption.lower()
    assert "conditional" in caption.lower()


def test_a_step_auto_expands_a_collapsed_container(graph_page):
    """Playing must not point at a module and call it an explanation."""
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 1


def test_stepping_back_returns_to_the_previous_step(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main'); CC.player.next()")
    graph_page.wait_for_function("CC.player.state.index === 1")
    graph_page.evaluate("CC.player.prev()")
    graph_page.wait_for_function("CC.player.state.index === 0")
    assert graph_page.locator(
        ".card[data-id='app.cli.main'] .src-line.step-active"
    ).get_attribute("data-line") == "5"


def test_stepping_back_from_the_first_step_stays_put(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main'); CC.player.prev()")
    assert graph_page.evaluate("CC.player.state.index") == 0


def test_stepping_past_the_end_stops_rather_than_wrapping(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.evaluate("for (let i = 0; i < 20; i++) CC.player.next()")
    total = graph_page.evaluate("CC.player.state.steps.length")
    assert graph_page.evaluate("CC.player.state.index") == total - 1
    assert graph_page.evaluate("CC.player.state.playing") is False


def test_stopping_clears_the_highlights_and_the_pins(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    graph_page.evaluate("CC.player.stop()")
    assert graph_page.locator("#transport").is_hidden()
    assert graph_page.locator(".card.active").count() == 0
    assert graph_page.evaluate("CC.zoom.isPinned('app.cli.main')") is False


def test_an_entry_point_with_no_calls_reports_rather_than_showing_nothing(graph_page):
    graph_page.evaluate("CC.player.start('app.mail.Mailer.retry')")
    assert "no calls" in graph_page.locator("#caption").inner_text().lower()
