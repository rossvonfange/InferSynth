"""Testbench for led-indicator (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. This is a **sink-only** DUT (see ``model/behavior.py``'s
docstring): it has no output port to assert a value against, so this
testbench documents "check-light-only v0" honestly — the checks below only
prove (a) the kernel runs a DC bias into the sink-only block without
crashing, and (b) the IN/GND signals it consumes hold the expected DC
levels (a sanity check on the wiring, not on any LED behavior — there is
none modeled at this tier).

One scenario, ``dc_bias`` -- IN at ``vin_volts`` (idiom default 5.0 V), GND
at 0 V, held for a handful of steps (stateless DUT and sources both settle
immediately, same convention as vref-shunt's DC testbench).
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params). i_led_amps/vin_volts/vf_volts all take
#: their cell.yaml defaults.
PARAMS: dict[str, object] = {}

VIN_VOLTS = 5.0
DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless sink block settles immediately


def make_testbench(params: Mapping[str, object]) -> Testbench:
    dc_bias = Run(
        name="dc_bias",
        stimulus=(
            Stimulus(DCSource(name="in_src", value=VIN_VOLTS), {"out": "in"}),
            Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
        ),
        checks=(
            # check-light-only v0 (module docstring): no LED/optical behavior
            # is modeled -- this only proves the DC bias reaches the sink
            # block's inputs cleanly (no crash, correct steady levels).
            settles_to("in", value=VIN_VOLTS, tol=1.0e-6, after_step=0),
            settles_to("gnd", value=0.0, tol=1.0e-6, after_step=0),
        ),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "in", "GND": "gnd"},
        runs=(dc_bias,),
        rails={"GND": 0.0},
    )
