from __future__ import annotations

from pathlib import Path

from codecards.extract.calls import MAX_AMBIGUOUS_CANDIDATES, resolve_calls, to_edges
from codecards.extract.discovery import SkippedFile
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


# -- fix round 2 -------------------------------------------------------------
#
# Two Important defects were introduced BY fix round 1 itself (not present in
# the original skeleton):
#
# 1. The rewrite of _iter_calls into per-field-aware _iter_stmt/_iter_expr only
#    recursed into children typed ast.expr or ast.stmt. ast.arguments (where a
#    lambda's default-value expressions live) is neither, so calls inside a
#    lambda default became silently unreachable - the old blanket recursion
#    reached them, the new explicit enumeration did not.
# 2. run() widened its except to (SyntaxError, RecursionError) around the
#    whole per-module walk. For SyntaxError that mirrors pass 1 correctly, but
#    a module that trips RecursionError during resolution IS in table.modules
#    (pass 1 accepted it) - its calls vanished with no record anywhere in the
#    public API, unlike build_symbol_table's SkippedFile list.


def test_lambda_default_value_call_is_reached(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "def missing_call():\n    pass\n"
        "\n"
        "def go():\n"
        "    f = lambda x=missing_call(): x\n"
        "    return f\n"
    )})
    assert result[("m.go", "m.missing_call")] is Confidence.RESOLVED


def test_construct_heavy_corpus_yields_exact_call_count(tmp_path):
    # Exercises: lambda defaults, keyword arguments, f-strings, starred args,
    # slices, await, yield from, decorators on a nested def, and comprehension
    # ifs, in one source file. This is a differential guard: it was verified
    # to return 11 both against the original skeleton
    # and against the current implementation, and 10 (missing the lambda
    # default call) against fix round 1 (commit bcaab12) - so a future change
    # that silently drops a construct's calls again will move this number.
    corpus = (
        "def missing_call():\n    pass\n"
        "\n"
        "def kwcall():\n    pass\n"
        "\n"
        "def fcall():\n    pass\n"
        "\n"
        "def starred_call():\n    pass\n"
        "\n"
        "def start_call():\n    pass\n"
        "\n"
        "def end_call():\n    pass\n"
        "\n"
        "def async_call():\n    pass\n"
        "\n"
        "def gen_call():\n    pass\n"
        "\n"
        "def if_call(x):\n    pass\n"
        "\n"
        "def deco_call():\n    pass\n"
        "\n"
        "def sink(*a, **kw):\n    pass\n"
        "\n"
        "async def go(xs):\n"
        "    lam = lambda x=missing_call(): x\n"
        "    sink(x=kwcall())\n"
        "    s = f'{fcall()}'\n"
        "    sink(*starred_call())\n"
        "    z = [1, 2, 3][start_call():end_call()]\n"
        "    await async_call()\n"
        "    def gen():\n"
        "        yield from gen_call()\n"
        "    @deco_call()\n"
        "    def inner():\n"
        "        pass\n"
        "    return [x for x in xs if if_call(x)]\n"
    )
    (tmp_path / "m.py").write_text(corpus)
    table, skipped = build_symbol_table([tmp_path / "m.py"], [tmp_path])
    assert skipped == []
    calls = resolve_calls(table)
    assert len(calls) == 11


def test_recursion_error_in_one_function_does_not_lose_its_module(tmp_path):
    n = 2000
    chain = "1" + " + f()" * n
    (tmp_path / "ok.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    (tmp_path / "deep.py").write_text(
        "def other():\n    pass\n"
        "\n"
        f"def go():\n    x = {chain}\n"
        "\n"
        "def also_fine():\n    other()\n"
    )
    table, _ = build_symbol_table([tmp_path / "ok.py", tmp_path / "deep.py"], [tmp_path])
    skipped: list[SkippedFile] = []
    calls = resolve_calls(table, skipped)

    # Only "deep.go" is lost - every other function, in the same module and
    # in every other module, still resolves normally.
    assert any(c.caller == "ok.b" and c.target == "ok.a" for c in calls)
    assert any(c.caller == "deep.also_fine" and c.target == "deep.other" for c in calls)
    assert not any(c.caller == "deep.go" for c in calls)

    # And the failure is reported, not swallowed: exactly one SkippedFile,
    # naming the offending module and function.
    assert len(skipped) == 1
    assert skipped[0].path == str(tmp_path / "deep.py")
    assert "deep.go" in skipped[0].reason


