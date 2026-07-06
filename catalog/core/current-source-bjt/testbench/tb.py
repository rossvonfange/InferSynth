"""Testbench for current-source-bjt (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC (no sine); the DUT is stateless, so a
handful of steps settles immediately. **Note:** ``OUT`` here is the
programmed current in amps, not a voltage -- see model/behavior.py's
module docstring.

* ``on`` -- VREF = 2.5 V (well above the 0.75 V turn-on threshold), so OUT
  settles to the programmed ``i_out_amps``.
* ``off`` -- VREF = 0.5 V (below the turn-on threshold: insufficient base
  drive), so OUT settles to 0 A.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params). i_out_amps takes its cell.yaml default
#: (1 mA); vref_volts only feeds R1's binding expression (unused by the
#: behavioral model directly), so it also takes its default.
PARAMS: dict[str, object] = {}

I_OUT_AMPS_DEFAULT = 0.001  # cell.yaml default for i_out_amps

DT = 1.0e-6
N_STEPS = 10  # DC steady state; stateless block settles immediately


def _rails(vref: float) -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vref_src", value=vref), {"out": "vref"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    i_out_amps = float(params.get("i_out_amps", I_OUT_AMPS_DEFAULT))  # type: ignore[arg-type]

    on = Run(
        name="on",
        stimulus=_rails(vref=2.5),
        checks=(settles_to("out", value=i_out_amps, tol=1.0e-6, after_step=0),),
    )
    off = Run(
        name="off",
        stimulus=_rails(vref=0.5),
        checks=(settles_to("out", value=0.0, tol=1.0e-6, after_step=0),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"VREF": "vref", "OUT": "out", "GND": "gnd"},
        runs=(on, off),
        rails={"GND": 0.0},
    )
