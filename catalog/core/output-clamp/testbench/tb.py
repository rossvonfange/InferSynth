"""Testbench for output-clamp (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. Single-supply rails (VCC = 5 V, GND = 0 V -- this cell has no VEE
port). Two scenarios:

* ``linear`` -- a 1 Vpk sine centered mid-rail (2.5 V offset), safely inside
  [GND - Vf, VCC + Vf]; the clamp is transparent here, so measured
  peak-to-peak gain must equal 1.0 within ±1 %.
* ``overdrive`` -- a 10 Vpk sine centered mid-rail, well beyond both rails;
  the output must stay clipped within [GND - 0.31, VCC + 0.31] (0.31 gives a
  hair of margin over the 0.3 V Schottky headroom for float comparison).
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

#: Operating point (idiom params). r_series_ohms takes its cell.yaml default.
PARAMS: dict[str, object] = {}

VCC = 5.0
GND = 0.0
FREQ_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples per cycle
N_STEPS = 1000  # exactly one cycle


def _rails() -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="gnd_src", value=GND), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    linear = Run(
        name="linear",
        stimulus=(
            *_rails(),
            Stimulus(
                SineSource(name="vin_src", amplitude=1.0, freq_hz=FREQ_HZ, offset=2.5),
                {"out": "vin"},
            ),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=1.0, tol_pct=1.0),
            clipped_within("vout", GND - 0.3, VCC + 0.3),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails(),
            Stimulus(
                SineSource(name="vin_src", amplitude=10.0, freq_hz=FREQ_HZ, offset=2.5),
                {"out": "vin"},
            ),
        ),
        checks=(clipped_within("vout", GND - 0.31, VCC + 0.31),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "VCC": "vcc", "GND": "gnd"},
        runs=(linear, overdrive),
        rails={"VCC": VCC, "GND": GND},
    )