def test_recursion_error_with_no_skipped_list_still_does_not_abort(tmp_path):
    # The out-parameter is optional - omitting it must not change behaviour
    # for every other function, only drop the reporting.
    n = 2000
    chain = "1" + " + f()" * n
    (tmp_path / "ok.py").write_text("def a():\n    pass\n\ndef b():\n    a()\n")
    (tmp_path / "deep.py").write_text(f"def go():\n    x = {chain}\n")
    table, _ = build_symbol_table([tmp_path / "ok.py", tmp_path / "deep.py"], [tmp_path])
    calls = resolve_calls(table)
    assert any(c.caller == "ok.b" and c.target == "ok.a" for c in calls)
    assert not any(c.caller == "deep.go" for c in calls)


# -- attribute calls: modules, and self/cls through the MRO -----------------


def test_call_through_a_module_alias_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": "from app import util\n\ndef go():\n    util.parse()\n",
    })
    assert result[("app.cli.go", "app.util.parse")] is Confidence.RESOLVED


def test_call_through_a_dotted_module_import_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": "import app.util\n\ndef go():\n    app.util.parse()\n",
    })
    assert result[("app.cli.go", "app.util.parse")] is Confidence.RESOLVED


def test_self_call_resolves_within_the_class(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n"
        "    def send(self):\n        self.retry()\n"
        "    def retry(self):\n        pass\n"
    )})
    assert result[("m.H.send", "m.H.retry")] is Confidence.RESOLVED


def test_self_call_resolves_through_a_base_class(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class Base:\n    def retry(self):\n        pass\n"
        "\n"
        "class H(Base):\n    def send(self):\n        self.retry()\n"
    )})
    assert result[("m.H.send", "m.Base.retry")] is Confidence.RESOLVED


def test_cls_call_resolves_like_self(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n"
        "    @classmethod\n"
        "    def make(cls):\n        cls.build()\n"
        "    @classmethod\n"
        "    def build(cls):\n        pass\n"
    )})
    assert result[("m.H.make", "m.H.build")] is Confidence.RESOLVED


def test_super_call_resolves_to_the_base_method(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class Base:\n    def go(self):\n        pass\n"
        "\n"
        "class H(Base):\n    def go(self):\n        super().go()\n"
    )})
    assert result[("m.H.go", "m.Base.go")] is Confidence.RESOLVED


def test_self_call_to_an_unknown_name_is_unresolved(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        self.nope()\n"
    )})
    assert result[("m.H.send", None)] is Confidence.UNRESOLVED


def test_external_module_attribute_call_is_external(tmp_path):
    result = edges_for(tmp_path, {"m.py": "import json\n\ndef go():\n    json.dumps({})\n"})
    assert result[("m.go", "json.dumps")] is Confidence.EXTERNAL


def test_shadowed_receiver_does_not_resolve_through_a_module_alias(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": (
            "from app import util\n\n"
            "def go():\n    util = object()\n    util.parse()\n"
        ),
    })
    # util.parse() must not resolve through the module-alias path (util is
    # shadowed by the assignment) - but "parse" happens to be a globally
    # unique method name in this fixture, so the name-index fallback
    # still guesses it, honestly, at INFERRED rather than the RESOLVED tier
    # the alias path would have produced.
    assert result[("app.cli.go", "app.util.parse")] is Confidence.INFERRED


# -- shadowing and super(): cases that must refuse to resolve ---------------


