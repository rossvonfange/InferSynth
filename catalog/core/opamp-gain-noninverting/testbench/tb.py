"""Testbench for opamp-gain-noninverting (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point — resolved by the gate through
``bind_cell``, then passed to both ``make_behavior`` and ``make_testbench``) and
``make_testbench(params) -> Testbench``.

Two scenarios sharing ±12 V rails:

* ``linear`` — a 1 Vpk sine; output stays in the linear region, so measured
  peak-to-peak gain must equal the bound gain within ±1 %.
* ``overdrive`` — a 5 Vpk sine; ideal output (gain·in) exceeds the rails, so the
  output must stay clipped within the rails.
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

#: Operating point (idiom params). rg_ohms defaults; gain has no default so the
#: testbench pins it. The gate validates this through bind_cell.
PARAMS = {"gain": 4.0}

VCC = 12.0
VEE = -12.0
FREQ_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples per cycle
N_STEPS = 1000  # exactly one cycle


def _rails() -> tuple[Stimulus, Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    gain = float(params["gain"])  # type: ignore[arg-type]

    linear = Run(
        name="linear",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=gain, tol_pct=1.0),
            clipped_within("vout", VEE, VCC),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=5.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(clipped_within("vout", VEE, VCC),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "VCC": "vcc", "VEE": "vee", "GND": "gnd"},
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE, "GND": 0.0},
    )
