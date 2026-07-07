"""Behavioral model for bridge-interface (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``r_series_ohms``, ``c_diff_farads``, ``c_cm_farads`` (cell.yaml's idiom
params, binding R1/R2/C1/C2/C3). The block's ports match cell.yaml ``ports``
exactly: SENSE_P, SENSE_N, OUT_P, OUT_N, GND.

Per-line first-order lag, referenced to GND, integrated with forward-Euler at
the kernel's fixed ``dt`` (same discretization as adc-driver-rc's RCLag)::

    tau = r_series_ohms * (c_cm_farads + 2*c_diff_farads)
    y_p[n+1] = y_p[n] + (dt/tau) * ((SENSE_P[n] - GND[n]) - y_p[n])
    y_n[n+1] = y_n[n] + (dt/tau) * ((SENSE_N[n] - GND[n]) - y_n[n])
    OUT_P = GND + y_p
    OUT_N = GND + y_n

**Simplification, documented honestly:** the real network is a coupled
differential/common-mode RC mesh (R1/R2 series into the shared C1 diff cap
plus the two C2/C3 common-mode shunt caps) -- a true 2-port analysis would
give OUT_P and OUT_N *coupled* dynamics (a differential-mode pole and a
common-mode pole that generally differ). This model collapses that to a
**single effective pole per line** at ``tau = r_series_ohms*(c_cm_farads +
2*c_diff_farads)`` -- the standard first-order lumped approximation for this
sense-conditioning topology (the differential cap contributes with a factor
of 2 because each series resistor sees C1 in series with the far side's own
R+C2 path, approximated here as if it were driven the same as the local C2
by symmetry) -- and treats the two lines as **independent** single-pole lags
sharing one time constant, not a true differential/common-mode decomposition.
This is honest for the v0 tier's DC pass-through checks below (which don't
depend on the diff/CM split at all) but is *not* validated against AC
common-mode-rejection behavior -- see the testbench docstring's "no AC check"
note. ``y_p``/``y_n`` are **stateful** (persist across steps within one run;
the kernel gives each run a fresh DUT, so no state leaks between scenarios).
"""

from __future__ import annotations

from collections.abc import Mapping


class BridgeInterface:
    """Two independent first-order lags (SENSE_P->OUT_P, SENSE_N->OUT_N)."""

    inputs = ("SENSE_P", "SENSE_N", "GND")
    outputs = ("OUT_P", "OUT_N")

    def __init__(
        self,
        r_series_ohms: float,
        c_diff_farads: float,
        c_cm_farads: float,
        name: str = "bridge_interface",
    ):
        self.name = name
        self.r_series_ohms = float(r_series_ohms)
        self.c_diff_farads = float(c_diff_farads)
        self.c_cm_farads = float(c_cm_farads)
        self.tau = self.r_series_ohms * (self.c_cm_farads + 2.0 * self.c_diff_farads)
        self.y_p = 0.0  # OUT_P voltage relative to GND
        self.y_n = 0.0  # OUT_N voltage relative to GND

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        sense_p_rel = inputs["SENSE_P"] - gnd
        sense_n_rel = inputs["SENSE_N"] - gnd
        self.y_p += (dt / self.tau) * (sense_p_rel - self.y_p)
        self.y_n += (dt / self.tau) * (sense_n_rel - self.y_n)
        return {"OUT_P": gnd + self.y_p, "OUT_N": gnd + self.y_n}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block (needs ``r_series_ohms``, ``c_diff_farads``, ``c_cm_farads``)."""
    return BridgeInterface(
        r_series_ohms=float(params["r_series_ohms"]),  # type: ignore[arg-type]
        c_diff_farads=float(params["c_diff_farads"]),  # type: ignore[arg-type]
        c_cm_farads=float(params["c_cm_farads"]),  # type: ignore[arg-type]
    )
