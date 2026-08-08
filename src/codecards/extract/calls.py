"""Pass 2 - resolve every call site against the completed symbol table.

Each call gets a confidence tier. Nothing is silently guessed: an attribute
call the resolver cannot pin down is reported as inferred, ambiguous or
unresolved rather than being drawn as if it were certain.
"""

from __future__ import annotations

import ast
import builtins
import sys
from dataclasses import dataclass
from typing import Iterator

from ..graph.model import CallSite, Confidence, Edge, NodeKind
from .discovery import SkippedFile
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
    reassigned: set[str]  # names rebound in this function's own body (not parameters)


def resolve_calls(
    table: SymbolTable, skipped: list[SkippedFile] | None = None
) -> list[ResolvedCall]:
    """Resolve every call site in table.

    If skipped is given, a function whose own expression tree is too deep to
    walk without exceeding Python's recursion limit is recorded there (path
    of its module, reason naming the function) instead of silently vanishing
    with no trace anywhere in the public API. Everything else in that module
    still resolves normally - only the pathological function is skipped.
    """
    resolver = _Resolver(table, skipped)
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


#: ast.TryStar only exists on 3.11+; guard so this module still imports on the 3.10
#: floor. Branching on sys.version_info (rather than hasattr) also lets pyright
#: statically narrow isinstance(node, _TRY_NODES) to the right node type below.
if sys.version_info >= (3, 11):
    _TRY_NODES = (ast.Try, ast.TryStar)
