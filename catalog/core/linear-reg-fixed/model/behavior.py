"""Behavioral model for linear-reg-fixed (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block``. ``params`` carries
``c_in_farads``/``c_out_farads`` (cell.yaml's idiom params, binding the bypass
caps C1/C2); this ideal-tier model reads them for forward-compatibility
(``output-clamp``'s ``r_series_ohms`` convention) but they have no effect on
the output -- bypass caps don't change a regulator's DC operating point. The
block's ports match cell.yaml ``ports`` exactly: VIN, VOUT, GND.

7805-class fixed 5 V linear regulator, referenced to GND, DC-ideal (no
dropout-voltage curve, no load-current limiting -- just the two operating
regions a 7805 datasheet defines)::

    vin_rel = VIN - GND
    VOUT = GND + 5.0                          if vin_rel >= 7.0   (regulating)
         = GND + max(0.0, vin_rel - 2.0)       otherwise           (dropout)

``7.0 V`` is the minimum input-to-GND the archetype 7805 needs to hold its
5 V output (datasheet ``Vin,min`` for a 5 V fixed part -- a ~2 V dropout
margin above the 5 V rail). Below that, the device can no longer regulate;

**Simplification, documented honestly:** below the 7 V threshold this model
does *not* claim to reproduce the pass transistor's actual dropout curve
(which is load- and temperature-dependent and nonlinear near cutoff). It uses
the same constant ~2 V dropout offset (``vin_rel - 2.0``, floored at 0) as a
simple, deterministic stand-in so the cell has *some* well-defined "brownout"
behavior below the regulation threshold rather than an unmodeled discontinuity
-- callers needing accurate dropout behavior should treat only the regulated
region (``vin_rel >= 7.0``) as characterized. ``VOUT`` is clamped at ``GND``
so it never reports a negative/no-supply voltage.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Minimum VIN-GND (volts) the 7805-class archetype needs to hold 5.0 V out.
REGULATION_THRESHOLD_VOLTS = 7.0
#: Regulated fixed output (volts).
VOUT_NOMINAL = 5.0
#: Dropout-region offset (volts) below the regulation threshold -- see the
#: module docstring's "Simplification" note.
DROPOUT_OFFSET_VOLTS = 2.0


class LinearRegFixed:
    """7805-class fixed 5 V regulator: regulated above threshold, dropout below."""

    inputs = ("VIN", "GND")
    outputs = ("VOUT",)

    def __init__(self, c_in_farads: float, c_out_farads: float, name: str = "linear_reg_fixed"):
        self.name = name
        # Validated/carried for completeness (cell.yaml idiom params); this
        # DC-ideal tier's math does not depend on either -- see module docstring.
        self.c_in_farads = float(c_in_farads)
        self.c_out_farads = float(c_out_farads)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        vin_rel = inputs["VIN"] - gnd
        if vin_rel >= REGULATION_THRESHOLD_VOLTS:
            vout_rel = VOUT_NOMINAL
        else:
            vout_rel = max(0.0, vin_rel - DROPOUT_OFFSET_VOLTS)
        return {"VOUT": max(gnd, gnd + vout_rel)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block (needs ``c_in_farads``, ``c_out_farads``)."""
    return LinearRegFixed(
        c_in_farads=float(params["c_in_farads"]),  # type: ignore[arg-type]
        c_out_farads=float(params["c_out_farads"]),  # type: ignore[arg-type]
    )
