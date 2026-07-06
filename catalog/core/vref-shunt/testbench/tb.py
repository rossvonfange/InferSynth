"""Testbench for vref-shunt (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC (no sine): a regulated-region run and a
below-threshold run that exercises the documented dropout simplification.

* ``regulated`` -- VIN = 5 V, GND = 0 V. At the default ``r_bias_ohms`` this
  is well above the 1 mA regulation threshold, so VREF settles to the TL431
  nominal 2.495 V.
* ``low_vin`` -- VIN = 1.5 V, below the regulation threshold at this
  ``r_bias_ohms``, so VREF must settle *below* the nominal 2.495 V (the
  documented "VREF follows VIN" simplification below threshold).

Both are steady-state DC runs; a handful of steps is enough since the sources
and DUT are stateless.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, clipped_within, settles_to

#: Operating point (idiom params). r_bias_ohms takes its cell.yaml default.
PARAMS: dict[str, object] = {}

DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless blocks settle immediately


def _rails(vin: float) -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vin_src", value=vin), {"out": "vin"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    regulated = Run(
        name="regulated",
        stimulus=_rails(vin=5.0),
        checks=(settles_to("vref", value=2.495, tol=1.0e-6, after_step=0),),
    )
    low_vin = Run(
        name="low_vin",
        stimulus=_rails(vin=1.5),
        checks=(
            clipped_within("vref", lo=-1.0, hi=2.495 - 1.0e-6),
            settles_to("vref", value=1.5, tol=1.0e-6, after_step=0),
        ),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"VIN": "vin", "VREF": "vref", "GND": "gnd"},
        runs=(regulated, low_vin),
        rails={"GND": 0.0},
    )
