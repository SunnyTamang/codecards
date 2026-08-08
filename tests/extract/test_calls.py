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


# -- regression tests added after fix-round review -------------------------


def _call_count(tmp_path: Path, text: str) -> int:
    (tmp_path / "m.py").write_text(text)
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    return len(resolve_calls(table))


def test_triple_nested_call_yields_three_calls(tmp_path):
    assert _call_count(tmp_path, "def go():\n    f(g(h()))\n") == 3


def test_chained_attribute_call_yields_two_calls(tmp_path):
    assert _call_count(tmp_path, "def go():\n    a().b()\n") == 2


def test_double_call_yields_two_calls(tmp_path):
    assert _call_count(tmp_path, "def go():\n    outer()()\n") == 2


# -- Finding 1: only parameters were tracked as shadowed --------------------


def test_assignment_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go():\n"
        "    parse = 1\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_with_as_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go():\n"
        "    with open('f') as parse:\n"
        "        pass\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_except_as_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as parse:\n"
        "        pass\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_for_target_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go(xs):\n"
        "    for parse in xs:\n"
        "        pass\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_walrus_shadows_a_module_level_name(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def parse():\n    pass\n"
        "\n"
        "def go(xs):\n"
        "    if (parse := xs):\n"
        "        pass\n"
        "    parse()\n"
    )})
    assert ("m.go", "m.parse") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


# -- Finding 2: a nested def inside a conditional is not bound --------------


def test_nested_function_inside_a_conditional_is_unresolved(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def helper():\n    pass\n"
        "\n"
        "def go(x):\n"
        "    if x:\n"
        "        def helper():\n            pass\n"
        "    helper()\n"
    )})
    assert ("m.go", "m.helper") not in result
    assert result[("m.go", None)] is Confidence.UNRESOLVED


# -- Finding 3: conditional/loop flags were inherited by every descendant ---


def test_if_test_expression_call_is_not_conditional(tmp_path):
    (tmp_path / "m.py").write_text(
        "def cond():\n    return True\n"
        "\n"
        "def go():\n"
        "    if cond():\n"
        "        pass\n"
    )
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.cond"]
    assert sites[0].in_conditional is False


def test_finally_body_call_is_not_conditional(tmp_path):
    (tmp_path / "m.py").write_text(
        "def f():\n    pass\n"
        "\n"
        "def go():\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        f()\n"
    )
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.f"]
    assert sites[0].in_conditional is False


def test_while_else_body_call_is_not_in_loop(tmp_path):
    (tmp_path / "m.py").write_text(
        "def e():\n    pass\n"
        "\n"
        "def go(x):\n"
        "    while x:\n"
        "        break\n"
        "    else:\n"
        "        e()\n"
    )
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.e"]
    assert sites[0].in_loop is False


def test_for_iterable_call_is_not_in_loop(tmp_path):
    (tmp_path / "m.py").write_text(
        "def src():\n    return []\n"
        "\n"
        "def go():\n"
        "    for x in src():\n"
        "        pass\n"
    )
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.src"]
    assert sites[0].in_loop is False


def test_comprehension_element_call_is_in_loop(tmp_path):
    (tmp_path / "m.py").write_text(
        "def item(x):\n    return x\n"
        "\n"
        "def go(xs):\n"
        "    return [item(x) for x in xs]\n"
    )
    table, _ = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    sites = [c.site for c in resolve_calls(table) if c.target == "m.item"]
    assert sites[0].in_loop is True


# -- item 5: a pathological file cannot kill the whole run ------------------


def test_recursion_error_during_resolution_does_not_abort_the_run(tmp_path):
    # A long left-associated chain parses fine (unlike deeply nested parens or
    # indentation, which the parser itself rejects), but walking it recursively
    # can exceed Python's stack - this reproduces that without tripping pass 1.
    n = 2000
    chain = "1" + " + f()" * n
    (tmp_path / "ok.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    (tmp_path / "deep.py").write_text(f"def go():\n    x = {chain}\n")
    table, skipped = build_symbol_table([tmp_path / "ok.py", tmp_path / "deep.py"], [tmp_path])
    assert skipped == []
    assert "deep" in table.modules  # pass 1 accepted it - the bug is pass 2's
    calls = resolve_calls(table)
    assert any(c.caller == "ok.b" and c.target == "ok.a" for c in calls)
