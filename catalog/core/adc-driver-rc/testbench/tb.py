"""Testbench for adc-driver-rc (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Both scenarios are DC (a step and a steady-state check), driven
long enough to settle: the step's settle time is ``5*RC`` (5 time constants,
> 99% settled), and ``dt`` is picked well below the time constant (1/1000th
of it) so the forward-Euler integrator in the model is accurate; ``n_steps``
is derived from those two so the run genuinely reaches steady state instead
of an arbitrary fixed count.

* ``step_1v`` -- IN steps to 1 V DC at t=0 (GND = 0 V); OUT must settle to
  1 V within tolerance after 5*RC.
* ``steady_2v`` -- a DC pass-through sanity check at a different level (2 V);
  same settle-time budget, OUT must settle to 2 V.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, settles_to

#: Operating point (idiom params); r_ohms/c_farads take their cell.yaml
#: defaults so the settle-time math below matches what the DUT actually uses.
PARAMS: dict[str, object] = {}

R_OHMS = 49.9
C_FARADS = 1.0e-9
TAU = R_OHMS * C_FARADS  # RC time constant (seconds)

DT = TAU / 1000.0  # dt << RC so forward-Euler integration is accurate
N_STEPS = int(5 * TAU / DT) + 1  # 5 time constants -> > 99.3% settled

#: Tolerance for the settled value: 5*RC leaves ~0.67% (e^-5) of the *step
#: size* unsettled in the ideal continuous RC response. Sized against the
#: larger of the two scenario levels (2 V) plus a small Euler-discretization
#: margin, so both the 1 V and 2 V runs pass with the same fixed budget.
SETTLE_TOL = 0.02
#: Check the tail of the run (last handful of samples, all near the full
#: 5*RC settle time -- not an early slice, which would still be mid-transient).
SETTLE_AFTER = N_STEPS - 5


def _rails(vin: float) -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vin_src", value=vin), {"out": "vin"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    step_1v = Run(
        name="step_1v",
        stimulus=_rails(vin=1.0),
        checks=(settles_to("vout", value=1.0, tol=SETTLE_TOL, after_step=SETTLE_AFTER),),
    )
    steady_2v = Run(
        name="steady_2v",
        stimulus=_rails(vin=2.0),
        checks=(settles_to("vout", value=2.0, tol=SETTLE_TOL, after_step=SETTLE_AFTER),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "GND": "gnd"},
        runs=(step_1v, steady_2v),
        rails={"GND": 0.0},
    )
