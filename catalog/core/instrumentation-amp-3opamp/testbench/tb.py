"""Testbench for instrumentation-amp-3opamp (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. INN and REF are both held at 0 V DC (so ``gain*(INP - INN)``
reduces to ``gain*INP`` and OUT is directly comparable to INP for the gain
check). Two scenarios sharing ±12 V rails:

* ``linear`` -- a 1 Vpk sine on INP; output stays in the linear region, so
  measured peak-to-peak gain must equal the bound ``gain`` within ±1 %. (No
  :func:`~infersynth.sim.inverted` check: this topology is non-inverting --
  OUT moves the same direction as INP, unlike the inverting-amp cells.)
* ``overdrive`` -- a 5 Vpk sine on INP; ideal output (``gain*INP``) exceeds
  the rails at this ``gain``, so the output must stay clipped within the
  rails.
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import (
    DCSource,
    Run,
    SineSource,
    Stimulus,
    Testbench,
    amplitude_ratio,
    clipped_within,
)

#: Operating point (idiom params). rf_ohms/r_diff_ohms default; gain has no
#: default so the testbench pins it. The gate validates this through bind_cell.
PARAMS = {"gain": 4.0}

VCC = 12.0
VEE = -12.0
FREQ_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples per cycle
N_STEPS = 1000  # exactly one cycle


def _rails_and_ref() -> tuple[Stimulus, Stimulus, Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
        Stimulus(DCSource(name="ref_src", value=0.0), {"out": "vref"}),
        Stimulus(DCSource(name="inn_src", value=0.0), {"out": "vinn"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    gain = float(params["gain"])  # type: ignore[arg-type]

    linear = Run(
        name="linear",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vinp_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vinp"}),
        ),
        checks=(
            amplitude_ratio("vinp", "vout", expected=gain, tol_pct=1.0),
            clipped_within("vout", VEE, VCC),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vinp_src", amplitude=5.0, freq_hz=FREQ_HZ), {"out": "vinp"}),
        ),
        checks=(clipped_within("vout", VEE, VCC),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={
            "INP": "vinp",
            "INN": "vinn",
            "OUT": "vout",
            "REF": "vref",
            "VCC": "vcc",
            "VEE": "vee",
        },
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE},
    )
