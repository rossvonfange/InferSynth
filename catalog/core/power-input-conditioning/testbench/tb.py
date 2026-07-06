"""Testbench for power-input-conditioning (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC; the DUT is stateless (see
model/behavior.py's docstring for why no RC lag is modeled), so a handful of
steps settles immediately.

* ``forward`` -- VIN = 12 V DC; VOUT must settle to 11.7 V (12 V minus the
  0.3 V Schottky drop).
* ``reverse`` -- VIN = -12 V DC (reverse polarity); the Schottky blocks, so
  VOUT must settle to 0 V.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params). c_bulk_farads takes its cell.yaml default.
PARAMS: dict[str, object] = {}

DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless block settles immediately


def _rails(vin: float) -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vin_src", value=vin), {"out": "vin"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    forward = Run(
        name="forward",
        stimulus=_rails(vin=12.0),
        checks=(settles_to("vout", value=11.7, tol=1.0e-6, after_step=0),),
    )
    reverse = Run(
        name="reverse",
        stimulus=_rails(vin=-12.0),
        checks=(settles_to("vout", value=0.0, tol=1.0e-6, after_step=0),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"VIN": "vin", "VOUT": "vout", "GND": "gnd"},
        runs=(forward, reverse),
        rails={"GND": 0.0},
    )
