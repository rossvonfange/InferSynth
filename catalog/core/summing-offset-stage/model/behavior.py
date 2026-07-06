"""Behavioral model for summing-offset-stage (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` are the resolved
idiom params (post-``bind_cell`` defaults): ``w1``/``w2`` are the per-input
summing weights (cell.yaml binds ``R1: "rf_ohms / w1"``, ``R2: "rf_ohms / w2"``,
so ``w_i = Rf/R_i`` — the classic inverting-summer gain-per-input). The block's
ports match cell.yaml ``ports`` exactly (IN1, IN2, OUT, REF, VCC, VEE).

Ideal-with-rails inverting summer, referenced to REF::

    OUT = clip(REF - (w1*(IN1 - REF) + w2*(IN2 - REF)), VEE + margin, VCC - margin)

which reduces to the single-input inverting-amp identity when the unused input
sits at REF (its `(IN - REF)` term drops to zero). ``margin`` is the rail
headroom convention shared with the other op-amp cells (``DEFAULT_RAIL_MARGIN``,
docs/SIM.md §6); not a cell.yaml idiom param.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal output stops short of each rail. Convention
#: of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class SummingOffsetStage:
    """Inverting 2-input summer about REF with hard rail clipping."""

    inputs = ("IN1", "IN2", "REF", "VCC", "VEE")
    outputs = ("OUT",)

    def __init__(
        self,
        w1: float,
        w2: float,
        margin: float = DEFAULT_RAIL_MARGIN,
        name: str = "summing_offset",
    ):
        self.name = name
        self.w1 = float(w1)
        self.w2 = float(w2)
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        ref = inputs["REF"]
        ideal = ref - (self.w1 * (inputs["IN1"] - ref) + self.w2 * (inputs["IN2"] - ref))
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        return {"OUT": min(max(ideal, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``w1``, ``w2``)."""
    return SummingOffsetStage(w1=float(params["w1"]), w2=float(params["w2"]))  # type: ignore[arg-type]
