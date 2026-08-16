"""What a Go project is told when there is no index yet.

Both routes into this tool dead-ended for anyone not holding Python: "no
Python files found" reads as an empty directory, and "no such index" says
nothing about how to make one. The commands below are the whole distance
between those messages and a graph.
"""

from __future__ import annotations

import pytest

from codecards.cli import main


def go_project(tmp_path):
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")
    return tmp_path


def test_a_go_project_is_told_how_to_index_itself(tmp_path, capsys):
    assert main([str(go_project(tmp_path)), "--no-html"]) == 1
    err = capsys.readouterr().err
    assert "1 Go file" in err
    assert "go install github.com/scip-code/scip-go" in err
    assert "--scip" in err


def test_the_indexer_is_named_by_a_path_that_does_not_need_PATH(tmp_path, capsys):
    """`go install` writes into GOPATH/bin, which is not on PATH unless
    somebody put it there. Naming the binary alone earns a "command not
    found" from the very reader who just ran the install line above it."""
    main([str(go_project(tmp_path)), "--no-html"])
    err = capsys.readouterr().err
    assert '"$(go env GOPATH)/bin/scip-go"' in err


def test_a_missing_index_says_how_to_build_that_index(tmp_path, capsys):
    pytest.importorskip("tree_sitter_go")
    project = go_project(tmp_path)
    wanted = tmp_path / "mine.scip"
    assert main([str(project), "--scip", str(wanted), "--no-html"]) == 1
    err = capsys.readouterr().err
    assert "no such index" in err
    # The command writes to the path that was asked for, not a generic name.
    assert f"--output {wanted}" in err


def test_a_python_project_is_not_nagged_about_indexes(tmp_path, capsys):
    """The default path needs no index and must not advertise one."""
    (tmp_path / "m.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    assert main([str(tmp_path), "--no-html", "--quiet"]) == 0
    assert "scip" not in capsys.readouterr().err.lower()
