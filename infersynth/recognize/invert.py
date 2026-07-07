"""STEP 3 — parameter inversion: the binding evaluator run in reverse.

The forward binder (:mod:`infersynth.bind.expr`) computes component values from
idiom params: ``R1 = (gain - 1) * rg_ohms``. Recognition inverts it: given the
matched components' *observed* values, recover the params that reproduce them.

Method (deterministic, exact-where-possible):

* **Identity bindings.** A binding whose expression is exactly one param name
  (``R2: rg_ohms``) recovers that param directly from the observed value.
* **Single-unknown resolve.** With other params known, an equation ``expr(p)
  = observed`` in one remaining unknown ``p`` is solved for ``p``:
  - *exact linear* when ``expr`` is affine in ``p`` (verified with three sample
    points): ``p = (observed - b) / m``;
  - *numeric bisection* otherwise — bracket a sign change of ``f(p) =
    expr(p) - observed`` on a fixed grid over ``p``'s declared range, then
    bisect to a tight tolerance. Deterministic (fixed grid, fixed iteration
    count); the first bracket in ascending order wins.
* Iterate to a fixed point (each newly-resolved param may unlock others).
* A param no equation constrains falls back to its declared default, flagged
  ``assumed_default``.

The result carries the recovered params, a **residual** (the max relative
error when the recovered params are pushed forward through every binding), and
a **confidence** derived from it, plus any unresolved-param flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from infersynth.bind.expr import BindingError, evaluate, free_names

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["InversionResult", "invert_params"]

_GRID = 512  # sample points for bisection bracketing
_BISECT_ITERS = 80  # bisection refinement steps (>= 2^-80 precision on the range)
_LINEAR_TOL = 1e-9  # affine-check relative tolerance


@dataclass(frozen=True)
class InversionResult:
    """Recovered params for a matched cell instance.

    ``params`` maps idiom param name -> recovered value. ``assumed_default`` is
    the sorted params that no observation constrained (fell back to default, or
    ``None`` when no default exists). ``residual`` is the max relative binding
    error under the recovered params; ``confidence`` in [0, 1] derived from it.
    ``method`` maps each resolved param -> how it was recovered
    (``identity`` | ``linear`` | ``bisection`` | ``default``).
    """

    params: dict[str, float]
    residual: float
    confidence: float
    assumed_default: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    method: dict[str, str] = field(default_factory=dict)


def _range_of(cell: CellPackage, pname: str) -> tuple[float, float] | None:
    schema = cell.idiom_params.get(pname, {})
    rng = schema.get("range")
    if rng and len(rng) == 2:
        return float(rng[0]), float(rng[1])
    return None


def _default_of(cell: CellPackage, pname: str) -> float | None:
    d = cell.idiom_params.get(pname, {}).get("default")
    return float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else None


def _safe_eval(expr: str, params: dict[str, float]) -> float | None:
    try:
        return evaluate(expr, params)
    except (BindingError, ZeroDivisionError, ValueError, OverflowError):
        return None


def _solve_scalar(
    expr: str, pname: str, fixed: dict[str, float], target: float, rng: tuple[float, float]
) -> tuple[float, str] | None:
    """Solve ``expr(pname) = target`` for *pname* over *rng*, other params
    *fixed*. Returns ``(value, method)`` or ``None``."""
    lo, hi = rng
    if hi <= lo:
        return None
    span = hi - lo

    def f(x: float) -> float | None:
        v = _safe_eval(expr, {**fixed, pname: x})
        return None if v is None else v - target

    # --- exact linear (affine) attempt on three interior points ---
    x1, x2, x3 = lo + 0.25 * span, lo + 0.5 * span, lo + 0.75 * span
    g1, g2, g3 = f(x1), f(x2), f(x3)
    if g1 is not None and g2 is not None and g3 is not None:
        m = (g3 - g1) / (x3 - x1)
        if abs(m) > 0.0:
            # affine iff the midpoint lies on the chord within tolerance
            g2_pred = g1 + m * (x2 - x1)
            scale = max(abs(g2), abs(g1), abs(g3), 1.0)
            if abs(g2 - g2_pred) <= _LINEAR_TOL * scale:
                root = x1 - g1 / m
                if lo - 1e-9 * span <= root <= hi + 1e-9 * span:
                    return min(max(root, lo), hi), "linear"

    # --- numeric bisection: bracket a sign change on a fixed grid ---
    prev_x = lo
    prev_v = f(lo)
    for i in range(1, _GRID + 1):
        x = lo + span * i / _GRID
        v = f(x)
        if v is None:
            prev_x, prev_v = x, v
            continue
        if v == 0.0:
            return x, "bisection"
        if prev_v is not None and (prev_v < 0.0) != (v < 0.0):
            a, b, fa = prev_x, x, prev_v
            for _ in range(_BISECT_ITERS):
                mid = 0.5 * (a + b)
                fm = f(mid)
                if fm is None or fm == 0.0:
                    return mid, "bisection"
                if (fa < 0.0) != (fm < 0.0):
                    b = mid
                else:
                    a, fa = mid, fm
            return 0.5 * (a + b), "bisection"
        prev_x, prev_v = x, v
    return None


def _residual(cell: CellPackage, observed: dict[str, float], params: dict[str, float]) -> float:
    """Max relative error pushing *params* forward through the bindings whose
    ref was observed."""
    worst = 0.0
    for ref, expr in cell.bindings.items():
        if ref not in observed:
            continue
        pred = _safe_eval(expr, params)
        if pred is None:
            return float("inf")
        obs = observed[ref]
        denom = max(abs(obs), 1e-30)
        worst = max(worst, abs(pred - obs) / denom)
    return worst


def invert_params(cell: CellPackage, observed: dict[str, float]) -> InversionResult:
    """Recover *cell*'s idiom params from *observed* (golden ref -> value).

    *observed* is keyed by GOLDEN ref (``R1``, ``C2`` …): the caller has
    already translated matched design refs back through ``phi``.
    """
    param_names = sorted(cell.idiom_params)
    # equations: (golden_ref, expr, observed_value, free param names)
    equations: list[tuple[str, str, float, frozenset[str]]] = []
    for ref, expr in sorted(cell.bindings.items()):
        if ref not in observed:
            continue
        try:
            fn = frozenset(free_names(expr)) & set(param_names)
        except BindingError:
            continue
        equations.append((ref, expr, observed[ref], fn))

    resolved: dict[str, float] = {}
    method: dict[str, str] = {}

    # pass 1 — identity bindings (expr is exactly one param name)
    for _ref, expr, obs, fn in equations:
        if len(fn) == 1:
            (only,) = tuple(fn)
            if expr.strip() == only and only not in resolved:
                resolved[only] = obs
                method[only] = "identity"

    # pass 2 — iterate single-unknown solves to a fixed point
    progressed = True
    while progressed:
        progressed = False
        for pname in param_names:
            if pname in resolved:
                continue
            for _ref, expr, obs, fn in equations:
                if pname not in fn:
                    continue
                if not (fn - {pname}) <= resolved.keys():
                    continue
                rng = _range_of(cell, pname)
                if rng is None:
                    continue
                fixed = {k: resolved[k] for k in fn if k != pname}
                sol = _solve_scalar(expr, pname, fixed, obs, rng)
                if sol is not None:
                    resolved[pname], method[pname] = sol
                    progressed = True
                    break

    # unresolved params -> default fallback (flagged)
    assumed: list[str] = []
    unresolved: list[str] = []
    for pname in param_names:
        if pname in resolved:
            continue
        d = _default_of(cell, pname)
        if d is not None:
            resolved[pname] = d
            method[pname] = "default"
            assumed.append(pname)
        else:
            unresolved.append(pname)

    residual = _residual(cell, observed, resolved) if not unresolved else float("inf")
    confidence = 0.0 if residual == float("inf") else 1.0 / (1.0 + residual)
    return InversionResult(
        params={k: resolved[k] for k in param_names if k in resolved},
        residual=residual,
        confidence=confidence,
        assumed_default=tuple(sorted(assumed)),
        unresolved=tuple(sorted(unresolved)),
        method=method,
    )