def test_a_nested_def_shadows_a_receiver_even_though_it_is_not_a_parameter(tmp_path):
    # _bind_locals deliberately keeps a top-level nested def's name OUT of
    # context.shadowed (it lives in context.names instead, so a bare call to
    # it resolves to the nested definition). _resolve_receiver must honour
    # that too: a nested `def util(): ...` shadows the module named `util`
    # just as much as a parameter or assignment would, so `util.parse()`
    # must not resolve through the `from app import util` alias - at
    # runtime `util` is the local function, and `.parse()` would raise
    # AttributeError, not call app.util.parse.
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": (
            "from app import util\n\n"
            "def go():\n"
            "    def util():\n        pass\n"
            "    util.parse()\n"
        ),
    })
    # Same reasoning as the shadowed-receiver test above: the alias path must
    # not fire, but "parse" is still a globally unique name in this fixture,
    # so the name-index fallback guesses it honestly at INFERRED.
    assert result[("app.cli.go", "app.util.parse")] is Confidence.INFERRED


def test_super_call_with_explicit_arguments_does_not_resolve(tmp_path):
    # super(B, self) starts the search AFTER B in type(self)'s MRO, so the
    # correct target is m.A.go, not m.B.go - the one class the call
    # explicitly skips. Resolving explicit-super correctly needs the
    # runtime MRO of type(self), which static analysis does not have, so
    # this must stay UNRESOLVED rather than resolve to the wrong class.
    result = edges_for(tmp_path, {"m.py": (
        "class A:\n    def go(self):\n        pass\n"
        "\n"
        "class B(A):\n    def go(self):\n        pass\n"
        "\n"
        "class C(B):\n    def go(self):\n        super(B, self).go()\n"
    )})
    # super(B, self) tells us exactly which class the call SKIPS, so the
    # name-index fallback is suppressed here rather than being free to guess
    # that very class. Positive evidence, not absence of evidence.
    assert ("m.C.go", "m.B.go") not in result
    assert result[("m.C.go", None)] is Confidence.UNRESOLVED


def test_bare_super_call_still_resolves_to_the_base_method(tmp_path):
    # Same scenario as the explicit-arguments case above, but with bare
    # super() - must keep resolving via the MRO, unaffected by the fix that
    # rejects explicit arguments.
    result = edges_for(tmp_path, {"m.py": (
        "class A:\n    def go(self):\n        pass\n"
        "\n"
        "class B(A):\n    def go(self):\n        pass\n"
        "\n"
        "class C(B):\n    def go(self):\n        super().go()\n"
    )})
    assert result[("m.C.go", "m.B.go")] is Confidence.RESOLVED


def test_self_reassignment_prevents_confident_mro_resolution(tmp_path):
    # self is reassigned to a different object before the call, so it no
    # longer names the receiver the MRO walk assumes - resolving through
    # the class MRO here would be a confident guess about an object we no
    # longer know anything about.
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n"
        "    def send(self, other):\n"
        "        self = other\n"
        "        self.retry()\n"
        "    def retry(self):\n        pass\n"
    )})
    # `self` was rebound, so it provably no longer names the instance. The
    # name-index fallback is suppressed rather than left free to guess a
    # method of this very class, which is the one answer we have evidence
    # against.
    assert ("m.H.send", "m.H.retry") not in result
    assert result[("m.H.send", None)] is Confidence.UNRESOLVED


def test_call_through_a_same_module_class_receiver_resolves(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def make(self):\n        pass\n"
        "\n"
        "def go():\n    H.make(H())\n"
    )})
    assert result[("m.go", "m.H.make")] is Confidence.RESOLVED


# -- type-informed attribute calls -------------------------------------------


def test_annotated_parameter_resolves_the_method(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/mail.py": "class H:\n    def send(self):\n        pass\n",
        "app/cli.py": (
            "from app.mail import H\n"
            "\n"
            "def go(handler: H):\n"
            "    handler.send()\n"
        ),
    })
    assert result[("app.cli.go", "app.mail.H.send")] is Confidence.RESOLVED


