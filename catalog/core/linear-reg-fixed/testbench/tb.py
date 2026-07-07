"""Testbench for linear-reg-fixed (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC; the DUT is stateless, so a handful of
steps settles immediately.

* ``regulating`` -- VIN = 12 V DC (>= the 7 V regulation threshold), GND =
  0 V; VOUT must settle to 5.0 V (the nominal regulated output).
* ``dropout`` -- VIN = 6 V DC (below the 7 V threshold -- the documented
  dropout region), GND = 0 V; VOUT must settle to 4.0 V (``6 - 2`` per the
  model's constant dropout-offset simplification -- see model/behavior.py's
  docstring for why this is an honest approximation, not a claim of
  datasheet-accurate dropout behavior).
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params). c_in_farads/c_out_farads take their
#: cell.yaml defaults.
PARAMS: dict[str, object] = {}

DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless block settles immediately


def _rails(vin: float) -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vin_src", value=vin), {"out": "vin"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    regulating = Run(
        name="regulating",
        stimulus=_rails(vin=12.0),
        checks=(settles_to("vout", value=5.0, tol=1.0e-6, after_step=0),),
    )
    dropout = Run(
        name="dropout",
        stimulus=_rails(vin=6.0),
        checks=(settles_to("vout", value=4.0, tol=1.0e-6, after_step=0),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"VIN": "vin", "VOUT": "vout", "GND": "gnd"},
        runs=(regulating, dropout),
        rails={"GND": 0.0},
    )
