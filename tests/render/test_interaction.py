from __future__ import annotations


def test_clicking_a_collapsed_container_expands_it(graph_page):
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 0
    graph_page.locator(".card[data-id='app.cli'] .card-head").click()
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 1


def test_double_clicking_an_expanded_container_collapses_it(graph_page):
    graph_page.evaluate("CC.view.toggle('app.cli')")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.locator(".card[data-id='app.cli'] .card-head").dblclick()
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator(".card[data-id='app.cli.main']").count() == 0


def test_expanding_a_module_reroutes_its_edges_to_the_functions(graph_page):
    graph_page.evaluate("CC.view.toggle('app.cli')")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator("#edges path[data-edge='app.cli->app.mail']").count() == 0
    assert graph_page.locator("#edges path[data-edge='app.cli.main->app.mail']").count() == 1


def test_selecting_a_card_opens_the_detail_panel(graph_page):
    graph_page.evaluate("CC.view.toggle('app.cli')")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.locator(".card[data-id='app.cli.main'] .card-head").click()
    panel = graph_page.locator("#panel")
    assert panel.is_visible()
    assert panel.locator("h2").inner_text() == "app.cli.main"
    assert "(argv=None)" in panel.locator(".sig").inner_text()
    assert "app/cli.py:4" in panel.locator(".path").inner_text()


def test_the_panel_lists_callers_and_callees(graph_page):
    graph_page.evaluate("CC.panel.show('app.mail.Mailer.send')")
    panel = graph_page.locator("#panel")
    assert panel.locator("[data-role='callers'] li").all_inner_texts() == ["app.cli.main"]
    assert (panel.locator("[data-role='callees'] li").all_inner_texts()
            == ["app.mail.Mailer.retry"])


def test_the_panel_offers_a_copyable_path_and_an_editor_link(graph_page):
    graph_page.evaluate("CC.panel.show('app.cli.main')")
    link = graph_page.locator("#panel a[data-role='editor']")
    assert link.get_attribute("href").startswith("vscode://file/")
    assert "app/cli.py:4" in link.get_attribute("href")
    assert graph_page.locator("#panel button[data-role='copy-path']").count() == 1


def test_a_callee_link_navigates_to_that_node(graph_page):
    graph_page.evaluate("CC.panel.show('app.cli.main')")
    graph_page.locator("#panel [data-role='callees'] li a").first.click()
    graph_page.wait_for_function("CC.view.selected() === 'app.cli.load_config'")
    assert graph_page.locator("#panel h2").inner_text() == "app.cli.load_config"


def test_the_neighbourhood_is_computed_on_the_full_graph(graph_page):
    """A callee hidden in a collapsed module is still reachable."""
    reached = graph_page.evaluate("Array.from(CC.focus.neighbourhood('app.cli.main', 1)).sort()")
    assert reached == ["app.cli.load_config", "app.cli.main", "app.mail.Mailer.send"]


def test_more_hops_reach_further(graph_page):
    two = graph_page.evaluate("Array.from(CC.focus.neighbourhood('app.cli.main', 2)).sort()")
    assert "app.mail.Mailer.retry" in two


def test_the_neighbourhood_reaches_upstream_as_well_as_down(graph_page):
    reached = graph_page.evaluate(
        "Array.from(CC.focus.neighbourhood('app.mail.Mailer.send', 1)).sort()")
    assert "app.cli.main" in reached          # caller
    assert "app.mail.Mailer.retry" in reached  # callee


def test_a_cycle_does_not_hang_the_traversal(graph_page):
    graph_page.evaluate("""
        CC.view.state.data.edges.push(
          {source: 'app.cli.load_config', target: 'app.cli.main',
           confidence: 'resolved', sites: [{line: 12, cond: false, loop: false}]});
    """)
    assert "app.cli.main" in graph_page.evaluate(
        "Array.from(CC.focus.neighbourhood('app.cli.main', 5))")