def test_string_annotation_resolves(tmp_path):
    result = edges_for(tmp_path, {
        "app/__init__.py": "",
        "app/mail.py": "class H:\n    def send(self):\n        pass\n",
        "app/cli.py": (
            "from app.mail import H\n"
            "\n"
            "def go(handler: 'H'):\n"
            "    handler.send()\n"
        ),
    })
    assert result[("app.cli.go", "app.mail.H.send")] is Confidence.RESOLVED


def test_local_constructor_assignment_resolves(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.RESOLVED


def test_annotated_local_variable_resolves(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(x):\n"
        "    h: H = x\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.RESOLVED


def test_type_inference_resolves_through_the_mro(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class Base:\n    def send(self):\n        pass\n"
        "\n"
        "class H(Base):\n    pass\n"
        "\n"
        "def go(h: H):\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.Base.send")] is Confidence.RESOLVED


def test_reassignment_to_an_unknown_value_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(other):\n"
        "    h = H()\n"
        "    h = other\n"
        "    h.send()\n"
    )})
    # The type-inference path must not fire (h no longer names an H), but
    # "send" is still a globally unique name in this fixture, so the
    # name-index fallback guesses it honestly at INFERRED rather than the RESOLVED tier
    # the type-inference path would have produced.
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


# -- every rebinding form must drop a stale inferred type -------------------
#
# context.types was cleared only on Assign/AnnAssign. Every other rebinding
# form (augmented assignment, walrus, for-target, with-as target) left a
# stale inferred type behind, so a later call resolved at the RESOLVED tier
# against a class the name no longer named - a confident, wrong edge. The
# fix routes the discard off the same Name/Store,Del walk that already
# builds context.shadowed and context.reassigned, rather than enumerating
# these forms separately.


def test_for_target_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(xs):\n"
        "    h = H()\n"
        "    for h in xs:\n"
        "        pass\n"
        "    h.send()\n"
    )})
    # The type-inference path must not fire (h no longer names an H), but
    # "send" is still a globally unique name in this fixture, so the
    # name-index fallback guesses it honestly at INFERRED rather than RESOLVED.
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_with_as_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    with open('f') as h:\n"
        "        pass\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_walrus_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(xs):\n"
        "    h = H()\n"
        "    if (h := xs):\n"
        "        pass\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_augmented_assignment_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    h += 1\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_an_unrelated_statement_between_bindings_does_not_clear_the_type(tmp_path):
    # Proves the fix did not over-clear: an unrelated assignment to a
    # DIFFERENT name in between must not disturb h's inferred type.
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    y = 5\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.RESOLVED


# -- a type is kept only when the name is bound exactly once ----------------
#
# Fix round 1 tried to prove a candidate type had been destroyed, by
# enumerating rebinding forms that reach a generic Name/Store,Del node. That
# enumeration was itself incomplete: match capture, an in-function
# `import ... as`, `nonlocal` rebinding from a nested closure, and a nested
# lambda's own parameter of the same name all bind through a plain string
# field or a different node type entirely, never reaching that branch, and
# all four produced a confident, wrong RESOLVED edge. The fix inverts the
# polarity: a candidate type now survives only when its name is bound
# EXACTLY ONCE across the whole function (including nested scopes), so an
# unenumerated rebinding form costs a missing type, never a wrong one.


def test_tuple_unpacking_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(xs):\n"
        "    h = H()\n"
        "    h, k = xs\n"
        "    h.send()\n"
    )})
    # The type-inference path must not fire, but "send" is still a globally
    # unique name in this fixture, so the name-index fallback guesses it honestly
    # at INFERRED rather than RESOLVED.
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_del_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    del h\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_match_capture_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go(x):\n"
        "    h = H()\n"
        "    match x:\n"
        "        case str() as h:\n"
        "            pass\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_in_function_import_reassignment_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    import os as h\n"
        "    h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


