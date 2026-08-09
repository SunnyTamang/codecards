from __future__ import annotations

from pathlib import Path

from codecards.extract import EXTERNAL_ROOT_ID, analyze
from codecards.graph.model import Confidence, EntryReason, NodeKind, validate


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_graph_is_valid_and_contains_the_full_containment_chain(tmp_path):
    root = project(tmp_path, {
        "app/__init__.py": "",
        "app/mail.py": "class H:\n    def send(self):\n        pass\n",
    })
    graph, _ = analyze([root])
    validate(graph)
    assert graph.nodes["app"].kind is NodeKind.PACKAGE
    assert graph.nodes["app.mail"].kind is NodeKind.MODULE
    assert graph.nodes["app.mail"].parent == "app"
    assert graph.nodes["app.mail.H"].kind is NodeKind.CLASS
    assert graph.nodes["app.mail.H.send"].parent == "app.mail.H"


def test_namespace_directories_become_packages(tmp_path):
    root = project(tmp_path, {"app/sub/deep.py": "def go():\n    pass\n"})
    graph, _ = analyze([root])
    validate(graph)
    assert graph.nodes["app"].kind is NodeKind.PACKAGE
    assert graph.nodes["app.sub"].kind is NodeKind.PACKAGE


def test_external_edges_are_dropped_by_default(tmp_path):
    root = project(tmp_path, {"m.py": "import os\n\ndef go():\n    os.getcwd()\n"})
    graph, report = analyze([root])
    validate(graph)
    assert graph.edges == []
    assert report.by_confidence["external"] == 1


def test_include_external_adds_leaf_nodes_under_a_synthetic_root(tmp_path):
    root = project(tmp_path, {"m.py": "import os\n\ndef go():\n    os.getcwd()\n"})
    graph, _ = analyze([root], include_external=True)
    validate(graph)
    assert graph.nodes["os.getcwd"].parent == EXTERNAL_ROOT_ID
    assert graph.nodes["os.getcwd"].kind is NodeKind.FUNCTION
    assert any(e.target == "os.getcwd" for e in graph.edges)


def test_main_block_produces_an_entry_hint(tmp_path):
    root = project(tmp_path, {"m.py": (
        "def go():\n    pass\n\nif __name__ == '__main__':\n    go()\n"
    )})
    graph, _ = analyze([root])
    assert any(h.node_id == "m.go" and h.reason is EntryReason.MAIN_BLOCK
               for h in graph.entry_hints)


def test_console_scripts_produce_entry_hints(tmp_path):
    root = project(tmp_path, {
        "pyproject.toml": '[project.scripts]\nmytool = "app.cli:main"\n',
        "app/__init__.py": "",
        "app/cli.py": "def main():\n    pass\n",
    })
    graph, _ = analyze([root])
    assert any(h.node_id == "app.cli.main" and h.reason is EntryReason.CONSOLE_SCRIPT
               for h in graph.entry_hints)


def test_external_decorator_produces_an_entry_hint(tmp_path):
    root = project(tmp_path, {"m.py": "import click\n\n@click.command()\ndef go():\n    pass\n"})
    graph, _ = analyze([root])
    assert any(h.node_id == "m.go" and h.reason is EntryReason.DECORATED
               for h in graph.entry_hints)


def test_internal_decorator_does_not_produce_a_hint(tmp_path):
    root = project(tmp_path, {"m.py": (
        "def deco(f):\n    return f\n\n@deco\ndef go():\n    pass\n"
    )})
    graph, _ = analyze([root])
    assert not any(h.node_id == "m.go" and h.reason is EntryReason.DECORATED
                   for h in graph.entry_hints)


def test_test_functions_produce_entry_hints(tmp_path):
    root = project(tmp_path, {"tests/test_thing.py": "def test_it():\n    pass\n"})
    graph, _ = analyze([root])
    assert any(h.reason is EntryReason.TEST for h in graph.entry_hints)


def test_embed_source_attaches_the_function_body(tmp_path):
    root = project(tmp_path, {"m.py": "def go():\n    return 42\n"})
    graph, _ = analyze([root], embed_source=True)
    assert graph.nodes["m.go"].source == "def go():\n    return 42"
    plain, _ = analyze([root])
    assert plain.nodes["m.go"].source is None


def test_report_counts_and_resolution_rate(tmp_path):
    root = project(tmp_path, {"m.py": "def a():\n    pass\n\ndef b():\n    a()\n"})
    _, report = analyze([root])
    assert report.total_calls == 1
    assert report.by_confidence["resolved"] == 1
    assert report.resolution_rate == 1.0
    assert report.callable_count == 2


def test_report_format_is_human_readable(tmp_path):
    root = project(tmp_path, {"m.py": "def a():\n    pass\n\ndef b():\n    a()\n"})
    _, report = analyze([root])
    text = report.format()
    assert "1 calls: 1 resolved" in text or "1 call: 1 resolved" in text


def test_skipped_files_are_reported_not_raised(tmp_path):
    root = project(tmp_path, {"good.py": "def a():\n    pass\n", "bad.py": "def (\n"})
    graph, report = analyze([root])
    validate(graph)
    assert "good.a" in graph.nodes
    assert len(report.skipped) == 1
    assert "syntax error" in report.format()


def test_graph_and_render_never_import_ast():
    """The layering rule, enforced."""
    import codecards
    package_root = Path(codecards.__file__).parent
    for sub in ("graph", "render"):
        for path in (package_root / sub).rglob("*.py"):
            text = path.read_text()
            assert "import ast" not in text, f"{path} imports ast"


def test_a_file_that_blows_the_recursion_limit_is_skipped_not_fatal(tmp_path):
    """One pathological file must cost itself, not the run."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "good.py").write_text("def a():\n    return 1\n")
    (root / "deep.py").write_text("x = " + " + ".join(str(i) for i in range(6000)) + "\n")
    (root / "also_good.py").write_text("def c():\n    return 3\n")

    graph, report = analyze([root])

    assert "good.a" in graph.nodes
    assert "also_good.c" in graph.nodes
    skipped = {s.path: s.reason for s in report.skipped}
    assert any("deep.py" in path for path in skipped)
    assert any("too deeply nested" in reason for reason in skipped.values())


def test_the_recursion_guard_does_not_leak_into_healthy_files(tmp_path):
    """The limit must be restored, or a later file inherits a raised one."""
    import sys

    root = tmp_path / "proj"
    root.mkdir()
    (root / "deep.py").write_text("x = " + " + ".join(str(i) for i in range(6000)) + "\n")
    (root / "later.py").write_text("def c():\n    return 3\n")

    before = sys.getrecursionlimit()
    graph, _report = analyze([root])
    assert sys.getrecursionlimit() == before
    assert "later.c" in graph.nodes
