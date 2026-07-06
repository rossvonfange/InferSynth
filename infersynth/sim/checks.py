"""Named assertions over recorded signal traces (testbench CHECKS).

A :class:`Check` is a name plus a pure predicate over the ``{signal: [samples]}``
traces a :class:`~infersynth.sim.kernel.Simulation` returns. ``evaluate``
returns ``(ok, detail)`` where ``detail`` always reports the measured numbers —
so a gate diagnostic is informative whether the check passed or failed.

The three helpers requested by the sim-gate v0 spec:

* :func:`amplitude_ratio` — measured peak-to-peak gain of ``out`` over ``in``.
* :func:`clipped_within` — every sample of a signal lies within ``[lo, hi]``.
* :func:`settles_to` — a signal holds ``value`` (±tol) after ``after_step``.

Plus, added for the inverting-topology cells:

* :func:`inverted` — ``out`` is phase-inverted (anti-correlated) w.r.t. ``in``.

All arithmetic is plain ``float`` math; no ``random``/``time`` (SELECTION.md §8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "Check",
    "amplitude_ratio",
    "clipped_within",
    "inverted",
    "settles_to",
]

Traces = Mapping[str, Sequence[float]]


@dataclass(frozen=True)
class Check:
    """A named predicate over traces. ``fn(traces) -> (ok, detail)``."""

    name: str
    fn: Callable[[Traces], tuple[bool, str]]

    def evaluate(self, traces: Traces) -> tuple[bool, str]:
        return self.fn(traces)


def _require(traces: Traces, *names: str) -> str | None:
    """Return a detail string naming the first missing signal, else None."""
    for n in names:
        if n not in traces:
            return f"signal {n!r} not in traces (have {sorted(traces)})"
    return None


def _peak_to_peak(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return max(samples) - min(samples)


def amplitude_ratio(
    in_sig: str, out_sig: str, expected: float, tol_pct: float
) -> Check:
    """Assert peak-to-peak(``out_sig``) / peak-to-peak(``in_sig``) ≈ ``expected``.

    The measured ratio must be within ``tol_pct`` percent of ``expected``. A
    flat input (zero peak-to-peak) fails with an explicit message rather than
    dividing by zero.
    """

    def fn(traces: Traces) -> tuple[bool, str]:
        miss = _require(traces, in_sig, out_sig)
        if miss:
            return False, miss
        pp_in = _peak_to_peak(traces[in_sig])
        pp_out = _peak_to_peak(traces[out_sig])
        if pp_in == 0.0:
            return False, f"input {in_sig!r} is flat (peak-to-peak 0); cannot measure gain"
        measured = pp_out / pp_in
        tol = abs(expected) * tol_pct / 100.0
        ok = abs(measured - expected) <= tol
        return ok, (
            f"measured gain {measured:.6g} (Vpp_out={pp_out:.6g}, Vpp_in={pp_in:.6g}) "
            f"vs expected {expected:.6g} ±{tol_pct:g}% (±{tol:.6g})"
        )

    return Check(name=f"amplitude_ratio({in_sig}->{out_sig})", fn=fn)


def clipped_within(sig: str, lo: float, hi: float, eps: float = 1e-9) -> Check:
    """Assert every sample of ``sig`` lies within ``[lo - eps, hi + eps]``."""

    def fn(traces: Traces) -> tuple[bool, str]:
        miss = _require(traces, sig)
        if miss:
            return False, miss
        samples = traces[sig]
        if not samples:
            return False, f"signal {sig!r} has no samples"
        smin, smax = min(samples), max(samples)
        ok = smin >= lo - eps and smax <= hi + eps
        return ok, (
            f"{sig!r} range [{smin:.6g}, {smax:.6g}] vs rails [{lo:.6g}, {hi:.6g}]"
        )

    return Check(name=f"clipped_within({sig})", fn=fn)


def settles_to(sig: str, value: float, tol: float, after_step: int) -> Check:
    """Assert ``sig`` holds ``value`` (±``tol``) for every sample at index >= ``after_step``."""

    def fn(traces: Traces) -> tuple[bool, str]:
        miss = _require(traces, sig)
        if miss:
            return False, miss
        tail = traces[sig][after_step:]
        if not tail:
            return False, (
                f"signal {sig!r} has no samples after step {after_step} "
                f"(len {len(traces[sig])})"
            )
        worst = max(tail, key=lambda v: abs(v - value))
        ok = abs(worst - value) <= tol
        return ok, (
            f"{sig!r} after step {after_step}: worst {worst:.6g} vs target "
            f"{value:.6g} ±{tol:.6g}"
        )

    return Check(name=f"settles_to({sig})", fn=fn)


def inverted(in_sig: str, out_sig: str, max_correlation: float = -0.9) -> Check:
    """Assert ``out_sig`` is phase-inverted (anti-correlated) w.r.t. ``in_sig``.

    Computes the Pearson correlation coefficient between the two mean-centered
    traces (a whole-trace, deterministic sign/covariance measure — not a
    per-sample sign flip, which would be fragile around zero-crossings and
    rail-clipped flats). The check passes when the correlation is at or below
    ``max_correlation`` (a negative number close to -1.0): the two signals
    consistently move in opposite directions, which is what "inverted about a
    reference" means for a periodic stimulus such as a sine sweep.

    A flat (zero-variance) trace on either side fails explicitly rather than
    dividing by zero.
    """

    def fn(traces: Traces) -> tuple[bool, str]:
        miss = _require(traces, in_sig, out_sig)
        if miss:
            return False, miss
        xs = traces[in_sig]
        ys = traces[out_sig]
        if len(xs) != len(ys) or not xs:
            return False, f"{in_sig!r} and {out_sig!r} traces have mismatched/zero length"
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        dx = [x - mean_x for x in xs]
        dy = [y - mean_y for y in ys]
        cov = sum(a * b for a, b in zip(dx, dy, strict=True))
        var_x = sum(a * a for a in dx)
        var_y = sum(b * b for b in dy)
        if var_x == 0.0 or var_y == 0.0:
            return False, (
                f"{in_sig!r} or {out_sig!r} is flat (zero variance); cannot measure correlation"
            )
        correlation = cov / (var_x * var_y) ** 0.5
        ok = correlation <= max_correlation
        return ok, (
            f"correlation({in_sig}, {out_sig}) = {correlation:.6g} "
            f"vs required <= {max_correlation:g}"
        )

    return Check(name=f"inverted({in_sig}->{out_sig})", fn=fn)
