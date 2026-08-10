from __future__ import annotations

import json
import re

from codecards.render.bundle import ASSETS_DIR, JS_ORDER, render_html, write_html

VIEWMODEL = {
    "meta": {"version": "0.1.0", "generated": "2026-08-09T00:00:00+00:00",
             "maxDepth": 15, "hasSource": False},
    "nodes": [{"id": "m", "kind": "module", "name": "m", "parent": None}],
    "edges": [],
    "entryPoints": [],
    "orphans": [],
    "initialView": {"collapsed": [], "visible": ["m"], "edges": [], "internalCounts": {}},
    "goldenTrace": None,
    "stats": {"totalCalls": 0, "byConfidence": {}, "resolutionRate": 0.0,
              "callableCount": 0, "edgeCount": 0, "skipped": []},
}


def test_elkjs_is_vendored():
    assert (ASSETS_DIR / "vendor" / "elk.bundled.js").is_file(), "run scripts/vendor_assets.sh"


def test_no_graph_library_was_reintroduced():
    """Cards are DOM. A canvas-drawing library cannot host selectable code."""
    vendored = {p.name for p in (ASSETS_DIR / "vendor").glob("*.js")}
    assert vendored == {"elk.bundled.js"}, f"unexpected vendored library: {vendored}"


def test_every_app_script_is_present():
    for name in JS_ORDER:
        assert (ASSETS_DIR / "js" / name).is_file()


def test_output_contains_no_external_urls():
    html = render_html(VIEWMODEL)
    offenders = re.findall(r'(?:src|href)\s*=\s*["\'](https?:)?//[^"\']+', html)
    assert offenders == [], f"external references found: {offenders}"
    assert "cdn." not in html.lower()


def test_no_placeholders_survive():
    assert "__CODECARDS_" not in render_html(VIEWMODEL)


def test_the_viewmodel_round_trips_through_the_page():
    html = render_html(VIEWMODEL)
    match = re.search(r"window\.CODECARDS_DATA\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    assert match, "embedded data block not found"
    assert json.loads(match.group(1))["nodes"][0]["id"] == "m"


def test_script_terminators_in_data_are_escaped():
    payload = dict(VIEWMODEL)
    payload["nodes"] = [{"id": "m", "kind": "module", "name": "</script><b>x", "parent": None}]
    html = render_html(payload)
    assert "</script><b>x" not in html
    assert "<\\/script>" in html


def test_embedded_source_survives_a_backslash_and_a_newline():
    """Source is embedded now, so the escaping path carries real code."""
    payload = dict(VIEWMODEL)
    payload["nodes"] = [{"id": "m", "kind": "function", "name": "m", "parent": None,
                         "source": "s = '\\\\n'\nreturn s", "tokens": [[], []]}]
    html = render_html(payload)
    match = re.search(r"window\.CODECARDS_DATA\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    assert json.loads(match.group(1))["nodes"][0]["source"] == "s = '\\\\n'\nreturn s"


def test_scripts_are_inlined_in_the_right_order():
    """Vendor, then data, then app. Matching on the substring "elk" would pass
    or fail on any incidental occurrence, so this pins structure and size."""
    html = render_html(VIEWMODEL)
    vendor_at = html.index("<script>")
    data_at = html.index("window.CODECARDS_DATA")
    app_at = html.index("__cc_boot")
    assert vendor_at < data_at < app_at
    assert data_at - vendor_at > 500_000, "elkjs does not appear to be inlined"


def test_write_html_creates_parent_directories(tmp_path):
    out = tmp_path / "nested" / "graph.html"
    write_html(VIEWMODEL, out)
    assert out.is_file()
    assert out.read_text().startswith("<!doctype html>")


def test_cli_writes_a_file(tmp_path):
    from codecards.cli import main
    (tmp_path / "m.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    out = tmp_path / "graph.html"
    assert main([str(tmp_path), "-o", str(out), "--quiet"]) == 0
    assert out.is_file() and out.stat().st_size > 100_000

def test_a_placeholder_inside_the_embedded_data_is_not_substituted():
    """codecards analysing codecards embeds bundle.py's own source, which
    contains the placeholder strings. Sequential replaces rescanned their own
    output and injected raw JavaScript into the middle of a JSON string, so
    the page loaded with window.CODECARDS_DATA undefined."""
    import json as _json

    booby_trapped = dict(VIEWMODEL)
    booby_trapped["nodes"] = [{
        "id": "m.render_html", "kind": "function", "name": "render_html",
        "parent": None,
        "source": ('("/*__CODECARDS_CSS__*/", css),\n'
                   '("/*__CODECARDS_VENDOR__*/", vendor),\n'
                   '("/*__CODECARDS_DATA__*/", data),\n'
                   '("/*__CODECARDS_APP__*/", app),'),
        "tokens": [[], [], [], []],
    }]
    html = render_html(booby_trapped)

    start = html.index("window.CODECARDS_DATA = ")
    end = html.index("</script>", start)
    block = html[start:end]
    assert "\n" not in block.rstrip("\n"), (
        "a replacement was injected into the data block, breaking the JSON"
    )
    blob = block[len("window.CODECARDS_DATA = "):].rstrip().rstrip(";")
    assert _json.loads(blob)["nodes"][0]["source"].count("__CODECARDS_") == 4


def test_every_placeholder_is_filled_exactly_once():
    html = render_html(VIEWMODEL)
    for name in ("CSS", "VENDOR", "DATA", "APP"):
        assert f"/*__CODECARDS_{name}__*/" not in html

