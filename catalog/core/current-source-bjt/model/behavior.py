"""Behavioral model for current-source-bjt (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``i_out_amps``/``vref_volts`` (cell.yaml's idiom params; ``R1`` is derived from
both, but the behavioral model needs only the *programmed* output current and
the base-drive threshold). The block's ports match cell.yaml ``ports`` exactly:
VREF, OUT, GND.

**IMPORTANT — signal-representation note (read before wiring this cell into
any other testbench):** this v0 sim tier is a *voltage-only* dataflow kernel
(docs/SIM.md §2: every :class:`~infersynth.sim.kernel.Block` port carries a
plain ``float`` sample with no unit tag). This cell's ``OUT`` port is a
**current source in the real circuit**, so there is no honest way to report it
in volts. Rather than silently faking a voltage, this model reports the
``OUT`` *signal value* as the programmed output current **in amps** — i.e. a
downstream consumer of this trace must know (from this docstring / cell.yaml)
that ``OUT`` is not a voltage here. This is a deliberate, documented departure
from the "OUT is a voltage" assumption every other v0 cell makes; do not copy
this pattern to a cell whose OUT is actually a voltage node.

Behavior (transistor on/off idealization, no active current regulation
dynamics modeled at this tier)::

    OUT = i_out_amps   if VREF - GND >= VBE_ON_VOLTS
        = 0.0          otherwise (insufficient base drive; BJT is off)

``VBE_ON_VOLTS = 0.75 V`` is the assumed silicon base-emitter turn-on
threshold (a first-order idealization -- the resistor-derived bias math in
cell.yaml's ``bindings`` uses 0.65 V as the *nominal* Vbe drop in the *on*
region; 0.75 V here is deliberately a bit higher, modeling "insufficient base
drive to reliably turn on and sustain the programmed current," not the
in-conduction Vbe itself).
"""

from __future__ import annotations

from collections.abc import Mapping

#: Assumed base-emitter turn-on threshold (volts) below which the BJT is
#: considered off (insufficient base drive). Tier convention, not a cell.yaml
#: idiom param.
VBE_ON_VOLTS = 0.75


class BjtCurrentSource:
    """NPN emitter-degeneration current source, on/off idealized.

    ``OUT`` reports the programmed current in **amps**, not volts -- see the
    module docstring.
    """

    inputs = ("VREF", "GND")
    outputs = ("OUT",)

    def __init__(self, i_out_amps: float, name: str = "current_source_bjt"):
        self.name = name
        self.i_out_amps = float(i_out_amps)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        vref_rel = inputs["VREF"] - inputs["GND"]
        out = self.i_out_amps if vref_rel >= VBE_ON_VOLTS else 0.0
        return {"OUT": out}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``i_out_amps``)."""
    return BjtCurrentSource(i_out_amps=float(params["i_out_amps"]))  # type: ignore[arg-type]
