"""Testbench for summing-offset-stage (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. REF is held at 0 V DC (the "arbitrary reference level" cell.yaml
allows collapses to plain ground for this scenario). Two scenarios sharing
±12 V rails:

* ``linear`` — IN2 held at 0 V DC (== REF, so its ``w2*(IN2-REF)`` term is
  identically zero) and a 1 Vpk sine on IN1; the output must (a) have
  peak-to-peak amplitude ratio ≈ ``w1`` within ±1 %, and (b) be
  phase-inverted w.r.t. IN1 (the topology is an inverting summer). We do
  *not* assert ``amplitude_ratio`` when IN2 carries a nonzero DC offset — a
  DC offset on IN2 shifts OUT's operating point but does not change its
  measured peak-to-peak swing versus IN1 in general once clipping is near,
  so keeping IN2 at the REF level here is what makes the ratio check honest.
* ``overdrive`` — a large sine on IN1 plus a nonzero DC offset on IN2 drives
  the ideal (unclipped) output well past both rails; the output must stay
  clipped within the rails.
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

#: Operating point (idiom params). rf_ohms defaults; w1/w2 are pinned so the
#: scenario's expected gain is known. The gate validates this through bind_cell.
PARAMS = {"w1": 2.0, "w2": 1.0}

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
    w1 = float(params["w1"])  # type: ignore[arg-type]

    linear = Run(
        name="linear",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vin1_src", amplitude=1.0, freq_hz=FREQ_HZ), {"out": "vin1"}),
            Stimulus(DCSource(name="vin2_src", value=0.0), {"out": "vin2"}),
        ),
        checks=(
            amplitude_ratio("vin1", "vout", expected=w1, tol_pct=1.0),
            inverted("vin1", "vout"),
            clipped_within("vout", VEE, VCC),
        ),
    )
    overdrive = Run(
        name="overdrive",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vin1_src", amplitude=10.0, freq_hz=FREQ_HZ), {"out": "vin1"}),
            Stimulus(DCSource(name="vin2_src", value=5.0), {"out": "vin2"}),
        ),
        checks=(clipped_within("vout", VEE, VCC),),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={
            "IN1": "vin1",
            "IN2": "vin2",
            "OUT": "vout",
            "REF": "vref",
            "VCC": "vcc",
            "VEE": "vee",
        },
        runs=(linear, overdrive),
        rails={"VCC": VCC, "VEE": VEE},
    )
