"""Testbench for opamp-gain-x4-noninverting (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` and ``make_testbench(params) -> Testbench``. One shared input
signal drives all four channels (gains 1, 2, 3, 4 — "gains 1..4"), so each
channel's measured peak-to-peak gain must equal its bound gain within ±1 %.
Two scenarios share ±12 V rails: a 1 Vpk ``linear`` sine, and a 5 Vpk
``overdrive`` sine that forces the higher-gain channels to clip within the rails.
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

#: Operating point: channels fixed at 4; the four channel gains are 1..4.
PARAMS = {"channels": 4, "gain1": 1.0, "gain2": 2.0, "gain3": 3.0, "gain4": 4.0}

VCC = 12.0
VEE = -12.0
FREQ_HZ = 1000.0
DT = 1.0e-6
N_STEPS = 1000
_CHANNELS = (1, 2, 3, 4)


def _rails() -> tuple[Stimulus, Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
        Stimulus(DCSource(name="gnd_src", value=0.0), {"out": "gnd"}),
    )


def _dut_bindings() -> dict[str, str]:
    b = {"VCC": "vcc", "VEE": "vee", "GND": "gnd"}
    for k in _CHANNELS:
        b[f"IN{k}"] = "vin"  # one shared input into all four channels
        b[f"OUT{k}"] = f"vout{k}"
    return b


def make_testbench(params: Mapping[str, object]) -> Testbench:
    gains = {k: float(params[f"gain{k}"]) for k in _CHANNELS}  # type: ignore[arg-type]

    linear = Run(
        name="linear",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(
            *(
                amplitude_ratio("vin", f"vout{k}", expected=gains[k], tol_pct=1.0)
                for k in _CHANNELS
            ),
            *(clipped_within(f"vout{k}", VEE, VCC) for k in _CHANNELS),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails(),
            Stimulus(SineSource(name="vin_src", amplitude=5.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=tuple(clipped_within(f"vout{k}", VEE, VCC) for k in _CHANNELS),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings=_dut_bindings(),
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE, "GND": 0.0},
    )
