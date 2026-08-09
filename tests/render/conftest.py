from __future__ import annotations

import pytest

from codecards.render.bundle import render_html

pytest.importorskip("playwright")

#: A graph with everything the renderer has to handle: a package, two modules,
#: a class, a method, a cross-module call in a loop inside a conditional, an
#: inferred edge, an ambiguous edge, and an orphan.
SAMPLE = {
    "meta": {"version": "0.0.0", "generated": "", "maxDepth": 15, "hasSource": True},
    "nodes": [
        {"id": "app", "kind": "package", "name": "app", "parent": None,
         "file": None, "lineStart": None, "lineEnd": None,
         "signature": None, "summary": None, "decorators": []},
        {"id": "app.cli", "kind": "module", "name": "cli", "parent": "app",
         "file": "app/cli.py", "lineStart": 1, "lineEnd": 12,
         "signature": None, "summary": None, "decorators": []},
        {"id": "app.cli.main", "kind": "function", "name": "main", "parent": "app.cli",
         "file": "app/cli.py", "lineStart": 4, "lineEnd": 9,
         "signature": "(argv=None)", "summary": "Run the tool.", "decorators": [],
         "source": "def main(argv=None):\n"
                   "    cfg = load_config()\n"
                   "    for user in cfg.users:\n"
                   "        if user.active:\n"
                   "            mailer.send(user)\n"
                   "    return 0",
         "tokens": [[[0, 3, "kw"], [4, 4, "def"]], [[10, 11, "call"]],
                    [[4, 3, "kw"], [13, 2, "kw"]], [[8, 2, "kw"]],
                    [[19, 4, "call"]], [[4, 6, "kw"], [11, 1, "num"]]]},
        {"id": "app.mail", "kind": "module", "name": "mail", "parent": "app",
         "file": "app/mail.py", "lineStart": 1, "lineEnd": 20,
         "signature": None, "summary": None, "decorators": []},
        {"id": "app.mail.Mailer", "kind": "class", "name": "Mailer", "parent": "app.mail",
         "file": "app/mail.py", "lineStart": 1, "lineEnd": 20,
         "signature": None, "summary": None, "decorators": []},
        {"id": "app.mail.Mailer.send", "kind": "method", "name": "send",
         "parent": "app.mail.Mailer", "file": "app/mail.py", "lineStart": 2, "lineEnd": 5,
         "signature": "(self, user)", "summary": "Deliver a message.", "decorators": [],
         "source": "    def send(self, user):\n        return post(user)",
         "tokens": [[[4, 3, "kw"], [8, 4, "def"]], [[8, 6, "kw"], [15, 4, "call"]]]},
        {"id": "app.mail.Mailer.retry", "kind": "method", "name": "retry",
         "parent": "app.mail.Mailer", "file": "app/mail.py", "lineStart": 7, "lineEnd": 8,
         "signature": "(self)", "summary": None, "decorators": [],
         "source": "    def retry(self):\n        pass", "tokens": [[], []]},
        {"id": "app.cli.load_config", "kind": "function", "name": "load_config",
         "parent": "app.cli", "file": "app/cli.py", "lineStart": 11, "lineEnd": 12,
         "signature": "()", "summary": None, "decorators": [],
         "source": "def load_config():\n    return {}", "tokens": [[], []]},
    ],
    "edges": [
        {"source": "app.cli.main", "target": "app.cli.load_config",
         "confidence": "resolved", "sites": [{"line": 5, "cond": False, "loop": False}]},
        {"source": "app.cli.main", "target": "app.mail.Mailer.send",
         "confidence": "inferred", "sites": [{"line": 8, "cond": True, "loop": True}]},
        {"source": "app.mail.Mailer.send", "target": "app.mail.Mailer.retry",
         "confidence": "ambiguous", "sites": [{"line": 3, "cond": False, "loop": False}]},
    ],
    "entryPoints": [{"id": "app.cli.main", "reasons": ["main_block"]}],
    "orphans": ["app.mail.Mailer.retry"],
    "initialView": {
        "collapsed": ["app.cli", "app.mail"],
        "visible": ["app", "app.cli", "app.mail"],
        "edges": [{"source": "app.cli", "target": "app.mail",
                   "confidence": "inferred", "weight": 1, "tiers": {"inferred": 1}}],
        "internalCounts": {"app.cli": 1, "app.mail": 1},
    },
    "goldenTrace": {
        "entryId": "app.cli.main",
        "steps": [
            {"index": 0, "callerId": "app.cli.main", "calleeId": "app.cli.load_config",
             "line": 5, "depth": 0, "stack": ["app.cli.main"], "confidence": "resolved",
             "cond": False, "loop": False, "recursive": False},
            {"index": 1, "callerId": "app.cli.main", "calleeId": "app.mail.Mailer.send",
             "line": 8, "depth": 0, "stack": ["app.cli.main"], "confidence": "inferred",
             "cond": True, "loop": True, "recursive": False},
            {"index": 2, "callerId": "app.mail.Mailer.send",
             "calleeId": "app.mail.Mailer.retry", "line": 3, "depth": 1,
             "stack": ["app.cli.main", "app.mail.Mailer.send"], "confidence": "ambiguous",
             "cond": False, "loop": False, "recursive": False},
        ],
    },
    "stats": {"totalCalls": 4, "byConfidence": {"resolved": 1, "inferred": 1,
              "ambiguous": 1, "unresolved": 1}, "resolutionRate": 0.25,
              "callableCount": 4, "edgeCount": 3,
              "skipped": [{"path": "app/broken.py", "reason": "syntax error at line 3"}]},
}


@pytest.fixture
def graph_page(tmp_path, page):
    """A loaded page with the sample graph, laid out and painted."""
    out = tmp_path / "graph.html"
    out.write_text(render_html(SAMPLE), encoding="utf-8")
    page.goto(out.as_uri())
    page.wait_for_function("window.CC && CC.view && CC.view.ready === true")
    return page
