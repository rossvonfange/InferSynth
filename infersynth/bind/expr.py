"""Restricted arithmetic evaluator for cell binding expressions (BUILD_PLAN WP1).

A binding expression (``cell.yaml`` ``bindings`` section) maps a fragment
reference to an arithmetic expression over the cell's idiom parameters, e.g.
``R1: "(gain - 1) * rg_ohms"``. The grammar is deliberately tiny:

* binary operators ``+ - * / ** // %``, unary ``+ -``, parentheses
* numeric literals (int/float)
* parameter names
* the functions ``min``, ``max``, ``abs``, ``round``

Implemented via :func:`ast.parse` plus a whitelist walker — never ``eval`` or
``exec``. Anything outside the whitelist (attribute access, subscripts,
lambdas, ``__import__``, keyword arguments, strings, ...) raises
:class:`BindingError` naming the offending token.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from infersynth.ir import Param

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["BindingError", "bind_cell", "evaluate", "free_names"]

_FUNCTIONS: dict[str, Any] = {"min": min, "max": max, "abs": abs, "round": round}

_BINOPS: dict[type, Any] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}

_UNARYOPS: dict[type, Any] = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


class BindingError(ValueError):
    """Raised for an invalid binding expression or invalid binding parameters."""


def _parse(expr: str) -> ast.Expression:
    """Parse *expr* and reject any node outside the whitelist."""
    if not isinstance(expr, str) or not expr.strip():
        raise BindingError(f"binding expression must be a non-empty string, got {expr!r}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise BindingError(f"invalid syntax in binding expression {expr!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        _check_node(node, expr)
    return tree


def _check_node(node: ast.AST, expr: str) -> None:
    if isinstance(node, (ast.Expression, ast.Name, ast.Load)):
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise BindingError(f"forbidden name {node.id!r} in binding expression {expr!r}")
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise BindingError(
                f"non-numeric literal {node.value!r} in binding expression {expr!r}"
            )
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise BindingError(
                f"forbidden operator {type(node.op).__name__!r} "
                f"in binding expression {expr!r}"
            )
        return
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARYOPS:
            raise BindingError(
                f"forbidden operator {type(node.op).__name__!r} "
                f"in binding expression {expr!r}"
            )
        return
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS):
            token = ast.unparse(node.func)
            raise BindingError(f"forbidden function {token!r} in binding expression {expr!r}")
        if node.keywords:
            raise BindingError(
                f"keyword arguments are not allowed in binding expression {expr!r}"
            )
        return
    if isinstance(node, _BINOP_NODE_TYPES) or isinstance(node, _UNARYOP_NODE_TYPES):
        return  # operator nodes reachable via ast.walk; validated on their parents
    token = ast.unparse(node) if isinstance(node, ast.expr) else type(node).__name__
    raise BindingError(f"forbidden syntax {token!r} in binding expression {expr!r}")


_BINOP_NODE_TYPES = tuple(_BINOPS)
_UNARYOP_NODE_TYPES = tuple(_UNARYOPS)


def free_names(expr: str) -> set[str]:
    """Return the set of parameter names referenced by *expr*.

    Function names from the builtin whitelist are not free names. Raises
    :class:`BindingError` if the expression does not parse cleanly.
    """
    tree = _parse(expr)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in called
    }


def _eval_node(node: ast.AST, params: Mapping[str, float | int], expr: str) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, params, expr)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            raise BindingError(
                f"function {node.id!r} used as a value in binding expression {expr!r}"
            )
        if node.id not in params:
            raise BindingError(f"unknown parameter {node.id!r} in binding expression {expr!r}")
        return params[node.id]
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, params, expr)
        right = _eval_node(node.right, params, expr)
        try:
            return _BINOPS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise BindingError(f"division by zero in binding expression {expr!r}") from exc
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_eval_node(node.operand, params, expr))
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)  # guaranteed by _parse
        args = [_eval_node(arg, params, expr) for arg in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except TypeError as exc:
            raise BindingError(
                f"bad call to {node.func.id!r} in binding expression {expr!r}: {exc}"
            ) from exc
    raise BindingError(  # pragma: no cover — _parse rejects everything else first
        f"forbidden syntax {type(node).__name__!r} in binding expression {expr!r}"
    )


def evaluate(expr: str, params: Mapping[str, float | int]) -> float:
    """Evaluate binding expression *expr* over *params*; return a float.

    Only whitelisted arithmetic is permitted (module docstring). Unknown
    names, forbidden nodes, and non-numeric parameter values raise
    :class:`BindingError`.
    """
    for name in sorted(params):
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BindingError(
                f"parameter {name!r} must be numeric, got {type(value).__name__} {value!r}"
            )
    return float(_eval_node(_parse(expr), params, expr))


def _idiom_params_to_ir(cell: CellPackage) -> list[Param]:
    """Build :class:`infersynth.ir.Param` objects from a cell's idiom param schemas."""
    out: list[Param] = []
    for pname in sorted(cell.idiom_params):
        schema = cell.idiom_params[pname]
        prange = schema.get("range")
        allowed = schema.get("allowed")
        out.append(
            Param(
                name=pname,
                type=schema.get("type", "float"),
                default=schema.get("default"),
                range=tuple(prange) if prange is not None else None,
                allowed=tuple(allowed) if allowed is not None else None,
            )
        )
    return out


def bind_cell(cell: CellPackage, params: Mapping[str, float | int]) -> dict[str, float]:
    """Compute a cell's binding values: ``{fragment ref: value}``.

    Validates *params* against the cell's idiom parameter constraints first
    (type/range/allowed, defaults merged — :class:`infersynth.ir.Param`
    semantics), then evaluates every ``bindings`` expression over the resolved
    parameters. Raises :class:`BindingError` collecting all constraint
    diagnostics, or on the first failing expression.
    """
    from infersynth.ir import Cell  # local import: keep module import cycle-free

    ir_cell = Cell(name=cell.name, params=_idiom_params_to_ir(cell))
    resolved, diagnostics = ir_cell.resolve_params(params)
    if diagnostics:
        raise BindingError(
            "invalid binding parameters for cell {}:\n{}".format(
                cell.key, "\n".join(f"  - {d}" for d in diagnostics)
            )
        )
    return {ref: evaluate(expr, resolved) for ref, expr in sorted(cell.bindings.items())}
