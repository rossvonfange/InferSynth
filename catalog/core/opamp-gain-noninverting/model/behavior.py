"""Behavioral model for opamp-gain-noninverting (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` are the resolved
idiom params (post-``bind_cell`` defaults): ``gain`` sets the closed-loop gain.
The block's ports match cell.yaml ``ports`` exactly (IN, OUT, VCC, VEE, GND).

Ideal-with-rails op-amp math, referenced to GND::

    OUT = clip(GND + gain*(IN - GND), VEE + margin, VCC - margin)

``margin`` is the rail headroom an op-amp output cannot swing into; it is not a
cell.yaml idiom param, so this model fixes it at the tier convention
``DEFAULT_RAIL_MARGIN`` (documented in docs/SIM.md).
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class NonInvertingOpAmp:
    """One non-inverting gain channel with hard rail clipping."""

    inputs = ("IN", "VCC", "VEE", "GND")
    outputs = ("OUT",)

    def __init__(self, gain: float, margin: float = DEFAULT_RAIL_MARGIN, name: str = "opamp"):
        self.name = name
        self.gain = float(gain)
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        ideal = gnd + self.gain * (inputs["IN"] - gnd)
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(ideal, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``gain``)."""
    return NonInvertingOpAmp(gain=float(params["gain"]))  # type: ignore[arg-type]
