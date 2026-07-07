"""Behavioral model for mfb-lowpass-2 (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries ``fc_hz``
and ``q`` -- cell.yaml's idiom params that set the R1/R3/R4/C2/C5 bindings.
``c_base_farads`` is accepted for forward-compatibility (same convention as
the other v0 models) but does not otherwise enter this ideal-tier math. The
block's ports match cell.yaml ``ports`` exactly: IN, OUT, REF, VCC, VEE.

Same 2nd-order low-pass state-variable math as sallen-key-lowpass-2 (see
that cell's model docstring for the full derivation and the
"equivalent-transfer-function ideal, not node-by-node" simplification note),
but with the output **inverted about REF** -- the MFB topology's summing
junction is inherently inverting (cell.yaml's disambiguation note), unlike
Sallen-Key's non-inverting unity-gain follower::

    x  = IN - REF
    hp = x - lp - bp/q
    bp += dt * w * hp
    lp += dt * w * bp
    OUT = clip(REF - lp, VEE + margin, VCC - margin)

Only the output tap's sign differs from sallen-key-lowpass-2 (``REF - lp``
instead of ``REF + lp``); the two integrator states (``bp``, ``lp``) and the
frequency response they realize are identical, so both cells characterize the
same ``fc``/``Q`` pair the way their cell.yaml bindings intend.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class MfbLowpass2:
    """2nd-order low-pass (state-variable realization), inverted about REF."""

    inputs = ("IN", "REF", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(
        self,
        fc_hz: float,
        q: float,
        margin: float = DEFAULT_RAIL_MARGIN,
        name: str = "mfb_lowpass_2",
    ):
        self.name = name
        self.fc_hz = float(fc_hz)
        self.q = float(q)
        self.margin = float(margin)
        self.w = 2.0 * math.pi * self.fc_hz
        self.bp = 0.0  # bandpass integrator state
        self.lp = 0.0  # lowpass integrator state (the output tap)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        ref = inputs["REF"]
        x = inputs["IN"] - ref
        hp = x - self.lp - self.bp / self.q
        self.bp += dt * self.w * hp
        self.lp += dt * self.w * self.bp
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(ref - self.lp, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``fc_hz``, ``q``)."""
    return MfbLowpass2(fc_hz=float(params["fc_hz"]), q=float(params["q"]))  # type: ignore[arg-type]
