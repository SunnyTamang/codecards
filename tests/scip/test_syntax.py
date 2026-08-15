"""What tree-sitter is asked for: structure, call sites, and token runs.

Nothing here resolves anything, so nothing here needs an index. These are the
questions a parse tree can answer on its own.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_python")

from codecards.scip import syntax

SOURCE = b'''
import os


class Mailer:
    """Sends things."""

    def send(self, to):
        if to:
            deliver(to)
        for _ in range(3):
            retry()
        return os.getcwd()


def deliver(to):
    pass
'''


def parsed():
    return syntax.parse(SOURCE), SOURCE


def test_definitions_carry_their_nesting_and_kind():
    tree, source = parsed()
    found = {d.name: d for d in syntax.definitions(tree, source)}
    assert found["Mailer"].kind == "class"
    assert found["send"].kind == "method"
    assert found["send"].parent is found["Mailer"]
    assert found["deliver"].kind == "function"
    assert found["deliver"].parent is None


def test_a_definition_carries_its_signature_and_first_docstring_line():
    tree, source = parsed()
    found = {d.name: d for d in syntax.definitions(tree, source)}
    assert found["send"].signature == "(self, to)"
    assert found["Mailer"].docstring == "Sends things."
    assert found["send"].docstring is None


def test_call_sites_name_the_identifier_that_is_called():
    tree, source = parsed()
    calls = {c.text for c in syntax.call_sites(tree, source)}
    # `os.getcwd()` is named by its attribute, which is where an index puts
    # the occurrence that resolves it.
    assert {"deliver", "retry", "range", "getcwd"} <= calls


def test_a_call_knows_whether_it_only_sometimes_happens():
    tree, source = parsed()
    by_name = {c.text: c for c in syntax.call_sites(tree, source)}
    assert by_name["deliver"].in_conditional is True
    assert by_name["retry"].in_loop is True
    assert by_name["getcwd"].in_conditional is False
    assert by_name["getcwd"].in_loop is False


def test_a_call_on_something_that_is_not_a_name_is_skipped():
    """`handlers[0](x)` has no identifier to look up, so there is nothing an
    index could resolve and nothing to draw."""
    source = b"def go(handlers):\n    handlers[0](1)\n"
    calls = syntax.call_sites(syntax.parse(source), source)
    assert [c.text for c in calls] == []


def test_highlighting_emits_only_the_runs_that_are_not_plain():
    tree, source = parsed()
    runs = syntax.highlight(tree, source, 0, 20)
    classes = {cls for line in runs.values() for _, _, cls in line}
    assert {"kw", "str", "def", "call"} <= classes
    for line in runs.values():
        for start, length, _cls in line:
            assert start >= 0 and length > 0