def test_nonlocal_rebinding_from_a_closure_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "class G:\n    pass\n"
        "\n"
        "def outer():\n"
        "    h = H()\n"
        "    def inner():\n"
        "        nonlocal h\n"
        "        h = G()\n"
        "    inner()\n"
        "    h.send()\n"
    )})
    assert result[("m.outer", "m.H.send")] is Confidence.INFERRED


def test_lambda_parameter_shadowing_a_typed_name_drops_the_type(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def send(self):\n        pass\n"
        "\n"
        "def go():\n"
        "    h = H()\n"
        "    f = lambda h: h.send()\n"
    )})
    assert result[("m.go", "m.H.send")] is Confidence.INFERRED


# -- the inferred and ambiguous tiers ----------------------------------------


def test_a_unique_method_name_is_inferred(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n    def dispatch_email(self):\n        pass\n"
        "\n"
        "def go(thing):\n"
        "    thing.dispatch_email()\n"
    )})
    assert result[("m.go", "m.H.dispatch_email")] is Confidence.INFERRED


def test_a_repeated_method_name_is_ambiguous(tmp_path):
    # edges_for keys by (caller, target), and an AMBIGUOUS ResolvedCall has
    # target=None - that collapses every candidate into one (caller, None)
    # entry and loses exactly the fan-out this test needs to see, so it goes
    # through to_edges directly instead, the same way a real consumer would.
    for rel, text in {
        "a.py": "class X:\n    def run(self):\n        pass\n",
        "b.py": "class Y:\n    def run(self):\n        pass\n",
        "c.py": "def go(thing):\n    thing.run()\n",
    }.items():
        (tmp_path / rel).write_text(text)
    table, _ = build_symbol_table(sorted(tmp_path.rglob("*.py")), [tmp_path])
    edges = to_edges(resolve_calls(table))
    ambiguous = {e.target for e in edges
                 if e.source == "c.go" and e.confidence is Confidence.AMBIGUOUS}
    assert ambiguous == {"a.X.run", "b.Y.run"}


def test_ambiguous_calls_record_every_candidate(tmp_path):
    for rel, text in {
        "a.py": "class X:\n    def run(self):\n        pass\n",
        "b.py": "class Y:\n    def run(self):\n        pass\n",
        "c.py": "def go(thing):\n    thing.run()\n",
    }.items():
        (tmp_path / rel).write_text(text)
    table, _ = build_symbol_table(sorted(tmp_path.rglob("*.py")), [tmp_path])
    call = next(c for c in resolve_calls(table) if c.caller == "c.go")
    assert sorted(call.candidates) == ["a.X.run", "b.Y.run"]


def test_an_unknown_method_name_stays_unresolved(tmp_path):
    result = edges_for(tmp_path, {"m.py": "def go(thing):\n    thing.nothing_defined()\n"})
    assert result[("m.go", None)] is Confidence.UNRESOLVED


def test_inference_never_overrides_a_certain_resolution(tmp_path):
    result = edges_for(tmp_path, {"m.py": (
        "class H:\n"
        "    def send(self):\n        self.retry()\n"
        "    def retry(self):\n        pass\n"
        "\n"
        "class Other:\n    def retry(self):\n        pass\n"
    )})
    assert result[("m.H.send", "m.H.retry")] is Confidence.RESOLVED


def test_module_level_functions_participate_in_inference(tmp_path):
    result = edges_for(tmp_path, {
        "a.py": "def uniquely_named():\n    pass\n",
        "b.py": "def go(thing):\n    thing.uniquely_named()\n",
    })
    assert result[("b.go", "a.uniquely_named")] is Confidence.INFERRED


