"""Behavioral model for output-clamp (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block``. ``params`` carries
``r_series_ohms`` (cell.yaml's only idiom param); this ideal-tier model does
not use it -- see the note below -- but accepts it for convention-uniformity
and so a future, more detailed model has the value ready. The block's ports
match cell.yaml ``ports`` exactly: IN, OUT, VCC, GND.

Dual-Schottky clamp, referenced to GND::

    OUT = clip(IN, GND - SCHOTTKY_VF, VCC + SCHOTTKY_VF)

``SCHOTTKY_VF = 0.3 V`` is the forward-drop headroom beyond each rail before a
clamp diode conducts hard (a BAT54-class Schottky, per cell.yaml's archetype
note), matching the topology: D1 clamps OUT to VCC + Vf, D2 clamps OUT to
GND - Vf.

**Simplification, documented honestly:** the series resistor R1 (between IN
and the clamped node) is *transparent* at this ideal-diode abstraction -- it
only matters for the clamp current-limiting it provides during a fault, which
this DC/large-signal ideal-clip model does not represent (no current is
modeled at all, only voltage). ``r_series_ohms`` is read from ``params`` for
forward-compatibility but has no effect on the output in this tier.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Schottky forward-conduction headroom (volts) beyond each rail before the
#: clamp diode holds the node. Fixed by the BAT54-class archetype device
#: (cell.yaml's ``archetype_symbol``), not a cell.yaml idiom param.
SCHOTTKY_VF = 0.3


class OutputClamp:
    """Series-R + dual-Schottky clamp: OUT tracks IN, clamped beyond the rails."""

    inputs = ("IN", "VCC", "GND")
    outputs = ("OUT",)

    def __init__(self, r_series_ohms: float, name: str = "output_clamp"):
        self.name = name
        # Read for forward-compatibility; transparent at this abstraction --
        # see the module docstring's "Simplification" note.
        self.r_series_ohms = float(r_series_ohms)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        lo = inputs["GND"] - SCHOTTKY_VF
        hi = inputs["VCC"] + SCHOTTKY_VF
        return {"OUT": min(max(inputs["IN"], lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``r_series_ohms``)."""
    return OutputClamp(r_series_ohms=float(params["r_series_ohms"]))  # type: ignore[arg-type]
