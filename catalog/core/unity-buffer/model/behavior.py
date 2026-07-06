"""Behavioral model for unity-buffer (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block``. This cell has no idiom params (a
unity-gain follower has no gain-setting resistors to compute); ``params`` is
accepted for convention-uniformity but unused. The block's ports match
cell.yaml ``ports`` exactly: IN, OUT, VCC, VEE — note there is **no GND port**
here (unlike the gain cells), matching cell.yaml, so the ideal output is
referenced to 0.0 V (the follower has no ground-reference pin to read from).

Ideal-with-rails voltage follower::

    OUT = clip(IN, VEE + margin, VCC - margin)

``margin`` is the rail headroom convention shared with the other op-amp cells
(``DEFAULT_RAIL_MARGIN``, docs/SIM.md §6); not a cell.yaml idiom param.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class UnityBuffer:
    """A unity-gain voltage follower with hard rail clipping."""

    inputs = ("IN", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(self, margin: float = DEFAULT_RAIL_MARGIN, name: str = "unity_buffer"):
        self.name = name
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(inputs["IN"], lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block. This cell has no idiom params; ``params`` unused."""
    return UnityBuffer()
