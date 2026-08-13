from __future__ import annotations


def test_the_entry_dropdown_is_grouped_by_detection_reason(graph_page):
    assert graph_page.locator("#entry-select optgroup").count() >= 1
    assert graph_page.locator("#entry-select option[value='app.cli.main']").count() == 1


def test_choosing_an_entry_point_starts_the_walkthrough(graph_page):
    graph_page.select_option("#entry-select", "app.cli.main")
    graph_page.wait_for_function("CC.player.state.steps.length > 0")
    assert graph_page.locator("#transport").is_visible()


def test_ambiguous_edges_are_hidden_by_default(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator("#edges path.ambiguous").count() == 0
    assert graph_page.locator("#edges path.resolved").count() > 0


def test_the_ambiguous_toggle_reveals_them(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.check("#show-ambiguous")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator("#edges path.ambiguous").count() > 0


def test_unchecking_inferred_hides_inferred_edges(graph_page):
    graph_page.uncheck("#show-inferred")
    graph_page.wait_for_function("CC.view.ready === true")
    assert graph_page.locator("#edges path.inferred").count() == 0


def test_search_selects_a_matching_node(graph_page):
    graph_page.fill("#search", "load_config")
    graph_page.keyboard.press("Enter")
    graph_page.wait_for_function("CC.view.selected() === 'app.cli.load_config'")
    assert graph_page.locator("#panel h2").inner_text() == "app.cli.load_config"


def test_search_that_matches_nothing_says_so_rather_than_doing_nothing(graph_page):
    graph_page.fill("#search", "zzzznotathing")
    graph_page.keyboard.press("Enter")
    assert "no match" in graph_page.locator("#statusbar").inner_text().lower()


def test_the_theme_toggle_flips_the_root_attribute(graph_page):
    assert graph_page.get_attribute("html", "data-theme") == "dark"
    graph_page.click("#theme")
    assert graph_page.get_attribute("html", "data-theme") == "light"


def test_the_info_panel_reports_the_resolution_rate_and_skipped_files(graph_page):
    graph_page.click("#info")
    text = graph_page.locator("#info-panel").inner_text()
    assert "25" in text                      # resolutionRate 0.25
    assert "app/broken.py" in text
    assert "syntax error at line 3" in text


def test_the_status_bar_states_the_totals_on_load(graph_page):
    text = graph_page.locator("#statusbar").inner_text()
    assert "4 callables" in text
    assert "1 file skipped" in text


def test_space_toggles_playback(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    graph_page.locator("body").press(" ")
    assert graph_page.evaluate("CC.player.state.playing") is True
    graph_page.locator("body").press(" ")
    assert graph_page.evaluate("CC.player.state.playing") is False


def test_the_arrow_keys_step(graph_page):
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.wait_for_function("CC.player.state.index === 0")
    graph_page.locator("body").press("ArrowRight")
    graph_page.wait_for_function("CC.player.state.index === 1")
    graph_page.locator("body").press("ArrowLeft")
    graph_page.wait_for_function("CC.player.state.index === 0")


def test_typing_in_the_search_box_does_not_trigger_shortcuts(graph_page):
    """Space in a text field must type a space, not start playback."""
    graph_page.evaluate("CC.player.start('app.cli.main')")
    graph_page.click("#search")
    graph_page.keyboard.type("load config")
    assert graph_page.evaluate("CC.player.state.playing") is False
    assert graph_page.input_value("#search") == "load config"


def test_escape_clears_focus_and_closes_the_panel(graph_page):
    graph_page.evaluate("CC.view.select('app.cli')")
    graph_page.locator("body").press("Escape")
    assert graph_page.locator("#panel").is_hidden()
    assert graph_page.locator("#cards .card.dimmed").count() == 0


def test_the_focus_slider_widens_the_neighbourhood(graph_page):
    graph_page.evaluate("CC.view.layout(new Set())")
    graph_page.wait_for_function("CC.view.ready === true")
    graph_page.evaluate("CC.focus.set('app.cli.main', 1)")
    narrow = graph_page.locator("#cards .card.dimmed").count()
    graph_page.fill("#focus-hops", "5")
    graph_page.dispatch_event("#focus-hops", "input")
    assert graph_page.locator("#cards .card.dimmed").count() < narrow


def test_the_page_loads_without_a_single_console_error(
    tmp_path, page, sample_viewmodel
):
    """`tests` is not a package, so importing SAMPLE directly raises
    ModuleNotFoundError. conftest exposes it as a fixture instead."""
    from codecards.render.bundle import render_html

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    out = tmp_path / "clean.html"
    out.write_text(render_html(sample_viewmodel), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    assert errors == []


def _page_with_a_dunder(tmp_path, page, sample_viewmodel):
    """The shared fixture has no special methods, so add one plus a call into
    it, the way a constructor call is retargeted onto __init__."""
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    vm["nodes"].append({
        "id": "app.mail.Mailer.__init__", "kind": "method", "name": "__init__",
        "parent": "app.mail.Mailer", "file": "app/mail.py",
        "lineStart": 10, "lineEnd": 11, "signature": "(self)",
        "summary": None, "decorators": [], "implicit": True, "dunder": True,
    })
    vm["edges"].append({
        "source": "app.cli.main", "target": "app.mail.Mailer.__init__",
        "confidence": "resolved", "sites": [{"line": 6, "cond": False, "loop": False}],
    })
    out = tmp_path / "dunder.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    page.evaluate("CC.view.layout(new Set())")
    page.wait_for_function("CC.view.ready === true")
    page.wait_for_timeout(200)
    return page


def _dunder_cards(page):
    return page.evaluate(
        "Array.from(document.querySelectorAll('#cards .card'))"
        ".filter(c => c.dataset.id.endsWith('__init__')).map(c => c.dataset.id)")


def test_special_methods_are_hidden_by_default(tmp_path, page, sample_viewmodel):
    page = _page_with_a_dunder(tmp_path, page, sample_viewmodel)
    assert _dunder_cards(page) == []
    assert page.is_checked("#show-dunders") is False


def test_the_dunder_toggle_reveals_them(tmp_path, page, sample_viewmodel):
    page = _page_with_a_dunder(tmp_path, page, sample_viewmodel)
    page.check("#show-dunders")
    page.wait_for_function("CC.view.ready === true")
    page.wait_for_timeout(200)
    assert _dunder_cards(page) == ["app.mail.Mailer.__init__"]


def test_hiding_a_dunder_folds_its_edge_onto_the_class(tmp_path, page, sample_viewmodel):
    """__init__ has real callers, since constructor calls retarget onto it.
    Dropping the edge would delete "something builds a Mailer" from the graph,
    so it re-points at the nearest visible ancestor instead."""
    page = _page_with_a_dunder(tmp_path, page, sample_viewmodel)
    assert page.locator(
        "#edges path[data-edge='app.cli.main->app.mail.Mailer.__init__']").count() == 0
    assert page.locator(
        "#edges path[data-edge='app.cli.main->app.mail.Mailer']").count() == 1

    page.check("#show-dunders")
    page.wait_for_function("CC.view.ready === true")
    page.wait_for_timeout(200)
    assert page.locator(
        "#edges path[data-edge='app.cli.main->app.mail.Mailer.__init__']").count() == 1


def test_a_huge_entry_point_group_is_capped(tmp_path, page, sample_viewmodel):
    """"nothing calls it" is a structural fallback, so on a large library it
    matches thousands of functions. scikit-learn produced 6,014 of them and a
    menu nobody could use."""
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    vm["entryPoints"] = [{"id": f"app.mod.fn{i}", "reasons": ["no_callers"]}
                         for i in range(200)]
    out = tmp_path / "many.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")

    # one blank "Walk through..." option plus the capped group
    assert page.locator("#entry-select option").count() == 51
    label = page.evaluate("document.querySelector('#entry-select optgroup').label")
    assert "50 of 200" in label, label


def test_a_small_group_is_not_labelled_with_a_count(tmp_path, page, sample_viewmodel):
    from codecards.render.bundle import render_html

    vm = sample_viewmodel
    out = tmp_path / "few.html"
    out.write_text(render_html(vm), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("CC.view.ready === true")
    label = page.evaluate("document.querySelector('#entry-select optgroup').label")
    assert "of" not in label, label


def test_the_info_panel_explains_the_visual_conventions(graph_page):
    """Solid versus dashed, the badges, the gutter glyphs and the chips were
    not written down anywhere in the interface."""
    graph_page.click("#info")
    text = graph_page.locator("#info-panel").inner_text().lower()
    assert "how to read it" in text
    for phrase in ("solid line", "dashed line", "entry", "unused",
                   "conditional", "loop", "package"):
        assert phrase in text, phrase
