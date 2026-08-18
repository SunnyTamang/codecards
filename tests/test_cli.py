from __future__ import annotations

import pytest

from codecards.cli import build_parser, main


def project(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


WORKING = {"m.py": "def a():\n    pass\n\ndef b():\n    a()\n"}


def test_successful_run_exits_zero_and_prints_the_summary(tmp_path, capsys):
    root = project(tmp_path, WORKING)
    code = main([str(root), "--no-html"])
    assert code == 0
    assert "1 calls: 1 resolved" in capsys.readouterr().out


def test_quiet_suppresses_the_summary(tmp_path, capsys):
    root = project(tmp_path, WORKING)
    main([str(root), "--no-html", "--quiet"])
    assert capsys.readouterr().out == ""


def test_nothing_readable_exits_one_and_names_what_it_reads(tmp_path, capsys):
    """"Found nothing" reads as an empty directory. Naming the languages says
    the real thing: there is no parser for whatever is in there."""
    root = project(tmp_path, {"notes.txt": "hi"})
    code = main([str(root), "--no-html"])
    assert code == 1
    err = capsys.readouterr().err
    assert "found no source it can read" in err
    assert "python" in err


def test_a_graph_with_no_edges_exits_one(tmp_path, capsys):
    root = project(tmp_path, {"m.py": "x = 1\n"})
    code = main([str(root), "--no-html"])
    assert code == 1
    assert "no calls" in capsys.readouterr().err


def test_missing_path_says_the_path_is_missing(tmp_path, capsys):
    """A typo'd path is the commonest bad invocation, and "no Python files
    found in X" reads as though X exists and is empty."""
    code = main([str(tmp_path / "nope"), "--no-html"])
    assert code == 1
    err = capsys.readouterr().err
    assert "does not exist" in err
    assert "no Python files" not in err


def test_exclude_flag_is_repeatable(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["src", "--exclude", "a/*", "--exclude", "b/*"])
    assert args.exclude == ["a/*", "b/*"]


def test_defaults_match_the_spec():
    args = build_parser().parse_args(["src"])
    assert args.output.name == "codecards.html"
    assert args.max_depth == 15
    assert args.include_external is False
    assert args.no_source is False
    assert args.quiet is False


def test_version_flag_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0


def test_large_graph_emits_a_warning(tmp_path, capsys, monkeypatch):
    from codecards import cli
    monkeypatch.setattr(cli, "LARGE_GRAPH_THRESHOLD", 1)
    root = project(tmp_path, WORKING)
    main([str(root), "--no-html"])
    assert "--exclude" in capsys.readouterr().err
