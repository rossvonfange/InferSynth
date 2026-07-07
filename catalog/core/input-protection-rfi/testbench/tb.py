"""Testbench for input-protection-rfi (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Single-supply rails (VCC = 5 V, GND = 0 V -- this cell has no
VEE port, same as output-clamp). ``dt`` is picked well below the R1/C1 time
constant (1/1000th of it, same pattern as adc-driver-rc) so the model's
forward-Euler integrator is accurate; ``n_steps`` covers ``5*tau`` (> 99%
settled).

* ``linear`` -- IN steps to 2 V DC, well inside the clamp window
  ``[GND - 0.3, VCC + 0.3] = [-0.3, 5.3]``; OUT must settle to 2 V (the RC
  lag and clamp are both transparent here).
* ``overdrive`` -- IN steps to 10 V DC, well beyond the clamp window; OUT
  must stay clipped within ``[-0.3, 5.3]`` throughout the run (true by
  construction once the lag state exceeds the clamp bound -- same "clip
  mechanism" check style as output-clamp's overdrive run).
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import DCSource, Run, Stimulus, Testbench, clipped_within, settles_to

#: Operating point (idiom params); both take their cell.yaml defaults so the
#: time-constant math below matches what the DUT actually uses.
PARAMS: dict[str, object] = {}

VCC = 5.0
GND = 0.0
CLAMP_LO = GND - 0.3
CLAMP_HI = VCC + 0.3

R_SERIES_OHMS = 1000.0
C_RFI_FARADS = 1.0e-9
TAU = R_SERIES_OHMS * C_RFI_FARADS  # seconds

DT = TAU / 1000.0  # dt << tau so forward-Euler integration is accurate
N_STEPS = int(5 * TAU / DT) + 1  # 5 time constants -> > 99.3% settled

SETTLE_TOL = 0.02
SETTLE_AFTER = N_STEPS - 5


def _rails() -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="gnd_src", value=GND), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    linear = Run(
        name="linear",
        stimulus=(*_rails(), Stimulus(DCSource(name="vin_src", value=2.0), {"out": "vin"})),
        checks=(settles_to("vout", value=2.0, tol=SETTLE_TOL, after_step=SETTLE_AFTER),),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(*_rails(), Stimulus(DCSource(name="vin_src", value=10.0), {"out": "vin"})),
        checks=(clipped_within("vout", CLAMP_LO, CLAMP_HI),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "VCC": "vcc", "GND": "gnd"},
        runs=(linear, overdrive),
        rails={"VCC": VCC, "GND": GND},
    )
