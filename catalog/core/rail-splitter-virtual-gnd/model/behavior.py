"""Behavioral model for rail-splitter-virtual-gnd (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block``. ``params`` carries
``r_div_ohms``/``c_filt_farads`` (cell.yaml's idiom params, binding
R1/R2/C1); this ideal-tier model reads them for forward-compatibility (same
convention as ``output-clamp``'s ``r_series_ohms``) but they have no effect
on the output -- see the "Simplification" note below. The block's ports
match cell.yaml ``ports`` exactly: VCC, GND, VGND.

Ideal buffered resistive-divider virtual ground, referenced to GND::

    VGND = clip((VCC + GND) / 2, GND + margin, VCC - margin)

``r_div_ohms`` sets equal top/bottom divider resistors (R1=R2), so the
*ideal* (infinite buffer input impedance, zero buffer output impedance)
divider midpoint is always exactly ``(VCC+GND)/2`` regardless of the
resistor value -- this model characterizes that ideal, buffered midpoint.

**Simplification, documented honestly:** ``c_filt_farads`` (C1, the divider
node's AC bypass cap) is **ignored** at this DC-ideal tier -- C1 only
smooths the pre-buffer divider node against load transients/ripple; it does
not change the buffered output's *steady-state* DC level, which is all this
model's ``settles_to``-style testbench characterizes (same reasoning as
power-input-conditioning's bulk cap C1). The op-amp buffer (U1) is modeled
ideal-with-rails, same ``DEFAULT_RAIL_MARGIN`` convention as the other op-amp
cells -- there is no separate ``VEE`` port here (cell.yaml has none: the
buffer's own negative rail is implicitly ``GND`` in this single-supply
topology), so the low clip bound is ``GND + margin``, not a ``VEE`` port.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Rail headroom (volts) the ideal buffered output stops short of each rail.
#: Convention of the v0 behavioral tier (not a cell.yaml param); see docs/SIM.md.
DEFAULT_RAIL_MARGIN = 0.1


class RailSplitterVirtualGnd:
    """Ideal buffered resistive-divider midpoint, clipped to [GND, VCC]."""

    inputs = ("VCC", "GND")
    outputs = ("VGND",)

    def __init__(
        self,
        r_div_ohms: float,
        c_filt_farads: float,
        margin: float = DEFAULT_RAIL_MARGIN,
        name: str = "rail_splitter_virtual_gnd",
    ):
        self.name = name
        # Validated/carried for completeness (cell.yaml idiom params); this
        # DC-ideal tier's math does not depend on either -- see module docstring.
        self.r_div_ohms = float(r_div_ohms)
        self.c_filt_farads = float(c_filt_farads)
        self.margin = float(margin)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        vcc = inputs["VCC"]
        gnd = inputs["GND"]
        mid = (vcc + gnd) / 2.0
        lo = gnd + self.margin
        hi = vcc - self.margin
        return {"VGND": min(max(mid, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block (needs ``r_div_ohms``, ``c_filt_farads``)."""
    return RailSplitterVirtualGnd(
        r_div_ohms=float(params["r_div_ohms"]),  # type: ignore[arg-type]
        c_filt_farads=float(params["c_filt_farads"]),  # type: ignore[arg-type]
    )
