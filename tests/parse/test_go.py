"""Go, read through the same walkers Python uses.

The point of these is not that Go works - it is that nothing in `syntax` had
to learn what Go is. Every difference below is a table entry in `grammars`:
different node names, a doc comment above the declaration instead of a string
inside it, and an entry point that is a definition rather than a call.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_go")

from codecards.parse import syntax
from codecards.parse.grammars import GO, for_path

SOURCE = b'''package main

import "fmt"

// model holds the state of the program.
type model struct {
	count int
}

// Init prepares the model.
func (m model) Init() error {
	if m.count > 0 {
		reset(m.count)
	}
	for i := 0; i < 3; i++ {
		retry()
	}
	return fmt.Errorf("x")
}

func reset(n int) {}

func retry() {}

func main() {
	m := model{}
	m.Init()
}
'''


def parsed():
    return syntax.parse(GO, SOURCE), SOURCE


def test_a_go_file_finds_its_grammar_by_suffix():
    assert for_path("cmd/tui/main.go") is GO
    assert for_path("cmd/tui/main.py") is not GO


def test_a_struct_is_a_type_and_a_receiver_makes_a_method():
    tree, source = parsed()
    found = {d.name: d for d in syntax.definitions(GO, tree, source)}
    assert found["model"].kind == "class"
    assert found["Init"].kind == "method"
    assert found["reset"].kind == "function"
    assert found["main"].kind == "function"


def test_a_method_is_not_nested_inside_its_type():
    """Go declares a method at the top level and attaches it through a
    receiver. Nesting is what Python uses to say the same thing, so position
    cannot be what decides the kind - and the containment has to come from
    the index rather than from the parse tree."""
    tree, source = parsed()
    found = {d.name: d for d in syntax.definitions(GO, tree, source)}
    assert found["Init"].parent is None
    assert found["model"].children == []


def test_a_doc_comment_above_the_declaration_is_the_summary():
    """Go has no docstring. The convention is the comment block touching the
    declaration, which is a sibling rather than a child."""
    tree, source = parsed()
    found = {d.name: d for d in syntax.definitions(GO, tree, source)}
    assert found["model"].docstring == "model holds the state of the program."
    assert found["Init"].docstring == "Init prepares the model."
    assert found["reset"].docstring is None


def test_call_sites_name_the_identifier_called():
    tree, source = parsed()
    calls = {c.text for c in syntax.call_sites(GO, tree, source)}
    # `fmt.Errorf(...)` is named by its field, which is where the index puts
    # the occurrence that resolves it.
    assert {"reset", "retry", "Errorf", "Init"} <= calls


def test_a_call_knows_whether_it_only_sometimes_happens():
    tree, source = parsed()
    by_name = {c.text: c for c in syntax.call_sites(GO, tree, source)}
    assert by_name["reset"].in_conditional is True
    # Go spells every loop `for`.
    assert by_name["retry"].in_loop is True


def test_main_is_a_door_even_though_nothing_calls_it():
    """The runtime invokes it, so no call site anywhere names it. Looking for
    calls - which is all Python's main block needs - finds nothing here."""
    tree, source = parsed()
    doors = syntax.entry_definitions(GO, tree, source)
    assert [d.name for d in doors] == ["main"]
    assert syntax.entry_calls(GO, tree, source) == []


def test_main_in_a_library_package_is_not_a_door():
    source = b"package helpers\n\nfunc main() {}\n"
    tree = syntax.parse(GO, source)
    assert syntax.entry_definitions(GO, tree, source) == []


def test_highlighting_paints_go_keywords_and_strings():
    tree, source = parsed()
    runs = syntax.highlight(GO, tree, source, 0, 40)
    classes = {cls for line in runs.values() for _, _, cls in line}
    assert {"kw", "str", "def", "call", "com"} <= classes
