"""Pass 2 - resolve every call site against the completed symbol table.

Each call gets a confidence tier. Nothing is silently guessed: an attribute
call the resolver cannot pin down is reported as inferred, ambiguous or
unresolved rather than being drawn as if it were certain.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from typing import Iterator

from ..graph.model import CallSite, Confidence, Edge, NodeKind
from .symbols import SymbolTable

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_BUILTINS = frozenset(dir(builtins))


@dataclass(frozen=True)
class ResolvedCall:
    caller: str
    target: str | None
    confidence: Confidence
    site: CallSite
    candidates: tuple[str, ...] = ()


@dataclass
class _Context:
    """Everything known about the function body currently being walked."""

    caller: str
    module_id: str
    class_qualname: str | None
    names: dict[str, str]  # local name -> callable qualname (nested defs)
    types: dict[str, str]  # local name -> class qualname
    shadowed: set[str]  # local bindings that block module-scope lookup


def resolve_calls(table: SymbolTable) -> list[ResolvedCall]:
    resolver = _Resolver(table)
    return resolver.run()


def to_edges(calls: list[ResolvedCall]) -> list[Edge]:
    grouped: dict[tuple[str, str, Confidence], list[CallSite]] = {}
    for call in calls:
        if call.target is None:
            continue
        grouped.setdefault((call.caller, call.target, call.confidence), []).append(call.site)
    return [
        Edge(source=caller, target=target, confidence=confidence,
             call_sites=tuple(sorted(sites, key=lambda s: s.line)))
        for (caller, target, confidence), sites in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value)
        )
    ]


def _iter_calls(
    body: list[ast.stmt], in_conditional: bool = False, in_loop: bool = False
) -> Iterator[tuple[ast.Call, bool, bool]]:
    """Yield calls in this body, not descending into nested definitions."""
    for node in body:
        if isinstance(node, (*_FUNCTION_NODES, ast.ClassDef)):
            continue
        conditional = in_conditional or isinstance(node, (ast.If, ast.Try, ast.Match))
        loop = in_loop or isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (*_FUNCTION_NODES, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                yield child, conditional, loop
                for grandchild in ast.iter_child_nodes(child):
                    yield from _iter_calls([grandchild], conditional, loop)  # type: ignore[list-item]
            elif isinstance(child, ast.stmt):
                yield from _iter_calls([child], conditional, loop)
            else:
                yield from _iter_calls([child], conditional, loop)  # type: ignore[list-item]
        if isinstance(node, ast.Call):
            yield node, conditional, loop


class _Resolver:
    def __init__(self, table: SymbolTable) -> None:
        self.table = table
        self.calls: list[ResolvedCall] = []

    def run(self) -> list[ResolvedCall]:
        for module_id, info in sorted(self.table.modules.items()):
            try:
                tree = ast.parse(info.source, filename=str(info.path))
            except SyntaxError:  # already reported in pass 1
                continue
            self._visit_body(tree.body, module_id, parent=module_id, class_qualname=None)
        return self.calls

    def _visit_body(
        self, body: list[ast.stmt], module_id: str, parent: str, class_qualname: str | None
    ) -> None:
        for node in body:
            if isinstance(node, _FUNCTION_NODES):
                qualname = f"{parent}.{node.name}"
                self._walk_function(node, module_id, qualname, class_qualname)
                self._visit_body(node.body, module_id, qualname, class_qualname=None)
            elif isinstance(node, ast.ClassDef):
                qualname = f"{parent}.{node.name}"
                self._visit_body(node.body, module_id, qualname, class_qualname=qualname)

    def _walk_function(
        self, node: ast.AST, module_id: str, caller: str, class_qualname: str | None
    ) -> None:
        context = _Context(
            caller=caller,
            module_id=module_id,
            class_qualname=class_qualname,
            names={},
            types={},
            shadowed=set(),
        )
        self._bind_locals(node, context)
        for call, in_conditional, in_loop in _iter_calls(node.body):
            site = CallSite(
                line=call.lineno, in_conditional=in_conditional, in_loop=in_loop
            )
            target, confidence, candidates = self._resolve(call.func, context)
            self.calls.append(
                ResolvedCall(
                    caller=caller,
                    target=target,
                    confidence=confidence,
                    site=site,
                    candidates=candidates,
                )
            )

    def _bind_locals(self, node: ast.AST, context: _Context) -> None:
        args = node.args
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            every.append(args.vararg)
        if args.kwarg:
            every.append(args.kwarg)
        for arg in every:
            context.shadowed.add(arg.arg)
        for inner in node.body:
            if isinstance(inner, _FUNCTION_NODES):
                context.names[inner.name] = f"{context.caller}.{inner.name}"

    # -- resolution strategies -------------------------------------------

    def _resolve(
        self, func: ast.expr, context: _Context
    ) -> tuple[str | None, Confidence, tuple[str, ...]]:
        if isinstance(func, ast.Name):
            return self._resolve_name(func.id, context)
        return None, Confidence.UNRESOLVED, ()

    def _resolve_name(
        self, name: str, context: _Context
    ) -> tuple[str | None, Confidence, tuple[str, ...]]:
        if name in context.names:
            return self._finalise(context.names[name])
        if name in context.shadowed:
            return None, Confidence.UNRESOLVED, ()
        aliases = self.table.modules[context.module_id].aliases
        if name in aliases:
            return self._finalise(aliases[name])
        module_level = f"{context.module_id}.{name}"
        if module_level in self.table.definitions:
            return self._finalise(module_level)
        if name in _BUILTINS:
            return name, Confidence.EXTERNAL, ()
        return None, Confidence.UNRESOLVED, ()

    def _finalise(
        self, target: str
    ) -> tuple[str | None, Confidence, tuple[str, ...]]:
        """Apply the class-to-__init__ retarget and the internal/external split."""
        if not self.table.is_internal(target):
            return target, Confidence.EXTERNAL, ()
        definition = self.table.definitions.get(target)
        if definition is None:
            return target, Confidence.EXTERNAL, ()
        if definition.kind is NodeKind.CLASS:
            for base in self.table.mro(target):
                init = f"{base}.__init__"
                if init in self.table.definitions:
                    return init, Confidence.RESOLVED, ()
            return None, Confidence.UNRESOLVED, ()
        return target, Confidence.RESOLVED, ()
