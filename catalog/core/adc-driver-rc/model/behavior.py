"""Behavioral model for adc-driver-rc (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``r_ohms``/``c_farads`` (cell.yaml binds ``R1: "r_ohms"``, ``C1: "c_farads"``).
The block's ports match cell.yaml ``ports`` exactly: IN, OUT, GND.

First-order RC lag, referenced to GND, integrated with a simple forward-Euler
step at the kernel's fixed ``dt``::

    y[n+1] = y[n] + (dt / (r_ohms * c_farads)) * ((IN[n] - GND[n]) - y[n])
    OUT = GND + y

This is the standard single-pole RC low-pass step response. The model is
**stateful** — ``y`` (the capacitor voltage relative to GND) lives on
``self`` and persists across steps within one simulation run (the kernel
gives each run a fresh DUT instance, per docs/SIM.md §4, so no state leaks
between testbench scenarios).

Euler-step accuracy note: this is an explicit-Euler integrator, not an exact
closed-form RC solution; it is accurate as long as ``dt << r_ohms*c_farads``
(the testbench picks ``dt`` well below the time constant so the discretization
error is negligible for the settling checks it makes).
"""

from __future__ import annotations

from collections.abc import Mapping


class RCLag:
    """A single-pole RC lag (forward-Euler integration of the RC ODE)."""

    inputs = ("IN", "GND")
    outputs = ("OUT",)

    def __init__(self, r_ohms: float, c_farads: float, name: str = "adc_driver_rc"):
        self.name = name
        self.r_ohms = float(r_ohms)
        self.c_farads = float(c_farads)
        self.y = 0.0  # capacitor voltage relative to GND

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        vin_rel = inputs["IN"] - gnd
        tau = self.r_ohms * self.c_farads
        self.y += (dt / tau) * (vin_rel - self.y)
        return {"OUT": gnd + self.y}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``r_ohms``, ``c_farads``)."""
    return RCLag(
        r_ohms=float(params["r_ohms"]),  # type: ignore[arg-type]
        c_farads=float(params["c_farads"]),  # type: ignore[arg-type]
    )
