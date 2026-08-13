"""Walk a function body and yield the calls it makes.

Traversal only: nothing here knows what a call resolves to. The walkers are
separated from the resolver because they answer a different question, and
because they are mutually recursive over the whole Python grammar and are
easier to reason about on their own.

Flags are computed per field rather than per statement. A statement's
children do not all inherit its conditional or loop status just because
their parent has it: a while-test always runs while its body might not, and
a while-body runs repeatedly while its else-clause runs at most once.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

#: ast.TryStar only exists on 3.11+; guard so this module still imports on the 3.10
#: floor. Branching on sys.version_info (rather than hasattr) also lets pyright
#: statically narrow isinstance(node, _TRY_NODES) to the right node type below.
if sys.version_info >= (3, 11):
    _TRY_NODES = (ast.Try, ast.TryStar)
else:
    _TRY_NODES = (ast.Try,)
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def iter_calls(
    body: list[ast.stmt], in_conditional: bool = False, in_loop: bool = False
) -> Iterator[tuple[ast.Call, bool, bool]]:
    """Yield calls in this body, not descending into nested definitions.

    Flags are computed per field, not per statement: a statement's children do
    not all inherit the same conditional/loop status just because their parent
    does (a while-test always runs, a while-body might not; a while-body runs
    repeatedly, a while-else runs at most once).
    """
    for stmt in body:
        yield from _iter_stmt(stmt, in_conditional, in_loop)


def _iter_stmt(
    node: ast.stmt, conditional: bool, loop: bool
) -> Iterator[tuple[ast.Call, bool, bool]]:
    if isinstance(node, (*FUNCTION_NODES, ast.ClassDef)):
        return  # a separate caller - not this function's business

    if isinstance(node, ast.If):
        yield from _iter_expr(node.test, conditional, loop)
        yield from _iter_stmts(node.body, True, loop)
        yield from _iter_stmts(node.orelse, True, loop)
        return

    if isinstance(node, ast.While):
        yield from _iter_expr(node.test, conditional, loop)
        yield from _iter_stmts(node.body, conditional, True)
        yield from _iter_stmts(node.orelse, True, loop)
        return

    if isinstance(node, (ast.For, ast.AsyncFor)):
        yield from _iter_expr(node.iter, conditional, loop)
        yield from _iter_stmts(node.body, conditional, True)
        yield from _iter_stmts(node.orelse, True, loop)
        return

    if isinstance(node, _TRY_NODES):
        yield from _iter_stmts(node.body, conditional, loop)
        for handler in node.handlers:
            if handler.type is not None:
                yield from _iter_expr(handler.type, True, loop)
            yield from _iter_stmts(handler.body, True, loop)
        yield from _iter_stmts(node.orelse, True, loop)
        yield from _iter_stmts(node.finalbody, conditional, loop)
        return

    if isinstance(node, ast.Match):
        yield from _iter_expr(node.subject, conditional, loop)
        for case in node.cases:
            if case.guard is not None:
                yield from _iter_expr(case.guard, True, loop)
            yield from _iter_stmts(case.body, True, loop)
        return

    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            yield from _iter_expr(item.context_expr, conditional, loop)
        yield from _iter_stmts(node.body, conditional, loop)
        return

    # Any other statement: its expr/stmt children inherit the flags unchanged.
    # Anything that is neither (e.g. a with-item, a match case) falls through
    # to _iter_any, which reaches it without pretending to know its semantics.
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            yield from _iter_expr(child, conditional, loop)
        elif isinstance(child, ast.stmt):
            yield from _iter_stmt(child, conditional, loop)
        else:
            yield from _iter_any(child, conditional, loop)


def _iter_stmts(
    stmts: list[ast.stmt], conditional: bool, loop: bool
) -> Iterator[tuple[ast.Call, bool, bool]]:
    for stmt in stmts:
        yield from _iter_stmt(stmt, conditional, loop)


def _iter_expr(
    node: ast.expr, conditional: bool, loop: bool
) -> Iterator[tuple[ast.Call, bool, bool]]:
    if isinstance(node, ast.Call):
        yield node, conditional, loop
        yield from _iter_expr(node.func, conditional, loop)
        for arg in node.args:
            yield from _iter_expr(arg, conditional, loop)
        for kw in node.keywords:
            yield from _iter_expr(kw.value, conditional, loop)
        return

    if isinstance(node, ast.IfExp):
        yield from _iter_expr(node.test, conditional, loop)
        yield from _iter_expr(node.body, True, loop)
        yield from _iter_expr(node.orelse, True, loop)
        return

    if isinstance(node, _COMPREHENSION_NODES):
        generators = node.generators
        if generators:
            first, *rest = generators
            yield from _iter_expr(first.iter, conditional, loop)
            for if_expr in first.ifs:
                yield from _iter_expr(if_expr, conditional, True)
            for gen in rest:
                yield from _iter_expr(gen.iter, conditional, True)
                for if_expr in gen.ifs:
                    yield from _iter_expr(if_expr, conditional, True)
        if isinstance(node, ast.DictComp):
            yield from _iter_expr(node.key, conditional, True)
            yield from _iter_expr(node.value, conditional, True)
        else:
            yield from _iter_expr(node.elt, conditional, True)
        return

    # Any other expression: recurse into its expr/stmt children unchanged.
    # Anything that is neither - e.g. a Lambda's ast.arguments, which holds
    # the default-value expressions - falls through to _iter_any so it is
    # still reached rather than silently dropped.
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*FUNCTION_NODES, ast.ClassDef)):
            continue
        if isinstance(child, ast.expr):
            yield from _iter_expr(child, conditional, loop)
        elif isinstance(child, ast.stmt):
            yield from _iter_stmt(child, conditional, loop)
        else:
            yield from _iter_any(child, conditional, loop)


def _iter_any(
    node: ast.AST, conditional: bool, loop: bool
) -> Iterator[tuple[ast.Call, bool, bool]]:
    """Fallback for AST nodes that are neither a statement nor an expression:
    ast.arguments (a Lambda's defaults live here), ast.keyword, ast.withitem,
    ast.comprehension, ast.ExceptHandler, ast.match_case, and anything else no
    one enumerated by hand.

    _iter_stmt and _iter_expr know the precise semantics of the node types
    they special-case, so a call found there gets a flag that is provably
    correct. This function does not know the semantics of whatever it is
    given - it just inherits the flags unchanged and keeps recursing. That is
    a deliberate trade: an unenumerated node costs at most an imprecise flag,
    never a silently missing call. (Most of what reaches here is already
    handled precisely by name at its point of use - e.g. ast.withitem via
    ast.With, ast.comprehension via the comprehension node types - so this
    is primarily the ast.arguments/ast.keyword catch-all.)
    """
    if isinstance(node, ast.expr):
        yield from _iter_expr(node, conditional, loop)
        return
    if isinstance(node, (*FUNCTION_NODES, ast.ClassDef)):
        return  # a separate caller - not this function's business
    if isinstance(node, ast.stmt):
        yield from _iter_stmt(node, conditional, loop)
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_any(child, conditional, loop)


def iter_own_scope(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Yield every node reachable in this function's own scope, in source order.

    Does not cross into a nested function or class body - but does yield the
    def/class node itself, so a caller can see its name without seeing what is
    local to it.

    Pre-order (parent before children, siblings left-to-right), via an
    explicit stack rather than Python recursion: a caller (the type-inference
    walk in _bind_locals) depends on this being genuine source order so that
    "the last binding wins" is correct, and other callers walk expression
    trees deep enough to blow the recursion limit if this recursed instead.
    Children are pushed in reverse so the leftmost is popped first.
    """
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (*FUNCTION_NODES, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))
