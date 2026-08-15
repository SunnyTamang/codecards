"""An index describes the code as it was when the indexer ran.

Every answer in it is only as true as that moment. Nothing in the file says
when it was built, so the one signal available is the clock: a source file
modified after the index was written was not the file the index describes.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("tree_sitter_python")

from test_index import SYMBOL, document, metadata, occurrence

from codecards.scip import analyze, stale_sources


def project(tmp_path, *, index_older: bool):
    """A one-file project and an index, with the clock set either way."""
    (tmp_path / "run.py").write_text("def main():\n    pass\n")
    index = tmp_path / "index.scip"
    index.write_bytes(
        metadata(f"file://{tmp_path}")
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
    assert stale_sources([tmp_path], index) == ["run.py"]


def test_an_index_newer_than_its_sources_is_current(tmp_path):
    index = project(tmp_path, index_older=False)
    assert stale_sources([tmp_path], index) == []


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


def test_the_reindex_command_names_the_tool_that_built_it(tmp_path):
    index = project(tmp_path, index_older=True)
    _, report = analyze(roots=[tmp_path], index_path=index, embed_source=False)
    assert report.reindex_command is not None
    assert str(index) in report.reindex_command