else:
    _TRY_NODES = (ast.Try,)
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _iter_calls(
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
    if isinstance(node, (*_FUNCTION_NODES, ast.ClassDef)):
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
        if isinstance(child, (*_FUNCTION_NODES, ast.ClassDef)):
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
    if isinstance(node, (*_FUNCTION_NODES, ast.ClassDef)):
        return  # a separate caller - not this function's business
    if isinstance(node, ast.stmt):
        yield from _iter_stmt(node, conditional, loop)
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_any(child, conditional, loop)


def _iter_own_scope(body: list[ast.stmt]) -> Iterator[ast.AST]:
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
        if isinstance(current, (*_FUNCTION_NODES, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


class _Resolver:
    def __init__(self, table: SymbolTable, skipped: list[SkippedFile] | None = None) -> None:
        self.table = table
        self.calls: list[ResolvedCall] = []
        self.skipped = skipped

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
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        module_id: str,
        caller: str,
        class_qualname: str | None,
    ) -> None:
        context = _Context(
            caller=caller,
            module_id=module_id,
            class_qualname=class_qualname,
            names={},
            types={},
            shadowed=set(),
            reassigned=set(),
        )
        self._bind_locals(node, context)
        # Buffered locally, not appended straight to self.calls: if this
        # function's expression tree is too deep and blows the recursion
        # limit partway through, we want all-or-nothing for this one
        # function rather than a half-recorded set of its calls.
        found: list[ResolvedCall] = []
        try:
            for call, in_conditional, in_loop in _iter_calls(node.body):
                site = CallSite(
                    line=call.lineno, in_conditional=in_conditional, in_loop=in_loop
                )
                target, confidence, candidates = self._resolve(call.func, context)
                found.append(
                    ResolvedCall(
                        caller=caller,
                        target=target,
                        confidence=confidence,
                        site=site,
                        candidates=candidates,
                    )
                )
        except RecursionError:
            # This one function's expression tree is too deep to walk, but
            # pass 1 already accepted the file - it must not vanish silently.
            # Only this function is lost; every other function in the module,
            # and every other module, still resolves normally.
            if self.skipped is not None:
                self.skipped.append(
                    SkippedFile(
                        path=str(self.table.modules[module_id].path),
                        reason=f"recursion limit exceeded during call resolution in {caller}",
                    )
                )
            return
        self.calls.extend(found)

    def _bind_locals(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, context: _Context
    ) -> None:
        args = node.args
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            every.append(args.vararg)
        if args.kwarg:
            every.append(args.kwarg)
        for arg in every:
            context.shadowed.add(arg.arg)

        # Nested defs directly in this function's own body are known callables -
        # calls to them resolve through context.names, not through module scope.
        top_level_defs = {id(inner) for inner in node.body if isinstance(inner, _FUNCTION_NODES)}
        for inner in node.body:
            if isinstance(inner, _FUNCTION_NODES):
                context.names[inner.name] = f"{context.caller}.{inner.name}"

        # Annotated parameters: `def go(h: H)` tells us the type of `h` before
        # the body runs at all, so this is set ahead of the body walk below
        # rather than interleaved with it - a parameter's binding always
        # precedes every statement in its own body in source order.
        for arg in every:
            if arg.annotation is not None:
                class_qualname = self._resolve_annotation(arg.annotation, context)
                if class_qualname:
                    context.types[arg.arg] = class_qualname

        # Everything else that binds a name anywhere in this function's own
        # scope (not crossing into a nested def/class) shadows module scope:
        # assignment, augmented/annotated assignment, for-targets, walrus,
        # `with ... as name`, comprehension targets (all ast.Name/Store or
        # ast.Name/Del), `except ... as name` (a plain str, not a Name node),
        # and any def/class that is not directly at this body's top level
        # (e.g. one declared inside an `if`) - pass 1 only records top-level
        # definitions, so there is no correct qualname to point at and the
        # call must degrade to unresolved rather than assert a wrong target.
        #
        # The Name/ExceptHandler branches also record into context.reassigned:
        # a genuine local rebinding of the identifier's value (as opposed to a
        # nested def/class merely occupying the name), which matters for
        # self/cls - those are always parameters (always in context.shadowed
        # for that reason alone) but only sometimes actually reassigned to
        # something other than the instance/class the method was called on.
        #
        # context.types is threaded through this same walk (not a separate
        # pass) because it is order-sensitive: the last binding of a name,
        # in source order, decides its type. Only two statement shapes are
        # inferable - an Assign with a constructor call on the right, or an
        # AnnAssign with a resolvable class annotation - and those branches
        # mark their target Name node in `inferred_targets` so the generic
        # Name/Store branch below (reached moments later, since it is that
        # same target node) does not immediately undo what was just
        # computed. Every OTHER way of rebinding a name that reaches the
        # generic Name/Store branch - augmented assignment, walrus, a
        # for-target, a with-as target, tuple/starred unpacking, del - is
        # not in `inferred_targets`, so it unconditionally drops any type
        # inferred for that name. Failing closed here is deliberate: an
        # unresolved call costs nothing, a confidently wrong one costs
        # trust in every edge on the graph.
        inferred_targets: set[int] = set()
        for descendant in _iter_own_scope(node.body):
            if isinstance(descendant, ast.Name) and isinstance(descendant.ctx, (ast.Store, ast.Del)):
                context.shadowed.add(descendant.id)
                context.reassigned.add(descendant.id)
                if id(descendant) not in inferred_targets:
                    context.types.pop(descendant.id, None)
            elif isinstance(descendant, ast.ExceptHandler) and descendant.name:
                context.shadowed.add(descendant.name)
                context.reassigned.add(descendant.name)
                context.types.pop(descendant.name, None)
            elif (
                isinstance(descendant, (*_FUNCTION_NODES, ast.ClassDef))
                and id(descendant) not in top_level_defs
            ):
                context.shadowed.add(descendant.name)
            elif isinstance(descendant, ast.AnnAssign) and isinstance(descendant.target, ast.Name):
                class_qualname = self._resolve_annotation(descendant.annotation, context)
                inferred_targets.add(id(descendant.target))
                if class_qualname:
                    context.types[descendant.target.id] = class_qualname
                else:
                    context.types.pop(descendant.target.id, None)
            elif isinstance(descendant, ast.Assign):
                class_qualname = self._constructed_class(descendant.value, context)
                for target in descendant.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    inferred_targets.add(id(target))
                    if class_qualname:
                        context.types[target.id] = class_qualname
                    else:
                        context.types.pop(target.id, None)

    def _resolve_annotation(self, node: ast.expr, context: _Context) -> str | None:
        """Map an annotation expression onto a class qualname, if it names one."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        dotted = self._dotted_receiver(node)
        if dotted is None:
            return None
        return self._as_class(dotted, context)

    def _constructed_class(self, node: ast.expr, context: _Context) -> str | None:
        """`H()` on the right-hand side of an assignment tells us the type."""
        if not isinstance(node, ast.Call):
            return None
        dotted = self._dotted_receiver(node.func)
        if dotted is None:
            return None
        return self._as_class(dotted, context)

    def _as_class(self, dotted: str, context: _Context) -> str | None:
        aliases = self.table.modules[context.module_id].aliases
        head, _, rest = dotted.partition(".")
        candidates = []
        if head in aliases:
            base = aliases[head]
            candidates.append(f"{base}.{rest}" if rest else base)
        candidates.append(f"{context.module_id}.{dotted}")
        candidates.append(dotted)
        for candidate in candidates:
            definition = self.table.definitions.get(candidate)
            if definition is not None and definition.kind is NodeKind.CLASS:
                return candidate
        return None

    # -- resolution strategies -------------------------------------------

    def _resolve(
        self, func: ast.expr, context: _Context
    ) -> tuple[str | None, Confidence, tuple[str, ...]]:
        if isinstance(func, ast.Name):
            return self._resolve_name(func.id, context)
        if isinstance(func, ast.Attribute):
            return self._resolve_attribute(func, context)
        return None, Confidence.UNRESOLVED, ()

    def _resolve_attribute(
        self, node: ast.Attribute, context: _Context
    ) -> tuple[str | None, Confidence, tuple[str, ...]]:
        attribute = node.attr

        # super().method() - bare super() only. super(B, self).method() needs
        # the runtime MRO of type(self) to resolve correctly (it starts the
        # search AFTER B, not at the current class), which static analysis
        # does not have; guessing would draw a confident edge to the wrong
        # class, so any argument at all keeps this UNRESOLVED. Also refuse if
        # "super" has been locally rebound - it would no longer name the
        # builtin.
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "super"
            and not node.value.args
            and not node.value.keywords
            and "super" not in context.shadowed
            and "super" not in context.names
            and context.class_qualname
        ):
            for base in self.table.mro(context.class_qualname)[1:]:
                candidate = f"{base}.{attribute}"
                if candidate in self.table.definitions:
                    return self._finalise(candidate)
            return None, Confidence.UNRESOLVED, ()

        # self.method() / cls.method() - refuse if self/cls has been
        # reassigned in this function's own body (legal, if rare): once
        # reassigned it no longer names the instance/class the method was
        # called on, so resolving through the class MRO would be a guess.
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
            and node.value.id not in context.reassigned
            and context.class_qualname
        ):
            for base in self.table.mro(context.class_qualname):
                candidate = f"{base}.{attribute}"
                if candidate in self.table.definitions:
                    return self._finalise(candidate)
            return None, Confidence.UNRESOLVED, ()

        # A receiver whose class we know from an annotation or a constructor.
        if isinstance(node.value, ast.Name) and node.value.id in context.types:
            owner = context.types[node.value.id]
            for base in self.table.mro(owner):
                candidate = f"{base}.{attribute}"
                if candidate in self.table.definitions:
                    return self._finalise(candidate)
            return None, Confidence.UNRESOLVED, ()

        # module.function() - the receiver is a dotted path we can name
        receiver = self._dotted_receiver(node.value)
        if receiver is not None:
            resolved_receiver = self._resolve_receiver(receiver, context)
            if resolved_receiver is not None:
                candidate = f"{resolved_receiver}.{attribute}"
                if candidate in self.table.definitions:
                    return self._finalise(candidate)
                if not self.table.is_internal(candidate):
                    return candidate, Confidence.EXTERNAL, ()

        return None, Confidence.UNRESOLVED, ()

    @staticmethod
    def _dotted_receiver(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            head = _Resolver._dotted_receiver(node.value)
            return f"{head}.{node.attr}" if head else None
        return None

    def _resolve_receiver(self, receiver: str, context: _Context) -> str | None:
        """Map a dotted receiver expression onto a module or package id."""
        head, _, rest = receiver.partition(".")
        # A locally bound name always beats the module alias map - including
        # a nested def/class, which context.names tracks separately from
        # context.shadowed precisely so a bare call to it still resolves
        # (through context.names, not here). A receiver head that names one
        # is not that nested callable's return value, it IS the callable
        # itself, so `.attr()` on it can never be the aliased module's
        # attribute.
        if head in context.shadowed or head in context.names:
            return None
        aliases = self.table.modules[context.module_id].aliases
        if head in aliases:
            base = aliases[head]
            return f"{base}.{rest}" if rest else base
        sibling = f"{context.module_id}.{receiver}"
        if sibling in self.table.modules or sibling in self.table.definitions:
            return sibling
        if receiver in self.table.modules or receiver in self.table.packages:
            return receiver
        return None

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
