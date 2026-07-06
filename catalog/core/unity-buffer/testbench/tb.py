"""Testbench for unity-buffer (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (empty — this cell has no idiom params) and
``make_testbench(params) -> Testbench``.

Two scenarios sharing ±12 V rails:

* ``linear`` — a 1 Vpk sine; output tracks input 1:1, so measured peak-to-peak
  gain must equal 1.0 within ±1 %.
* ``overdrive`` — a 20 Vpk sine (exceeds the rails even before any gain), so
  the output must stay clipped within the rails.
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

#: Operating point (idiom params) — none for this cell. The gate validates
#: this (empty) dict through bind_cell.
PARAMS: dict[str, object] = {}

VCC = 12.0
VEE = -12.0
FREQ_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples per cycle
N_STEPS = 1000  # exactly one cycle


def _rails() -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    linear = Run(
        name="linear",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=1.0, tol_pct=1.0),
            clipped_within("vout", VEE, VCC),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=20.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(clipped_within("vout", VEE, VCC),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "VCC": "vcc", "VEE": "vee"},
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE},
    )
