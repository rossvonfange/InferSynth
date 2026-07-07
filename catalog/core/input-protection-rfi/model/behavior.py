"""Behavioral model for input-protection-rfi (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``r_series_ohms``/``c_rfi_farads`` (cell.yaml's idiom params, binding
R1/C1). The block's ports match cell.yaml ``ports`` exactly: IN, OUT, VCC,
GND.

First-order RC lag (same forward-Euler discretization as adc-driver-rc's
RCLag) into a hard dual-diode clamp, referenced to GND::

    tau = r_series_ohms * c_rfi_farads
    y[n+1] = y[n] + (dt/tau) * ((IN[n] - GND[n]) - y[n])
    OUT = clip(GND + y, GND - CLAMP_HEADROOM_VOLTS, VCC + CLAMP_HEADROOM_VOLTS)

``CLAMP_HEADROOM_VOLTS = 0.3`` is the Schottky forward-conduction headroom
beyond each rail, same convention/value as ``output-clamp``'s
``SCHOTTKY_VF`` (cell.yaml's ``archetype_symbol`` is also a Schottky here).

**Simplification, documented honestly:** the RC lag and the diode clamp are
modeled as two independent stages in series (lag first, then clip) rather
than a coupled network where the clamp diodes' conduction current also loads
R1 during a clamp event -- an honest approximation valid for the *voltage*
checks this v0 tier makes (which never drive far enough into clamp for long
enough that the diode's loading of the RC time constant would matter to a DC
settle check), not a claim of transient-fault-current accuracy. ``y`` is
**stateful** (persists across steps within one run; the kernel gives each
run a fresh DUT, so no state leaks between scenarios).
"""

from __future__ import annotations

from collections.abc import Mapping

#: Schottky forward-conduction headroom (volts) beyond each rail before the
#: clamp diode holds the node. Fixed by the Schottky archetype device
#: (cell.yaml's ``archetype_symbol``), not a cell.yaml idiom param. Same
#: value/convention as output-clamp's ``SCHOTTKY_VF``.
CLAMP_HEADROOM_VOLTS = 0.3


class InputProtectionRfi:
    """RC lag (R1/C1) followed by a hard dual-Schottky clamp."""

    inputs = ("IN", "VCC", "GND")
    outputs = ("OUT",)

    def __init__(
        self,
        r_series_ohms: float,
        c_rfi_farads: float,
        name: str = "input_protection_rfi",
    ):
        self.name = name
        self.r_series_ohms = float(r_series_ohms)
        self.c_rfi_farads = float(c_rfi_farads)
        self.tau = self.r_series_ohms * self.c_rfi_farads
        self.y = 0.0  # lagged node voltage relative to GND

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        vin_rel = inputs["IN"] - gnd
        self.y += (dt / self.tau) * (vin_rel - self.y)
        lo = gnd - CLAMP_HEADROOM_VOLTS
        hi = inputs["VCC"] + CLAMP_HEADROOM_VOLTS
        return {"OUT": min(max(gnd + self.y, lo), hi)}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block (needs ``r_series_ohms``, ``c_rfi_farads``)."""
    return InputProtectionRfi(
        r_series_ohms=float(params["r_series_ohms"]),  # type: ignore[arg-type]
        c_rfi_farads=float(params["c_rfi_farads"]),  # type: ignore[arg-type]
    )