def test_a_wildly_ambiguous_call_draws_no_edges(tmp_path):
    """An ambiguous call fans out to every candidate, which is informative
    while the candidates are few and useless when they are not. On
    scikit-learn `.fit()` has 314 definitions, and its ambiguous calls alone
    produced 1.5 million edges and a 250MB page.

    Goes through to_edges, since that is where the fan-out happens: a
    ResolvedCall carries the candidates but no target."""
    def fan_out(root, candidates):
        root.mkdir(parents=True, exist_ok=True)
        for i in range(candidates):
            (root / f"m{i}.py").write_text(f"class C{i}:\n    def go(self):\n        pass\n")
        (root / "caller.py").write_text("def use(thing):\n    thing.go()\n")
        table, _ = build_symbol_table(sorted(root.rglob("*.py")), [root])
        return [e for e in to_edges(resolve_calls(table)) if e.source == "caller.use"]

    at_cap = fan_out(tmp_path / "at", MAX_AMBIGUOUS_CANDIDATES)
    assert len(at_cap) == MAX_AMBIGUOUS_CANDIDATES
    assert all(e.confidence is Confidence.AMBIGUOUS for e in at_cap)

    over = fan_out(tmp_path / "over", MAX_AMBIGUOUS_CANDIDATES + 1)
    assert over == [], "past the cap the call should draw nothing at all"


def test_an_undrawn_ambiguous_call_is_still_counted(tmp_path):
    """Dropping the edges must not quietly improve the resolution rate: the
    call is still ambiguous, it just is not worth drawing."""
    root = tmp_path / "big"
    root.mkdir()
    for i in range(MAX_AMBIGUOUS_CANDIDATES + 5):
        (root / f"m{i}.py").write_text(f"class C{i}:\n    def go(self):\n        pass\n")
    (root / "caller.py").write_text("def use(thing):\n    thing.go()\n")
    table, _ = build_symbol_table(sorted(root.rglob("*.py")), [root])
    calls = resolve_calls(table)
    assert any(c.caller == "caller.use" and c.confidence is Confidence.AMBIGUOUS
               for c in calls)


# -- a definition nothing can reach is not a candidate ----------------------
#
# The inferred tier matches an attribute call against every definition with
# that name. A function defined inside another function is created when its
# holder runs and gone when it returns, so nothing outside can name it - yet
# it was a candidate like any other. Those helpers are named for what they do
# locally, which is exactly the vocabulary of the language's own methods, so
# one `def get(key)` nested in a main() made every dict.get() call in the
# codebase point at it.


def test_a_nested_helper_is_not_inferred_from_another_module(tmp_path):
    """`state.get(...)` is a dict method, not a call into somebody's main()."""
    result = edges_for(tmp_path, {
        "runner.py": (
            "def main():\n"
            "    final = {}\n"
            "    def get(key):\n"
            "        return final[key]\n"
            "    return get('a')\n"
        ),
        "other.py": (
            "def score(state):\n"
            "    return state.get('reasoning')\n"
        ),
    })
    assert ("other.score", "runner.main.get") not in result


def test_a_nested_helper_is_still_called_by_the_body_holding_it(tmp_path):
    """The rule must not cost the one call that is real: a plain name in the
    scope that defines it, which never reaches the name-matching path."""
    result = edges_for(tmp_path, {"runner.py": (
        "def main():\n"
        "    final = {}\n"
        "    def get(key):\n"
        "        return final[key]\n"
        "    return get('a')\n"
    )})
    assert result[("runner.main", "runner.main.get")] is Confidence.RESOLVED


def test_a_method_is_still_inferred(tmp_path):
    """A method's holder is a class, and a class is reachable. Only functions
    inside functions are unreachable."""
    result = edges_for(tmp_path, {
        "m.py": "class Mailer:\n    def deliver(self):\n        pass\n",
        "other.py": "def go(x):\n    return x.deliver()\n",
    })
    assert result[("other.go", "m.Mailer.deliver")] is Confidence.INFERRED


# -- resolving through a declared type ---------------------------------------

