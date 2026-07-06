"""Behavioral model for opamp-gain-x4-noninverting (sim-gate v0, docs/SIM.md).

Four independent non-inverting gain channels sharing one set of rails, in a
single block — the quad op-amp package modeled as one DUT. Exports
``make_behavior(params) -> Block`` where ``params`` carries ``gain1..gain4``
(resolved idiom params). Ports match cell.yaml exactly: IN1..IN4, OUT1..OUT4,
VCC, VEE, GND.

Each channel k uses the same ideal-with-rails math as the single-channel cell::

    OUT{k} = clip(GND + gain{k}*(IN{k} - GND), VEE + margin, VCC - margin)

Channels are independent (no crosstalk in this ideal tier); the rails are shared.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Shared with the single-channel cell's convention (docs/SIM.md).
DEFAULT_RAIL_MARGIN = 0.1

_CHANNELS = (1, 2, 3, 4)


class QuadNonInvertingOpAmp:
    """Four independent non-inverting channels, shared VCC/VEE/GND rails."""

    inputs = ("IN1", "IN2", "IN3", "IN4", "VCC", "VEE", "GND")
    outputs = ("OUT1", "OUT2", "OUT3", "OUT4")

    def __init__(
        self,
        gains: Mapping[int, float],
        margin: float = DEFAULT_RAIL_MARGIN,
        name: str = "opamp_quad",
    ):
        self.name = name
        self.gains = {k: float(gains[k]) for k in _CHANNELS}
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        lo = inputs["VEE"] + self.margin
        hi = inputs["VCC"] - self.margin
        out: dict[str, float] = {}
        for k in _CHANNELS:
            ideal = gnd + self.gains[k] * (inputs[f"IN{k}"] - gnd)
            out[f"OUT{k}"] = min(max(ideal, lo), hi)
        return out


def make_behavior(params: Mapping[str, object]):
    """Build the quad DUT from resolved idiom params (needs ``gain1..gain4``)."""
    gains = {k: float(params[f"gain{k}"]) for k in _CHANNELS}  # type: ignore[arg-type]
    return QuadNonInvertingOpAmp(gains=gains)
