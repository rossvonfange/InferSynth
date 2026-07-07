"""Behavioral model for sallen-key-lowpass-2 (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries ``fc_hz``
(cutoff) and ``q`` (quality factor) -- cell.yaml's idiom params that set the
R1/R2/C1/C2 bindings. ``c_base_farads`` is accepted for forward-compatibility
(convention shared with other v0 models that read but don't use every idiom
param) but does not otherwise enter this ideal-tier math. The block's ports
match cell.yaml ``ports`` exactly: IN, OUT, REF, VCC, VEE.

2nd-order low-pass via the standard **state-variable filter** form (a
textbook realization of the Sallen-Key transfer function, not a literal
R/C/op-amp-node simulation of this specific topology -- see the
"Simplification" note below), referenced to ``REF`` (the filter's AC-ground
reference, per cell.yaml's manifest note) and integrated with forward-Euler
at the kernel's fixed ``dt``::

    x  = IN - REF
    hp = x - lp - bp/q
    bp += dt * w * hp
    lp += dt * w * bp
    OUT = clip(REF + lp, VEE + margin, VCC - margin)

where ``w = 2*pi*fc_hz`` and ``lp``/``bp`` are integrator states (the
filter's two poles) living on ``self`` -- **stateful**, and the kernel gives
each testbench run a fresh DUT instance (docs/SIM.md §4), so no state leaks
between scenarios.

**Simplification, documented honestly:** this is the canonical 2-integrator
state-variable topology tuned to the same ``fc``/``Q`` as the Sallen-Key
cell.yaml bindings target -- an equivalent-transfer-function ideal, not a
node-by-node simulation of R1/R2/C1/C2 and the specific unity-gain-follower
wiring in fragment.kicad_sch. It shares the op-amp cells' ideal-with-rails
clipping convention (``DEFAULT_RAIL_MARGIN``) and forward-Euler accuracy
caveat (adc-driver-rc's model docstring): accurate as long as
``dt << 1/fc_hz``, which the testbench ensures.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class SallenKeyLowpass2:
    """2nd-order low-pass (state-variable realization) about REF, rail-clipped."""

    inputs = ("IN", "REF", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(
        self,
        fc_hz: float,
        q: float,
        margin: float = DEFAULT_RAIL_MARGIN,
        name: str = "sallen_key_lowpass_2",
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
        return {"OUT": min(max(ref + self.lp, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``fc_hz``, ``q``)."""
    return SallenKeyLowpass2(fc_hz=float(params["fc_hz"]), q=float(params["q"]))  # type: ignore[arg-type]
