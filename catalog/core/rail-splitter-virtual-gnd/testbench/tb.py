"""Testbench for rail-splitter-virtual-gnd (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. A single DC scenario; the DUT is stateless, so a handful of
steps settles immediately.

* ``midpoint`` -- VCC = 10 V DC, GND = 0 V DC; VGND must settle to 5.0 V
  (the ideal buffered divider midpoint, well within the [GND+0.1, VCC-0.1]
  rail-margin clip at this operating point).
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params). r_div_ohms/c_filt_farads take their
#: cell.yaml defaults.
PARAMS: dict[str, object] = {}

DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless block settles immediately


def make_testbench(params: Mapping[str, object]) -> Testbench:
    midpoint = Run(
        name="midpoint",
        stimulus=(
            Stimulus(DCSource(name="vcc_src", value=10.0), {"out": "vcc"}),
            Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
        ),
        checks=(settles_to("vgnd", value=5.0, tol=1.0e-6, after_step=0),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"VCC": "vcc", "GND": "gnd", "VGND": "vgnd"},
        runs=(midpoint,),
        rails={"VCC": 10.0, "GND": 0.0},
    )