def test_focus_dims_exactly_the_complement_of_the_neighbourhood(graph_page):
    graph_page.evaluate("CC.view.toggle('app.cli')")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.focus.set('app.cli.main', 1)")
    assert "dimmed" not in graph_page.locator(
        ".card[data-id='app.cli.main']").get_attribute("class")
    assert "dimmed" not in graph_page.locator(
        ".card[data-id='app.cli.load_config']").get_attribute("class")
    # app.mail is the visible representative of a reached callee, so it stays lit.
    assert "dimmed" not in graph_page.locator(
        ".card[data-id='app.mail']").get_attribute("class")


def test_clearing_focus_undims_everything(graph_page):
    graph_page.evaluate("CC.focus.set('app.cli.main', 1); CC.focus.clear()")
    assert graph_page.locator("#cards .card.dimmed").count() == 0


def test_focus_survives_expanding_a_container(graph_page):
    graph_page.evaluate("CC.focus.set('app.cli.main', 1)")
    graph_page.evaluate("CC.view.toggle('app.mail')")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator("#cards .card.dimmed").count() > 0

def _click_head(page, node_id):
    box = page.locator(f".card[data-id='{node_id}'] .card-head").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def test_the_panel_has_a_visible_way_to_close_it(graph_page):
    """Selecting dims most of the graph and covers a strip of canvas. Leaving
    a keyboard shortcut as the only way out strands anyone who does not know
    about it."""
    _click_head(graph_page, "app.cli")
    graph_page.wait_for_function("CC.view.ready === true")
    _click_head(graph_page, "app.cli.main")
    assert graph_page.locator("#panel").is_visible()

    close = graph_page.locator("#panel button[data-role='close']")
    assert close.count() == 1
    box = close.bounding_box()
    graph_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    assert graph_page.locator("#panel").is_hidden()
    assert graph_page.evaluate("CC.view.selected()") is None
    assert graph_page.locator("#cards .card.dimmed").count() == 0
    assert graph_page.locator("#cards .card.selected").count() == 0


def test_clicking_the_background_clears_the_selection(graph_page):
    _click_head(graph_page, "app.cli")
    graph_page.wait_for_function("CC.view.ready === true")
    _click_head(graph_page, "app.cli.main")
    assert graph_page.locator("#panel").is_visible()

    # Pick a point the layout genuinely leaves empty rather than guessing.
    spot = graph_page.evaluate("""() => {
        const box = document.getElementById('viewport').getBoundingClientRect();
        for (let y = box.bottom - 12; y > box.top + 12; y -= 20) {
            for (let x = box.left + 12; x < box.right - 12; x += 20) {
                const el = document.elementFromPoint(x, y);
                if (el && !el.closest('.card') && !el.closest('#panel')) {
                    return {x: x, y: y};
                }
            }
        }
        return null;
    }""")
    assert spot, "no empty canvas to click"
    graph_page.mouse.click(spot["x"], spot["y"])

    assert graph_page.locator("#panel").is_hidden()
    assert graph_page.evaluate("CC.view.selected()") is None
    assert graph_page.locator("#cards .card.dimmed").count() == 0


def test_a_drag_on_the_background_keeps_the_selection(graph_page):
    """Panning ends with a click event on the background. Treating that as
    clicking away would clear the selection every time the user moved the
    canvas."""
    _click_head(graph_page, "app.cli")
    graph_page.wait_for_function("CC.view.ready === true")
    _click_head(graph_page, "app.cli.main")
    selected = graph_page.evaluate("CC.view.selected()")

    graph_page.mouse.move(200, 880)
    graph_page.mouse.down()
    graph_page.mouse.move(320, 800)
    graph_page.mouse.up()

    assert graph_page.evaluate("CC.view.selected()") == selected
    assert graph_page.locator("#panel").is_visible()


def test_escape_clears_the_selection_not_just_the_panel(graph_page):
    _click_head(graph_page, "app.cli")
    graph_page.wait_for_function("CC.view.ready === true")
    _click_head(graph_page, "app.cli.main")
    graph_page.locator("body").press("Escape")

    assert graph_page.locator("#panel").is_hidden()
    assert graph_page.evaluate("CC.view.selected()") is None
    assert graph_page.locator("#cards .card.selected").count() == 0

