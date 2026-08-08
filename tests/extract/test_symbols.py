from __future__ import annotations

from pathlib import Path

from codecards.extract.symbols import build_symbol_table, module_id_for
from codecards.graph.model import NodeKind


def project(tmp_path: Path, files: dict[str, str]) -> tuple[list[Path], list[Path]]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sorted(tmp_path.rglob("*.py")), [tmp_path]


def build(tmp_path, files):
    paths, roots = project(tmp_path, files)
    return build_symbol_table(paths, roots)


def test_module_id_from_path(tmp_path):
    assert module_id_for(tmp_path / "app" / "mail.py", tmp_path) == "app.mail"
    assert module_id_for(tmp_path / "app" / "__init__.py", tmp_path) == "app"


def test_functions_classes_and_methods_are_recorded(tmp_path):
    table, _ = build(tmp_path, {"app/mail.py": (
        "def send(to, subject='hi'):\n"
        "    '''Send it.\n\n    More.\n    '''\n"
        "    return 1\n"
        "\n"
        "class H:\n"
        "    def go(self):\n"
        "        pass\n"
    )})
    send = table.definitions["app.mail.send"]
    assert send.kind is NodeKind.FUNCTION
    assert send.signature == "(to, subject='hi')"
    assert send.summary == "Send it."
    assert send.location.line_start == 1
    assert table.definitions["app.mail.H"].kind is NodeKind.CLASS
    assert table.definitions["app.mail.H.go"].kind is NodeKind.METHOD


def test_nested_functions_include_their_parent(tmp_path):
    table, _ = build(tmp_path, {"m.py": "def outer():\n    def inner():\n        pass\n"})
    assert "m.outer.inner" in table.definitions


def test_async_functions_are_recorded(tmp_path):
    table, _ = build(tmp_path, {"m.py": "async def go():\n    pass\n"})
    assert table.definitions["m.go"].is_async is True


def test_decorators_are_recorded(tmp_path):
    table, _ = build(tmp_path, {"m.py": "import click\n\n@click.command()\ndef go():\n    pass\n"})
    assert table.definitions["m.go"].decorators == ("click.command",)


def test_import_aliases_are_mapped(tmp_path):
    table, _ = build(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/cli.py": (
            "import app.util as u\n"
            "from app.util import parse\n"
            "from app.util import parse as p\n"
        ),
    })
    aliases = table.modules["app.cli"].aliases
    assert aliases["u"] == "app.util"
    assert aliases["parse"] == "app.util.parse"
    assert aliases["p"] == "app.util.parse"


def test_relative_imports_are_resolved(tmp_path):
    table, _ = build(tmp_path, {
        "app/__init__.py": "",
        "app/util.py": "def parse():\n    pass\n",
        "app/sub/__init__.py": "",
        "app/sub/deep.py": "from ..util import parse\nfrom . import sibling\n",
        "app/sub/sibling.py": "",
    })
    aliases = table.modules["app.sub.deep"].aliases
    assert aliases["parse"] == "app.util.parse"
    assert aliases["sibling"] == "app.sub.sibling"


def test_class_bases_are_resolved_through_aliases(tmp_path):
    table, _ = build(tmp_path, {
        "app/__init__.py": "",
        "app/base.py": "class Base:\n    def go(self):\n        pass\n",
        "app/impl.py": "from app.base import Base\n\nclass Impl(Base):\n    pass\n",
    })
    assert table.mro("app.impl.Impl") == ["app.impl.Impl", "app.base.Base"]


def test_mro_deduplicates_diamonds(tmp_path):
    table, _ = build(tmp_path, {"m.py": (
        "class A:\n    pass\n"
        "class B(A):\n    pass\n"
        "class C(A):\n    pass\n"
        "class D(B, C):\n    pass\n"
    )})
    assert table.mro("m.D") == ["m.D", "m.B", "m.A", "m.C"]


def test_simple_name_index_groups_by_last_segment(tmp_path):
    table, _ = build(tmp_path, {
        "a.py": "class X:\n    def go(self):\n        pass\n",
        "b.py": "class Y:\n    def go(self):\n        pass\n",
    })
    assert sorted(table.by_simple_name["go"]) == ["a.X.go", "b.Y.go"]


def test_main_block_calls_are_captured(tmp_path):
    table, _ = build(tmp_path, {"m.py": (
        "def go():\n    pass\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    go()\n"
    )})
    assert table.modules["m"].main_block_calls == ("go",)


def test_syntax_errors_are_skipped_not_raised(tmp_path):
    table, skipped = build(tmp_path, {"good.py": "def a():\n    pass\n", "bad.py": "def (\n"})
    assert "good.a" in table.definitions
    assert len(skipped) == 1
    assert "syntax error" in skipped[0].reason


def test_is_internal_distinguishes_project_from_stdlib(tmp_path):
    table, _ = build(tmp_path, {"app/__init__.py": "", "app/util.py": "def parse():\n    pass\n"})
    assert table.is_internal("app.util.parse") is True
    assert table.is_internal("app.util") is True
    assert table.is_internal("os.path.join") is False