#: The dependency-injection shape this project is repeatedly pointed at: one
#: real class, and several stand-ins defining the same method names. Only the
#: annotation distinguishes them.
_INJECTED = {
    "app/__init__.py": "",
    "app/client.py": "class LLMClient:\n    def complete(self, text):\n        pass\n",
    "app/trace.py": (
        "class Tracing:\n"
        "    def __init__(self, inner):\n"
        "        self._inner = inner\n"
        "    def complete(self, text):\n"
        "        return self._inner.complete(text)\n"
    ),
    "tests_pkg/__init__.py": "",
    "tests_pkg/fake.py": "class FakeClient:\n    def complete(self, text):\n        pass\n",
}


def test_an_optional_annotation_names_the_class_it_wraps(tmp_path):
    """`X | None` is the ordinary way to write an injected dependency. Reading
    only a bare `X` leaves the commonest form of the idiom unresolved."""
    result = edges_for(tmp_path, {**_INJECTED, "app/node.py": (
        "from app.client import LLMClient\n\n"
        "def summarise(client: LLMClient | None):\n"
        "    return client.complete('x')\n"
    )})
    assert result[("app.node.summarise", "app.client.LLMClient.complete")] \
        is Confidence.RESOLVED


def test_optional_spelled_the_typing_way_also_names_its_class(tmp_path):
    result = edges_for(tmp_path, {**_INJECTED, "app/node.py": (
        "from typing import Optional\n"
        "from app.client import LLMClient\n\n"
        "def summarise(client: Optional[LLMClient]):\n"
        "    return client.complete('x')\n"
    )})
    assert result[("app.node.summarise", "app.client.LLMClient.complete")] \
        is Confidence.RESOLVED


def test_a_default_filled_in_from_the_same_class_keeps_the_declared_type(tmp_path):
    """`client = client or LLMClient()` rebinds the name, but both branches
    carry the same class, so the declared type survives the rebinding."""
    result = edges_for(tmp_path, {**_INJECTED, "app/node.py": (
        "from app.client import LLMClient\n\n"
        "def summarise(client: LLMClient | None = None):\n"
        "    client = client or LLMClient()\n"
        "    return client.complete('x')\n"
    )})
    assert result[("app.node.summarise", "app.client.LLMClient.complete")] \
        is Confidence.RESOLVED
    # and nothing is drawn to the stand-ins that merely share the method name
    assert ("app.node.summarise", "tests_pkg.fake.FakeClient.complete") not in result
    assert ("app.node.summarise", "app.trace.Tracing.complete") not in result


def test_a_union_of_two_classes_is_not_resolved_to_either(tmp_path):
    """Two real possibilities is not a declared type. Picking one would be a
    guess, and the whole point of the tier is that guesses are marked."""
    result = edges_for(tmp_path, {**_INJECTED, "app/node.py": (
        "from app.client import LLMClient\n"
        "from tests_pkg.fake import FakeClient\n\n"
        "def summarise(client: LLMClient | FakeClient):\n"
        "    return client.complete('x')\n"
    )})
    assert ("app.node.summarise", "app.client.LLMClient.complete") not in result


def test_an_unrecognised_rebinding_still_drops_the_declared_type(tmp_path):
    """The safety property: a binding form this code cannot type must cost a
    missing edge, never a wrong one."""
    result = edges_for(tmp_path, {**_INJECTED, "app/node.py": (
        "from app.client import LLMClient\n\n"
        "def summarise(client: LLMClient | None, others):\n"
        "    for client in others:\n"
        "        pass\n"
        "    return client.complete('x')\n"
    )})
    assert result.get(("app.node.summarise", "app.client.LLMClient.complete")) \
        is not Confidence.RESOLVED


def test_an_untyped_wrapper_stays_ambiguous(tmp_path):
    """Tracing holds `inner` with no annotation, so there is genuinely nothing
    to resolve and the call must stay ambiguous rather than pick a favourite."""
    result = edges_for(tmp_path, {**_INJECTED})
    assert ("app.trace.Tracing.complete", "app.client.LLMClient.complete") not in result
