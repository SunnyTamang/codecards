"""Every module must import standalone, in any order.

A circular import is invisible to an ordinary test suite: once one test has
imported the package in a working order, every later test inherits a populated
`sys.modules` and the cycle never fires again. So each module is imported here
in a *fresh interpreter*, first thing, which is what a user does.

This exists because `report.py` imported `extract.discovery` while
`extract/__init__.py` imported `report` for its return type. Reaching a
submodule initialises its parent package first, so `import codecards.report`
failed outright while `import codecards.extract` happened to work. The whole
suite was green throughout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

MODULES = [
    "codecards",
    "codecards.cli",
    "codecards.report",
    "codecards.extract",
    "codecards.extract.discovery",
    "codecards.extract.symbols",
    "codecards.extract.calls",
    "codecards.extract.highlight",
    "codecards.graph.model",
    "codecards.graph.collapse",
    "codecards.graph.entrypoints",
    "codecards.graph.walkthrough",
    "codecards.render",
    "codecards.render.viewmodel",
]


def import_in_fresh_interpreter(statement: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC), "PATH": ""},
    )


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_first_in_a_fresh_interpreter(module):
    result = import_in_fresh_interpreter(f"import {module}")
    assert result.returncode == 0, (
        f"`import {module}` fails when it is the first codecards import:\n"
        f"{result.stderr}"
    )


@pytest.mark.parametrize("module", MODULES)
def test_module_is_importable_via_from_syntax(module):
    """`from x import y` fails on a partially initialised module even when
    `import x` would have succeeded, so it is a separate check."""
    parent, _, leaf = module.rpartition(".")
    statement = f"from {parent} import {leaf}" if parent else f"import {module}"
    result = import_in_fresh_interpreter(statement)
    assert result.returncode == 0, f"`{statement}` fails:\n{result.stderr}"


def test_the_public_names_are_reachable_from_a_cold_start():
    result = import_in_fresh_interpreter(
        "from codecards.report import AnalysisReport;"
        "from codecards.extract import analyze;"
        "from codecards.cli import main;"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
