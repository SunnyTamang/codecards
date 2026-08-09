from __future__ import annotations

import sys

import pytest

from codecards.extract.highlight import (
    CALL, COMMENT, DEFINITION, KEYWORD, MAX_SOURCE_LINES, NUMBER, OPERATOR, STRING,
    highlight, prepare,
)


def spans(source: str) -> list[list[tuple[str, str]]]:
    """Return per-line [(class, covered text)], which reads far better in a diff."""
    lines = source.splitlines()
    return [
        [(cls, line[col:col + length]) for col, length, cls in runs]
        for line, runs in zip(lines, highlight(source))
    ]


def test_one_entry_per_line():
    assert len(highlight("a = 1\nb = 2\nc = 3\n")) == 3


def test_empty_source_yields_nothing():
    assert highlight("") == ()


def test_keywords_definitions_and_calls_are_distinguished():
    assert spans("def send(x):\n    return other(x)\n") == [
        [(KEYWORD, "def"), (DEFINITION, "send"), (OPERATOR, "("), (OPERATOR, ")"),
         (OPERATOR, ":")],
        [(KEYWORD, "return"), (CALL, "other"), (OPERATOR, "("), (OPERATOR, ")")],
    ]


def test_an_indented_method_slice_lexes_and_columns_stay_aligned():
    """The case that matters: extracted methods are always indented."""
    source = "    def send(self):\n        self.retry()\n"
    assert spans(source) == [
        [(KEYWORD, "def"), (DEFINITION, "send"), (OPERATOR, "("), (OPERATOR, ")"),
         (OPERATOR, ":")],
        [(OPERATOR, "."), (CALL, "retry"), (OPERATOR, "("), (OPERATOR, ")")],
    ]


def test_a_tab_indented_slice_keeps_its_columns():
    assert spans("\tdef f(self):\n\t\treturn 1\n") == [
        [(KEYWORD, "def"), (DEFINITION, "f"), (OPERATOR, "("), (OPERATOR, ")"),
         (OPERATOR, ":")],
        [(KEYWORD, "return"), (NUMBER, "1")],
    ]


def test_comments_and_numbers():
    assert spans("x = 3.14e-2  # tau-ish\n") == [
        [(OPERATOR, "="), (NUMBER, "3.14e-2"), (COMMENT, "# tau-ish")],
    ]


def test_a_triple_quoted_string_produces_a_run_on_every_line_it_covers():
    source = 'def g():\n    s = """one\ntwo\nthree"""\n'
    assert spans(source)[1] == [(OPERATOR, "="), (STRING, '"""one')]
    assert spans(source)[2] == [(STRING, "two")]
    assert spans(source)[3] == [(STRING, 'three"""')]


def test_escaped_quotes_do_not_end_the_string():
    assert spans("b = 'it\\'s'\n") == [[(OPERATOR, "="), (STRING, "'it\\'s'")]]


def test_decorators_and_async():
    assert spans('@app.route("/x")\nasync def h(r):\n    await go()\n') == [
        [(OPERATOR, "@"), (OPERATOR, "."), (CALL, "route"), (OPERATOR, "("),
         (STRING, '"/x"'), (OPERATOR, ")")],
        [(KEYWORD, "async"), (KEYWORD, "def"), (DEFINITION, "h"), (OPERATOR, "("),
         (OPERATOR, ")"), (OPERATOR, ":")],
        [(KEYWORD, "await"), (CALL, "go"), (OPERATOR, "("), (OPERATOR, ")")],
    ]


def test_a_plain_identifier_gets_no_run():
    """Unstyled text is the gap between runs, not a run of its own."""
    assert spans("value = other\n") == [[(OPERATOR, "=")]]


def test_soft_keywords_are_left_unstyled():
    """`match` is a name that may be a variable. Guessing would be wrong sometimes."""
    assert spans("match value:\n") == [[(OPERATOR, ":")]]


@pytest.mark.skipif(sys.version_info < (3, 12),
                    reason="f-strings became multiple tokens in 3.12")
def test_f_string_interpolations_are_highlighted_on_312_and_later():
    classes = {cls for cls, _ in spans('m = f"hi {name}"\n')[0]}
    assert STRING in classes and OPERATOR in classes


@pytest.mark.skipif(sys.version_info >= (3, 12),
                    reason="f-strings are a single token before 3.12")
def test_f_string_is_one_string_before_312():
    assert spans('m = f"hi {name}"\n') == [[(OPERATOR, "="), (STRING, 'f"hi {name}"')]]


def test_unlexable_source_yields_empty_runs_rather_than_raising():
    """A card must still show its code, just unstyled."""
    assert highlight("def broken(:\n    ???\n") == ((), ())


def test_every_run_lies_inside_its_line():
    source = (
        "    @property\n"
        "    def name(self) -> str:\n"
        '        """Doc."""\n'
        "        return f'{self._n}'  # trailing\n"
    )
    for line, runs in zip(source.splitlines(), highlight(source)):
        for col, length, _cls in runs:
            assert 0 <= col and col + length <= len(line)


def test_prepare_slices_the_requested_lines():
    text = "a = 1\nb = 2\nc = 3\nd = 4\n"
    source, tokens, truncated = prepare(text, 2, 3)
    assert source == "b = 2\nc = 3"
    assert len(tokens) == 2
    assert truncated is False


def test_prepare_truncates_a_monster_function():
    text = "\n".join(f"x{i} = {i}" for i in range(MAX_SOURCE_LINES + 50)) + "\n"
    source, tokens, truncated = prepare(text, 1, MAX_SOURCE_LINES + 50)
    assert truncated is True
    assert len(source.splitlines()) == MAX_SOURCE_LINES
    assert len(tokens) == MAX_SOURCE_LINES


def test_prepare_tolerates_a_line_range_past_the_end_of_the_file():
    source, tokens, truncated = prepare("a = 1\n", 1, 99)
    assert source == "a = 1"
    assert len(tokens) == 1
    assert truncated is False
