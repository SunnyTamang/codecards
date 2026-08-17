"""A graph with no index behind it.

Name matching is the only thing available, and it is a guess. These pin the
guess being labelled as one - the whole reason this tier is allowed to exist.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_go")

from codecards.graph.model import (
    CALLABLE_KINDS,
    CONTAINER_KINDS,
    Confidence,
    NodeKind,
    validate,
)
from codecards.parse import structural

GO = """package app

// model is the state.
type model struct{ n int }

func (m model) Init() { helper() }

func (m model) View() string { helper(); return "" }

func helper() {}

func main() { helper() }
"""


def project(tmp_path, **files):
    for name, text in files.items():
        path = tmp_path / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return structural.analyze(roots=[tmp_path], embed_source=False)


def test_a_go_project_with_no_index_still_draws(tmp_path):
    graph, report = project(tmp_path, **{"app__main.go": GO})
    validate(graph)
    assert graph.edges
    assert report.callable_count == 4
    assert {n.name for n in graph.nodes.values() if n.kind in CALLABLE_KINDS} == {
        "Init", "View", "helper", "main"}


def test_nothing_is_ever_called_resolved(tmp_path):
    """Without an index there is no authority that a name means a definition.
    Marking any of this resolved would be a lie the tiers exist to prevent."""
    graph, _ = project(tmp_path, **{"app__main.go": GO})
    assert graph.edges
    assert all(e.confidence is not Confidence.RESOLVED for e in graph.edges)


def test_one_definition_of_a_name_is_inferred(tmp_path):
    graph, _ = project(tmp_path, **{"app__main.go": GO})
    edge = next(e for e in graph.edges if e.target.endswith(".helper"))
    assert edge.confidence is Confidence.INFERRED


def test_two_definitions_of_a_name_are_ambiguous_and_both_drawn(tmp_path):
    """The graph says "one of these", naming them, rather than picking."""
    graph, _ = project(tmp_path, **{
        "a__one.go": "package a\n\nfunc render() {}\n\nfunc go1() { render() }\n",
        "b__two.go": "package b\n\nfunc render() {}\n",
    })
    from_go1 = [e for e in graph.edges if e.source.endswith(".go1")]
    assert len(from_go1) == 2
    assert all(e.confidence is Confidence.AMBIGUOUS for e in from_go1)


def test_a_method_sits_inside_the_type_its_receiver_names(tmp_path):
    """Go declares a method beside its type, so nesting cannot say this and
    with no index there is no symbol to say it either. The receiver does."""
    graph, _ = project(tmp_path, **{"app__main.go": GO})
    init = next(n for n in graph.nodes.values() if n.name == "Init")
    assert graph.nodes[init.parent].name == "model"


def test_a_doc_comment_still_reaches_the_card(tmp_path):
    graph, _ = project(tmp_path, **{"app__main.go": GO})
    model = next(n for n in graph.nodes.values() if n.name == "model")
    assert model.summary == "model is the state."


def test_main_is_still_a_door(tmp_path):
    graph, _ = project(tmp_path, **{"main.go": "package main\n\nfunc main() {}\n"})
    assert [h.node_id for h in graph.entry_hints] == ["main"]


def test_a_nested_package_contains_the_modules_below_it(tmp_path):
    """Containment has to be a tree, not a list.

    Every module used to arrive with no parent, which drew a package beside
    the modules it holds instead of around them. Nothing downstream survives
    that: the opening view is chosen by descending to the first level holding
    more than one container, and collapse has nothing to fold.
    """
    pytest.importorskip("tree_sitter_python")
    # Written directly: the project() helper spells directories with "__",
    # which cannot express a file actually called __init__.py.
    for name, text in {
        "pkg/__init__.py": "",
        "pkg/config.py": "def load():\n    pass\n",
        "pkg/checks/__init__.py": "",
        "pkg/checks/length.py": "def check():\n    pass\n",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    graph, _ = structural.analyze(roots=[tmp_path], embed_source=False)
    validate(graph)
    parents = {n.id: n.parent for n in graph.nodes.values()
               if n.kind in CONTAINER_KINDS}
    assert parents == {
        "pkg": None,
        "pkg.config": "pkg",
        "pkg.checks": "pkg",
        "pkg.checks.length": "pkg.checks",
    }


def test_a_package_no_file_declares_is_still_drawn(tmp_path):
    """A Go directory is a namespace whether or not anything sits directly in
    it, so the level above a package has to be invented rather than skipped -
    otherwise its children point at a parent that does not exist."""
    graph, _ = project(tmp_path, **{
        "cmd__tui__main.go": "package main\n\nfunc main() {}\n",
    })
    validate(graph)
    assert graph.nodes["cmd"].kind is NodeKind.PACKAGE
    assert graph.nodes["cmd"].parent is None
    assert graph.nodes["cmd/tui"].parent == "cmd"


def test_vendored_code_is_not_the_project(tmp_path):
    """Walking into a vendor directory answers a question about the project
    with a description of its dependencies."""
    graph, _ = project(tmp_path, **{
        "app__main.go": "package app\n\nfunc mine() {}\n",
        "vendor__other__lib.go": "package other\n\nfunc theirs() {}\n",
    })
    assert {n.name for n in graph.nodes.values() if n.kind in CALLABLE_KINDS} == {"mine"}
