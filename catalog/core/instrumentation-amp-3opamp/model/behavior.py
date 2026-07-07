"""Behavioral model for instrumentation-amp-3opamp (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` are the resolved
idiom params (post-``bind_cell`` defaults): ``gain`` is the overall
differential gain (cell.yaml: ``gain = 1 + 2*Rf/Rg`` for the input stage,
unity diff stage -- the classic 3-op-amp in-amp closed-loop gain). The
block's ports match cell.yaml ``ports`` exactly (INP, INN, OUT, REF, VCC,
VEE).

Ideal-with-rails 3-op-amp instrumentation amplifier, referenced to REF::

    OUT = clip(REF + gain*(INP - INN), VEE + margin, VCC - margin)

which is the textbook 3-op-amp in-amp transfer function (AoE §5.15-class):
the input stage buffers/differences INP-INN with gain, the unity-gain diff
stage subtracts and re-references to REF. ``margin`` is the rail headroom
convention shared with the other op-amp cells (``DEFAULT_RAIL_MARGIN``,
docs/SIM.md §6); not a cell.yaml idiom param.

**Simplification, documented honestly:** this collapses the two internal
stages (input gain stage + unity-gain diff stage) into one closed-form
expression rather than modeling each op-amp node individually -- an
equivalent-transfer-function ideal, same convention as the other multi-stage
cells in this catalog (summing-offset-stage). Internal node voltages (M1/M2
in cell.yaml's topology note) are not observable in this model.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class InstrumentationAmp3OpAmp:
    """3-op-amp instrumentation amplifier, referenced to REF, rail-clipped."""

    inputs = ("INP", "INN", "REF", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(self, gain: float, margin: float = DEFAULT_RAIL_MARGIN, name: str = "in_amp"):
        self.name = name
        self.gain = float(gain)
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        ref = inputs["REF"]
        ideal = ref + self.gain * (inputs["INP"] - inputs["INN"])
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(ideal, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``gain``)."""
    return InstrumentationAmp3OpAmp(gain=float(params["gain"]))  # type: ignore[arg-type]
