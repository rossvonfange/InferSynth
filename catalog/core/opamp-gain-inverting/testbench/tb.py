"""Testbench for opamp-gain-inverting (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. REF is driven by a DC source at 0 V (the "analog reference" is
just ground for this scenario — cell.yaml leaves the actual reference level
up to the instantiator). Two scenarios sharing ±12 V rails:

* ``linear`` — a 1 Vpk sine on IN; the output must (a) have peak-to-peak
  amplitude ratio ≈ the bound gain within ±1 %, and (b) be phase-inverted
  w.r.t. IN (the defining behavior of this topology vs. the non-inverting
  cell) — checked with :func:`infersynth.sim.inverted`.
* ``overdrive`` — a 5 Vpk sine; ideal output magnitude exceeds the rails, so
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
    inverted,
)

#: Operating point (idiom params). rin_ohms defaults; gain has no default so
#: the testbench pins it. The gate validates this through bind_cell.
PARAMS = {"gain": 4.0}

VCC = 12.0
VEE = -12.0
FREQ_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples per cycle
N_STEPS = 1000  # exactly one cycle


def _rails_and_ref() -> tuple[Stimulus, Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
        Stimulus(DCSource(name="ref_src", value=0.0), {"out": "vref"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    gain = float(params["gain"])  # type: ignore[arg-type]

    linear = Run(
        name="linear",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vin_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=gain, tol_pct=1.0),
            inverted("vin", "vout"),
            clipped_within("vout", VEE, VCC),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vin_src", amplitude=5.0, freq_hz=FREQ_HZ), {"out": "vin"}),
        ),
        checks=(clipped_within("vout", VEE, VCC),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"IN": "vin", "OUT": "vout", "REF": "vref", "VCC": "vcc", "VEE": "vee"},
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE},
    )
