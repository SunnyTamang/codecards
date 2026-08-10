"""Turn a source slice into per-line syntax token spans.

Highlighting runs at build time so the browser ships no parser: the page
receives the raw line plus a list of styled runs and paints spans between them.

Only non-plain runs are emitted. Text not covered by a run renders unstyled,
which roughly halves the payload on ordinary code.
"""

from __future__ import annotations

import io
import keyword
import textwrap
import tokenize

#: Fixed class names. These exact strings appear in JSON and in CSS.
KEYWORD = "kw"
STRING = "str"
NUMBER = "num"
COMMENT = "com"
DEFINITION = "def"
CALL = "call"

# Operators are deliberately NOT a class. Punctuation was 63% of all runs on
# real code while covering almost no characters, costing 58% of the token
# payload for the least visual value in the set. It renders in the default
# text colour, which is what most editor themes do anyway.

#: Above this, a callable embeds truncated. One generated 12,000-line function
#: should not dominate the output file.
MAX_SOURCE_LINES = 400

#: (column, length, class) for one styled run on one line.
Run = tuple[int, int, str]

_SKIP = frozenset({
    tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
    tokenize.ENDMARKER, tokenize.ENCODING,
})

_SIMPLE = {
    tokenize.COMMENT: COMMENT,
    tokenize.STRING: STRING,
    tokenize.NUMBER: NUMBER,
}

# f-string tokens exist only on 3.12+. Before that an f-string arrives as a
# single STRING token, so their absence is not a problem.
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    _type = getattr(tokenize, _name, None)
    if _type is not None:
        _SIMPLE[_type] = STRING


def prepare(
    file_text: str, line_start: int, line_end: int
) -> tuple[str, tuple[tuple[Run, ...], ...], bool]:
    """Slice a callable out of its file and highlight it.

    Returns `(source, tokens, truncated)`. Line numbers are 1-based and
    inclusive, matching `ast` and `Location`.
    """
    lines = file_text.splitlines()
    slice_lines = lines[line_start - 1:line_end]
    truncated = len(slice_lines) > MAX_SOURCE_LINES
    if truncated:
        slice_lines = slice_lines[:MAX_SOURCE_LINES]
    source = "\n".join(slice_lines)
    return source, highlight(source), truncated


def highlight(source: str) -> tuple[tuple[Run, ...], ...]:
    """Return one tuple of runs per line of `source`.

    Never raises. Unlexable input yields empty runs for every line, so a card
    still shows its code, just unstyled.
    """
    lines = source.splitlines()
    if not lines:
        return ()

    # A callable's slice is indented relative to its file, which tokenize
    # rejects outright. Dedent to lex, then shift every column back by what
    # was removed so the runs line up with the original text.
    dedented = textwrap.dedent(source)
    dedented_lines = dedented.splitlines()
    offsets = [len(o) - len(d) for o, d in zip(lines, dedented_lines, strict=False)]

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(dedented).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return tuple(() for _ in lines)

    runs: list[list[Run]] = [[] for _ in lines]
    previous_name = ""
    for index, token in enumerate(tokens):
        if token.type in _SKIP:
            continue
        cls = _classify(token, tokens, index, previous_name)
        if token.type == tokenize.NAME:
            previous_name = token.string
        elif token.type != tokenize.COMMENT:
            previous_name = ""
        if cls is not None:
            _emit(runs, token, cls, dedented_lines, offsets)
    return tuple(tuple(r) for r in runs)


def _classify(token, tokens, index: int, previous_name: str) -> str | None:
    if token.type in _SIMPLE:
        return _SIMPLE[token.type]
    if token.type != tokenize.NAME:
        return None
    if keyword.iskeyword(token.string):
        return KEYWORD
    if previous_name in ("def", "class"):
        return DEFINITION
    following = _next_significant(tokens, index)
    if following is not None and following.type == tokenize.OP and following.string == "(":
        return CALL
    # Soft keywords (`match`, `case`, `type`) are deliberately left unstyled:
    # they are ordinary names in most code and guessing is wrong sometimes.
    return None


def _next_significant(tokens, index: int):
    for token in tokens[index + 1:]:
        if token.type not in _SKIP and token.type != tokenize.COMMENT:
            return token
    return None


def _emit(runs, token, cls: str, dedented_lines, offsets) -> None:
    """Record `token` as one run per line it covers."""
    start_row, start_col = token.start
    end_row, end_col = token.end
    for row in range(start_row, end_row + 1):
        line_index = row - 1
        if not 0 <= line_index < len(runs):
            continue
        text = dedented_lines[line_index]
        col = start_col if row == start_row else 0
        end = end_col if row == end_row else len(text)
        if end > col:
            runs[line_index].append((col + offsets[line_index], end - col, cls))
