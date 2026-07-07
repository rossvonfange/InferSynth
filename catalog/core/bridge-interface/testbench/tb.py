"""Testbench for bridge-interface (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC (a step on each line, held long enough to
settle): the step's settle time is ``7*tau`` (> 99.9% settled -- a slightly
larger budget than adc-driver-rc's ``5*tau`` because this cell's two DC
targets, +3 V and -3 V, are further from 0 than adc-driver-rc's 1-2 V
levels, so the same *tolerance* needs a bit more settling margin), and
``dt`` is picked well below the time constant (1/1000th of it) so the
model's forward-Euler integrator is accurate; ``n_steps`` is derived from
those two, same pattern as adc-driver-rc's testbench.

* ``dc_passthrough`` -- SENSE_P steps to 3 V DC, SENSE_N steps to -3 V DC
  (a typical differential bridge excitation), GND = 0 V; both OUT_P and
  OUT_N must settle to their respective SENSE inputs (the RC network is
  transparent at DC).

**No AC check in this v0 tier** -- the model collapses the true coupled
diff/common-mode network to a single effective per-line pole (see
model/behavior.py's docstring for the simplification), so this testbench
does not attempt to validate any frequency-domain or common-mode-rejection
behavior; it only characterizes the DC pass-through the model actually
claims to get right.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params); all three take their cell.yaml defaults
#: so the settle-time math below matches what the DUT actually uses.
PARAMS: dict[str, object] = {}

R_SERIES_OHMS = 1000.0
C_DIFF_FARADS = 1.0e-8
C_CM_FARADS = 1.0e-9
TAU = R_SERIES_OHMS * (C_CM_FARADS + 2.0 * C_DIFF_FARADS)  # seconds

DT = TAU / 1000.0  # dt << tau so forward-Euler integration is accurate
N_STEPS = int(7 * TAU / DT) + 1  # 7 time constants -> > 99.9% settled

#: Tolerance for the settled value, sized against the 3 V step level plus a
#: small Euler-discretization margin (same reasoning as adc-driver-rc). 7
#: time constants leaves ~0.09% (e^-7) of the 3 V step unsettled (~0.007 V);
#: budgeted with headroom for the tail's discretization error.
SETTLE_TOL = 0.02
#: Check the tail of the run (all samples near the full 5*tau settle time).
SETTLE_AFTER = N_STEPS - 5


def make_testbench(params: Mapping[str, object]) -> Testbench:
    dc_passthrough = Run(
        name="dc_passthrough",
        stimulus=(
            Stimulus(DCSource(name="sense_p_src", value=3.0), {"out": "sense_p"}),
            Stimulus(DCSource(name="sense_n_src", value=-3.0), {"out": "sense_n"}),
            Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
        ),
        checks=(
            settles_to("out_p", value=3.0, tol=SETTLE_TOL, after_step=SETTLE_AFTER),
            settles_to("out_n", value=-3.0, tol=SETTLE_TOL, after_step=SETTLE_AFTER),
        ),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={
            "SENSE_P": "sense_p",
            "SENSE_N": "sense_n",
            "OUT_P": "out_p",
            "OUT_N": "out_n",
            "GND": "gnd",
        },
        runs=(dc_passthrough,),
        rails={"GND": 0.0},
    )
