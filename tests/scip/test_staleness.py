"""An index describes the code as it was when the indexer ran.

Every answer in it is only as true as that moment. Nothing in the file says
when it was built, so the one signal available is the clock: a source file
modified after the index was written was not the file the index describes.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("tree_sitter_python")

from test_index import SYMBOL, document, metadata, occurrence, tool_info

from codecards.scip import analyze, stale_sources


def project(tmp_path, *, index_older: bool, tool: str | None = "scip-python"):
    """A one-file project and an index, with the clock set either way."""
    (tmp_path / "run.py").write_text("def main():\n    pass\n")
    index = tmp_path / "index.scip"
    index.write_bytes(
        (tool_info(tool) if tool else b"")
        + metadata(f"file://{tmp_path}")
        + document("run.py", occurrence(0, 4, 8, SYMBOL, roles=1))
    )
    # Set times explicitly rather than sleeping: the test must not depend on
    # filesystem timestamp granularity, which is a whole second on some.
    old, new = 1_000_000, 2_000_000
    os.utime(index, (old, old) if index_older else (new, new))
    os.utime(tmp_path / "run.py", (new, new) if index_older else (old, old))
    return index


def test_a_source_newer_than_the_index_is_reported(tmp_path):
    index = project(tmp_path, index_older=True)
    assert stale_sources(tmp_path, index, ["run.py"]) == ["run.py"]


def test_an_index_newer_than_its_sources_is_current(tmp_path):
    index = project(tmp_path, index_older=False)
    assert stale_sources(tmp_path, index, ["run.py"]) == []


def test_a_stale_index_still_produces_a_graph(tmp_path):
    """Never blocks and never prompts. The reader asked for a picture; they
    get one, plus the fact that it may be out of date."""
    index = project(tmp_path, index_older=True)
    graph, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert graph.nodes
    assert report.stale == ["run.py"]


def test_a_current_index_reports_nothing_stale(tmp_path):
    index = project(tmp_path, index_older=False)
    _, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert report.stale == []


def test_the_reindex_command_comes_from_the_tool_the_index_names(tmp_path):
    index = project(tmp_path, index_older=True)
    _, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert report.reindex_command is not None
    assert report.reindex_command.startswith("npx @sourcegraph/scip-python")
    assert str(index) in report.reindex_command


def test_an_index_that_names_no_tool_still_warns(tmp_path):
    """The files and the fact are the point. The command is a convenience,
    and its absence must not swallow the warning."""
    index = project(tmp_path, index_older=True, tool=None)
    _, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert report.stale == ["run.py"]
    assert report.reindex_command is None


def test_the_command_is_one_that_actually_runs(tmp_path):
    """A tool NAME is not a command. scip-python is distributed on npm and is
    normally not on PATH at all, so printing the bare name produced
    "command not found" for the one reader who tried to follow the advice.
    """
    from codecards.scip import reindex_command

    command = reindex_command(tmp_path / "i.scip", tmp_path, "scip-python")
    assert command.startswith("npx @sourcegraph/scip-python index")
    # `index` takes no positional project root - it reads --cwd.
    assert f"--cwd {tmp_path}" in command
    assert f"--output {tmp_path / 'i.scip'}" in command


def test_an_indexer_we_have_not_verified_gets_no_invented_command(tmp_path):
    """Guessing a second time is how the first wrong command happened."""
    from codecards.scip import reindex_command

    assert reindex_command(tmp_path / "i.scip", tmp_path, "scip-elixir") is None
    assert reindex_command(tmp_path / "i.scip", tmp_path, None) is None


def test_only_the_files_the_index_describes_are_examined(tmp_path):
    """Walking the tree for *.py finds the virtualenv. On a 46-file project
    that reported 3,447 stale files, nearly all of them site-packages the
    graph never drew, which buries the handful that matter and the
    instruction under them."""
    index = project(tmp_path, index_older=True)
    vendored = tmp_path / ".venv" / "lib" / "site-packages" / "thing"
    vendored.mkdir(parents=True)
    (vendored / "mod.py").write_text("def f():\n    pass\n")

    # The index describes run.py and nothing else, so that is all it can be
    # out of date with respect to.
    assert stale_sources(tmp_path, index, ["run.py"]) == ["run.py"]

    _, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert report.stale == ["run.py"]
