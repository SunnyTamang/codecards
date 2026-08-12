from __future__ import annotations

from pathlib import Path

from codecards.extract import EXTERNAL_ROOT_ID, analyze
from codecards.graph.model import EntryReason, NodeKind, validate


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


def test_language_decorators_do_not_produce_hints(tmp_path):
    """@property and friends resolve outside the codebase like a framework
    decorator does, but they say how a callable behaves, not who calls it.
    Before this rule, @property alone was 84 of email's 101 entry points."""
    root = project(tmp_path, {"m.py": (
        "import functools\n"
        "import abc\n"
        "\n"
        "class C:\n"
        "    @property\n"
        "    def value(self):\n        return 1\n"
        "    @value.setter\n"
        "    def value(self, v):\n        pass\n"
        "    @staticmethod\n"
        "    def helper():\n        pass\n"
        "    @classmethod\n"
        "    def make(cls):\n        pass\n"
        "    @abc.abstractmethod\n"
        "    def must(self):\n        pass\n"
        "    @functools.cached_property\n"
        "    def heavy(self):\n        return 2\n"
    )})
    graph, _ = analyze([root])
    decorated = [h.node_id for h in graph.entry_hints
                 if h.reason is EntryReason.DECORATED]
    assert decorated == []


def test_a_framework_decorator_still_produces_a_hint_alongside_a_language_one(tmp_path):
    """The denylist must skip the boring decorator and keep looking, not
    give up on the callable."""
    root = project(tmp_path, {"m.py": (
        "import click\n"
        "\n"
        "class C:\n"
        "    @staticmethod\n"
        "    @click.command()\n"
        "    def go():\n        pass\n"
    )})
    graph, _ = analyze([root])
    assert any(h.node_id == "m.C.go" and h.reason is EntryReason.DECORATED
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
    """Embedding is the default: the cards are the code, so opting out is the
    unusual case. The CLI exposes this as --no-source."""
    root = project(tmp_path, {"m.py": "def go():\n    return 42\n"})
    graph, _ = analyze([root], embed_source=True)
    assert graph.nodes["m.go"].source == "def go():\n    return 42"

    default, _ = analyze([root])
    assert default.nodes["m.go"].source == "def go():\n    return 42"

    omitted, _ = analyze([root], embed_source=False)
    assert omitted.nodes["m.go"].source is None


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


def test_graph_and_render_never_import_python_syntax_modules():
    """The layering rule, enforced.

    `tokenize` matters as much as `ast`: syntax highlighting belongs in
    extract/ with every other module that knows Python. If it
    drifts into graph/ or render/, a second language stops being additive.
    """
    import codecards
    package_root = Path(codecards.__file__).parent
    for sub in ("graph", "render"):
        for path in (package_root / sub).rglob("*.py"):
            text = path.read_text()
            assert "import ast" not in text, f"{path} imports ast"
            assert "import tokenize" not in text, f"{path} imports tokenize"


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


def test_analyze_attaches_source_and_tokens_by_default(tmp_path):
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "mail.py").write_text(
        "class Mailer:\n"
        "    def send(self, msg):\n"
        "        return post(msg)\n"
    )
    graph, _ = analyze([root])
    node = graph.nodes["app.mail.Mailer.send"]
    assert node.source == "    def send(self, msg):\n        return post(msg)"
    assert node.source_tokens is not None
    assert len(node.source_tokens) == 2
    # The slice is indented, so the `def` keyword starts at column 4, not 0.
    assert (4, 3, "kw") in node.source_tokens[0]
    assert node.source_truncated is False


def test_analyze_omits_source_when_embedding_is_off(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "m.py").write_text("def f():\n    return 1\n")
    graph, _ = analyze([root], embed_source=False)
    node = graph.nodes["m.f"]
    assert node.source is None
    assert node.source_tokens is None

def test_pointing_at_a_package_directory_names_modules_from_its_parent(tmp_path):
    """`codecards src/mypkg` is the normal invocation, but mypkg is not the
    import root: Python puts its parent on the path. Measuring from the
    directory itself named mypkg/cli.py as `cli` and mypkg/__init__.py as the
    empty string, which reached the canvas as a blank unnamed card."""
    root = tmp_path / "mypkg"
    (root / "sub").mkdir(parents=True)
    (root / "__init__.py").write_text("def top():\n    pass\n")
    (root / "cli.py").write_text("def main():\n    pass\n")
    (root / "sub" / "__init__.py").write_text("")
    (root / "sub" / "deep.py").write_text("def go():\n    pass\n")

    graph, _ = analyze([root])

    assert "" not in graph.nodes
    assert "mypkg.cli.main" in graph.nodes
    assert "mypkg.top" in graph.nodes
    assert "mypkg.sub.deep.go" in graph.nodes
    assert [n.id for n in graph.nodes.values() if n.parent is None] == ["mypkg"]


def test_a_plain_directory_root_is_unchanged(tmp_path):
    """Only a package directory shifts the naming root."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "loose.py").write_text("def go():\n    pass\n")
    graph, _ = analyze([root])
    assert "loose.go" in graph.nodes


def test_nested_package_roots_walk_all_the_way_up(tmp_path):
    """Pointing inside a package tree still names from outside it."""
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    for part in (tmp_path / "a", tmp_path / "a" / "b", deep):
        (part / "__init__.py").write_text("")
    (deep / "m.py").write_text("def go():\n    pass\n")
    graph, _ = analyze([deep])
    assert "a.b.c.m.go" in graph.nodes


def test_special_methods_are_not_reported_as_dead_code(tmp_path):
    """__eq__ runs on ==, __hash__ on a dict lookup. Neither has a call site
    the analyser can see, so the structural "nothing calls this" rule brands
    both dead code and lists them as ways into the program."""
    root = project(tmp_path, {"m.py": (
        "class H:\n"
        "    def __init__(self):\n        pass\n"
        "    def __eq__(self, other):\n        return True\n"
        "    def __hash__(self):\n        return 1\n"
        "    @property\n"
        "    def size(self):\n        return 2\n"
        "    def helper(self):\n        pass\n"
    )})
    graph, _ = analyze([root])

    for name in ("__init__", "__eq__", "__hash__"):
        node = graph.nodes[f"m.H.{name}"]
        assert node.implicitly_called is True, name
        assert node.is_dunder is True, name

    # a property is invoked by attribute access, so it is implicit too, but it
    # is not a dunder and the interface does not hide it with them
    prop = graph.nodes["m.H.size"]
    assert prop.implicitly_called is True
    assert prop.is_dunder is False

    plain = graph.nodes["m.H.helper"]
    assert plain.implicitly_called is False

    flagged = {h.node_id for h in graph.entry_hints}
    assert "m.H.__eq__" not in flagged
    assert "m.H.size" not in flagged


def test_a_plain_uncalled_function_is_still_reported(tmp_path):
    """The exemption must not swallow genuine dead code."""
    root = project(tmp_path, {"m.py": "def nobody_calls_me():\n    pass\n"})
    graph, _ = analyze([root])
    assert graph.nodes["m.nobody_calls_me"].implicitly_called is False
