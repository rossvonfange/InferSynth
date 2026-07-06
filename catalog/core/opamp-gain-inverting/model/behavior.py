"""Behavioral model for opamp-gain-inverting (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` are the resolved
idiom params (post-``bind_cell`` defaults): ``gain`` is the closed-loop gain
**magnitude** (cell.yaml binds ``R1: "gain * rin_ohms"`` — a magnitude, not a
signed value; the inversion is a topology fact, not something ``gain`` encodes).
The block's ports match cell.yaml ``ports`` exactly (IN, OUT, REF, VCC, VEE) —
note ``REF`` replaces ``GND``: the non-inverting input is tied to an explicit
analog reference, not assumed ground (see cell.yaml's manifest note).

Ideal-with-rails inverting op-amp math, referenced to REF::

    OUT = clip(REF - gain*(IN - REF), VEE + margin, VCC - margin)

which is the standard inverting-amp identity ``Vout = Vref - gain*(Vin - Vref)``
(sets Vout = Vref when Vin = Vref, and swings the opposite direction from Vin
around that reference — that's the "inversion"). ``margin`` is the rail
headroom convention shared with the other op-amp cells (``DEFAULT_RAIL_MARGIN``,
docs/SIM.md §6); not a cell.yaml idiom param.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class InvertingOpAmp:
    """One inverting gain channel (gain magnitude) with hard rail clipping."""

    inputs = ("IN", "REF", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(self, gain: float, margin: float = DEFAULT_RAIL_MARGIN, name: str = "opamp_inv"):
        self.name = name
        self.gain = float(gain)
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        ref = inputs["REF"]
        ideal = ref - self.gain * (inputs["IN"] - ref)
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(ideal, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``gain``)."""
    return InvertingOpAmp(gain=float(params["gain"]))  # type: ignore[arg-type]
