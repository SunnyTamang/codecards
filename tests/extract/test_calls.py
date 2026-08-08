from __future__ import annotations

from pathlib import Path

from codecards.extract.calls import resolve_calls, to_edges
from codecards.extract.symbols import build_symbol_table
from codecards.graph.model import Confidence


def edges_for(tmp_path: Path, files: dict[str, str]):
    """Return {(caller, target): confidence} for a small synthetic project."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    table, _ = build_symbol_table(sorted(tmp_path.rglob("*.py")), [tmp_path])
    calls = resolve_calls(table)
    return {(c.caller, c.target): c.confidence for c in calls}


def test_module_level_call_resolves(tmp_path):
    result = edges_for(tmp_path, {"m.py": "def a():\n    pass\n\ndef b():\n    a()\n"})
    assert result[("m.b", "m.a")] is Confidence.RESOLVED


def test_call_through_a_from_import_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": "from app.util import parse\n\ndef go():\n    parse()\n",
    })
    assert result[("app.cli.go", "app.util.parse")] is Confidence.RESOLVED


def test_call_through_an_aliased_import_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": "from app.util import parse as p\n\ndef go():\n    p()\n",
    })
    assert result[("app.cli.go", "app.util.parse")] is Confidence.RESOLVED


def test_relative_import_call_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": "from .util import parse\n\ndef go():\n    parse()\n",
    })
    assert result[("app.cli.go", "app.util.parse")] is Confidence.RESOLVED


def test_nested_function_call_resolves_to_the_nested_definition(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def helper():\n    pass\n"
        "\n"
        "def outer():\n"
        "    def helper():\n        pass\n"
        "    helper()\n"
    )})
    assert result[("m.outer", "m.outer.helper")] is Confidence.RESOLVED
    assert ("m.outer", "m.helper") not in result


def test_a_parameter_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go(parse):\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_builtin_call_is_external(tmp_path):
    result = edges_for(tmp_path, {"m.py": "def go():\n    print('x')\n"})
    assert result[("m.go", "print")] is Confidence.EXTERNAL


def test_constructor_call_retargets_to_init(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def __init__(self):\n        pass\n"
        "\n"
        "def go():\n    H()\n"
    )})
    assert result[("m.go", "m.H.__init__")] is Confidence.RESOLVED


def test_constructor_without_init_is_unresolved(tmp_path):
    result = edges_for(tmp_path, {"m.py": "class H:\n    pass\n\ndef go():\n    H()\n"})
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_getattr_call_is_unresolved(tmp_path):
    result = edges_for(tmp_path, {"m.py": "def go(o, n):\n    getattr(o, n)()\n"})
    assert ("m.go", None) in result


def test_call_sites_record_line_and_context(tmp_path):
    for rel, text in {"m.py": (
        "def a():\n    pass\n"
        "\n"
        "def go(flag):\n"
        "    if flag:\n"
        "        a()\n"
        "    for _ in range(3):\n"
        "        a()\n"
    )}.items():
        (tmp_path / rel).write_text(text)
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.a"]
    assert sites[0].line == 6 and sites[0].in_conditional is True
    assert sites[1].line == 8 and sites[1].in_loop is True


def test_to_edges_groups_by_caller_target_and_tier(tmp_path):
    (tmp_path / "m.py").write_text("def a():\n    pass\n\ndef go():\n    a()\n    a()\n")
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    edges = to_edges(resolve_calls(table))
    internal = [e for e in edges if e.target == "m.a"]
    assert len(internal) == 1
    assert len(internal[0].call_sites) == 2


def test_to_edges_drops_calls_without_a_target(tmp_path):
    (tmp_path / "m.py").write_text("def go(f):\n    f()\n")
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    assert to_edges(resolve_calls(table)) == []
