"""Behavioral model for power-input-conditioning (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``c_bulk_farads`` (cell.yaml's sole idiom param, binding the bulk cap C1). The
block's ports match cell.yaml ``ports`` exactly: VIN, VOUT, GND.

Topology recap (cell.yaml): F1 (polyfuse, series VIN->VP) then D1 (Schottky,
series VP->VOUT) then C1 (bulk cap, VOUT->GND).

**This model does NOT add an RC bulk-cap lag.** The polyfuse (F1) is modeled
as transparent/no-op at this tier (per the task convention -- it is a
protection element with no steady-state voltage drop or dynamics worth
representing in a DC-ideal voltage-only tier). The bulk cap (C1) only
*smooths* an already-DC (or slow) rail in this cell's intended use — it does
not change the rail's steady-state DC level, which is all `settles_to`-style
DC testbenches at this tier characterize. Adding a fixed-tau RC lag here
would (a) not be derivable from ``c_bulk_farads`` alone without inventing an
unstated series resistance, and (b) not change any DC settle-time assertion
this tier makes since the DC checks already wait for the (trivial, stateless)
steady state. So this tier keeps the model purely algebraic (stateless) and
DC-accurate; ``c_bulk_farads`` is validated by the binder but does not
otherwise enter the behavioral math.

Ideal-with-diode-drop math, referenced to GND::

    vin_rel = VIN - GND
    VOUT = GND + max(0.0, vin_rel - VF_SCHOTTKY_VOLTS)

Reverse polarity (``vin_rel < 0``): the Schottky blocks (open), so
``vin_rel - VF_SCHOTTKY_VOLTS`` is already negative and ``max(0, ...)`` clamps
VOUT to ``GND`` (0 V relative) -- exactly the intended reverse-polarity
protection behavior.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Schottky forward-voltage drop (volts) assumed for D1 at typical load
#: current. Tier convention, not a cell.yaml idiom param.
VF_SCHOTTKY_VOLTS = 0.3


class PowerInputConditioning:
    """Fuse (transparent) + series Schottky drop + bulk cap (DC no-op)."""

    inputs = ("VIN", "GND")
    outputs = ("VOUT",)

    def __init__(self, c_bulk_farads: float, name: str = "power_input_conditioning"):
        self.name = name
        # Validated/carried for completeness (cell.yaml idiom param); this
        # DC-ideal tier's math does not depend on it -- see module docstring.
        self.c_bulk_farads = float(c_bulk_farads)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        vin_rel = inputs["VIN"] - gnd
        vout_rel = max(0.0, vin_rel - VF_SCHOTTKY_VOLTS)
        return {"VOUT": gnd + vout_rel}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``c_bulk_farads``)."""
    return PowerInputConditioning(c_bulk_farads=float(params["c_bulk_farads"]))  # type: ignore[arg-type]
