"""A graph resolved by a language server.

These start a real server. There is no useful way to fake one: everything
worth testing here is about how a real implementation behaves - that pyright
asks the client a question before it will answer any of ours, that a relative
path has no URI, that a server which cannot resolve is different from one that
resolves to somewhere outside the project.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_python")

from codecards.graph.model import CALLABLE_KINDS, Confidence, validate
from codecards.lsp import ServerUnusable, analyze
from codecards.lsp.client import find
from codecards.parse.grammars import PYTHON

pytestmark = pytest.mark.skipif(
    find(PYTHON.lsp_command) is None,
    reason="no Python language server installed")


def project(tmp_path, **files):
    for name, text in files.items():
        path = tmp_path / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


TWO_MODULES = {
    "mail.py": (
        "class Mailer:\n"
        "    def send(self, to):\n"
        "        return to\n"
    ),
    "run.py": (
        "import os\n"
        "\n"
        "from mail import Mailer\n"
        "\n"
        "\n"
        "def deliver(to):\n"
        "    mailer = Mailer()\n"
        "    return mailer.send(to)\n"
        "\n"
        "\n"
        "def cwd():\n"
        "    return os.getcwd()\n"
    ),
}


def test_a_cross_module_call_resolves_without_an_index(tmp_path):
    root = project(tmp_path, **TWO_MODULES)
    graph, _report = analyze([root], embed_source=False)
    validate(graph)
    pairs = {(e.source, e.target) for e in graph.edges}
    assert ("run.deliver", "mail.Mailer.send") in pairs
    assert all(e.confidence is Confidence.RESOLVED for e in graph.edges)


def test_nothing_is_ever_guessed(tmp_path):
    """The whole point of this tier. A server either knows or it does not,
    so no edge may arrive marked inferred or ambiguous."""
    root = project(tmp_path, **TWO_MODULES)
    _graph, report = analyze([root], embed_source=False)
    assert report.by_confidence.get("inferred", 0) == 0
    assert report.by_confidence.get("ambiguous", 0) == 0


def test_a_call_into_the_standard_library_is_external_not_unknown(tmp_path):
    """`os.getcwd()` is resolvable and outside the project. Counting it as
    unresolved would report a gap in the analysis where there is none."""
    root = project(tmp_path, **TWO_MODULES)
    _graph, report = analyze([root], embed_source=False)
    assert report.by_confidence.get("external", 0) >= 1


def test_a_relative_path_is_accepted(tmp_path, monkeypatch):
    """Every file is addressed by URI and a relative path has no URI, so
    `codecards src --lsp` used to die inside pathlib with nothing to connect
    the failure to the flag."""
    project(tmp_path, **TWO_MODULES)
    monkeypatch.chdir(tmp_path.parent)
    graph, _report = analyze([tmp_path.name], embed_source=False)
    assert graph.edges


def test_a_named_server_that_is_missing_is_an_error(tmp_path):
    """Not a reason to quietly run a different one. Substituting silently
    would make the graph depend on whatever happened to be installed."""
    root = project(tmp_path, **TWO_MODULES)
    with pytest.raises(ServerUnusable, match="not installed"):
        analyze([root], server=("definitely-not-a-language-server",),
                embed_source=False)


def test_the_containment_tree_matches_the_other_tiers(tmp_path):
    # Written directly: project() spells directories with "__", which cannot
    # express a file actually called __init__.py.
    for name, text in {
        "pkg/__init__.py": "",
        "pkg/inner/__init__.py": "",
        "pkg/inner/leaf.py": "def go():\n    pass\n",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    graph, _report = analyze([tmp_path], embed_source=False)
    validate(graph)
    assert graph.nodes["pkg.inner"].parent == "pkg"
    assert graph.nodes["pkg.inner.leaf"].parent == "pkg.inner"


def test_source_is_embedded_when_asked(tmp_path):
    root = project(tmp_path, **TWO_MODULES)
    graph, _ = analyze([root], embed_source=True)
    send = graph.nodes["mail.Mailer.send"]
    assert send.source is not None
    assert "def send" in send.source
    assert send.kind in CALLABLE_KINDS


def test_calling_a_parameter_is_not_the_function_calling_itself(tmp_path):
    """A parameter is declared on the same line as the function's own name.
    Keyed by line alone, every call to a callable parameter resolved to the
    enclosing function - four false self-edges on this project's source, each
    drawn `resolved` at full confidence, which is precisely what the
    confidence tiers exist to prevent."""
    root = project(tmp_path, **{
        "m.py": (
            "def render(node, text):\n"
            "    return text(node)\n"
            "\n"
            "\n"
            "def walk(n):\n"
            "    if n:\n"
            "        walk(n - 1)\n"
            "    return n\n"
        ),
    })
    graph, _report = analyze([root], embed_source=False)
    loops = {e.source for e in graph.edges if e.source == e.target}
    # `walk` really does call itself; `render` really does not.
    assert "m.walk" in loops
    assert "m.render" not in loops
