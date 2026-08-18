"""What someone is told when calls cannot be resolved.

Every route into this tool used to dead-end for anyone not holding Python:
"no Python files found" reads as an empty directory, and "no such index" says
nothing about how to make one. There are now two upgrades out of the guessing
tier - install a server, or build an index - and the messages have to name
both, cheaper one first.

`--no-lsp` throughout, so these exercise the fallback rather than depending on
which servers happen to be installed on the machine running the tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codecards.cli import main


def go_project(tmp_path):
    (tmp_path / "main.go").write_text(
        "package main\n"
        "\n"
        "func helper() {}\n"
        "\n"
        "func main() { helper() }\n"
    )
    return tmp_path


def test_a_guessed_graph_offers_the_server_before_the_index(tmp_path, capsys):
    """A server needs nothing built and never goes stale, so it is the
    cheaper of the two upgrades and belongs first."""
    pytest.importorskip("tree_sitter_go")
    assert main([str(go_project(tmp_path)), "--no-html", "--no-lsp", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "matched by name" in err
    assert err.index("gopls") < err.index("scip-go")


def test_a_go_project_is_told_how_to_index_itself(tmp_path, capsys):
    pytest.importorskip("tree_sitter_go")
    main([str(go_project(tmp_path)), "--no-html", "--no-lsp", "--quiet"])
    err = capsys.readouterr().err
    assert "1 Go file" in err
    assert "go install github.com/scip-code/scip-go" in err
    assert "--scip" in err


def test_the_indexer_is_named_by_a_path_that_does_not_need_PATH(tmp_path, capsys):
    """`go install` writes into GOPATH/bin, which is not on PATH unless
    somebody put it there. Naming the binary alone earns a "command not
    found" from the very reader who just ran the install line above it."""
    pytest.importorskip("tree_sitter_go")
    main([str(go_project(tmp_path)), "--no-html", "--no-lsp", "--quiet"])
    assert '"$(go env GOPATH)/bin/scip-go"' in capsys.readouterr().err


def test_the_server_is_named_by_what_installs_it(tmp_path, capsys):
    """Same rule as the indexer: a binary name alone is not actionable to
    someone who does not have it."""
    pytest.importorskip("tree_sitter_go")
    main([str(go_project(tmp_path)), "--no-html", "--no-lsp", "--quiet"])
    err = capsys.readouterr().err
    assert "gopls" in err
    assert "go install golang.org/x/tools/gopls@latest" in err


def test_a_missing_index_says_how_to_build_that_index(tmp_path, capsys):
    pytest.importorskip("tree_sitter_go")
    project = go_project(tmp_path)
    wanted = tmp_path / "mine.scip"
    assert main([str(project), "--scip", str(wanted), "--no-html"]) == 1
    err = capsys.readouterr().err
    assert "no such index" in err
    # The command writes to the path that was asked for, not a generic name.
    assert f"--output {wanted}" in err


def test_a_resolved_graph_is_not_nagged_about_indexes(tmp_path, capsys):
    """Nothing to upgrade to, so nothing to advertise."""
    (tmp_path / "m.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    assert main([str(tmp_path), "--no-html", "--quiet"]) == 0
    err = capsys.readouterr().err.lower()
    assert "scip" not in err
    assert "matched by name" not in err


# -- the command we print has to work when it is run -------------------------

def test_the_rebuild_command_names_a_resolved_path(tmp_path):
    """scip-python resolves --cwd itself and gets it wrong twice over: a
    relative path and a symlinked one both produce a valid, empty index and
    exit 0. On macOS /tmp is a symlink, so a plausible invocation lands in
    the silent case without anything looking wrong."""
    from codecards.scip import reindex_command

    link = tmp_path / "link"
    real = tmp_path / "real"
    real.mkdir()
    link.symlink_to(real)

    command = reindex_command(Path("out.scip"), link, "scip-python")
    assert command is not None
    assert str(real.resolve()) in command, command
    assert " --cwd ." not in command
    # and the output too, since it is interpreted relative to --cwd
    assert str((Path("out.scip")).resolve()) in command


def test_the_index_command_printed_for_a_project_is_absolute(tmp_path, capsys):
    """The path a reader typed is whatever they typed. Formatting it straight
    into the command hands them the failing form."""
    pytest.importorskip("tree_sitter_go")
    go_project(tmp_path)
    main([str(tmp_path), "--no-html", "--no-lsp", "--quiet"])
    err = capsys.readouterr().err
    line = next(x for x in err.splitlines() if "scip-go" in x and "index" in x)
    assert str(tmp_path.resolve()) in line, line
