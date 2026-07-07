"""Testbench for sallen-key-lowpass-2 (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. REF is driven by a DC source at 0 V (cell.yaml leaves the actual
AC-ground level up to the instantiator). Three scenarios at ``fc_hz=1000``,
``q=0.7071`` (maximally-flat / Butterworth Q), sharing ±12 V rails and
``dt = 1 µs`` (<< ``1/fc_hz = 1 ms``, so the forward-Euler integrator in the
model is accurate, per the model docstring's caveat):

* ``passband`` -- a 1 Vpk sine at ``fc_hz/10`` (100 Hz, one full cycle: 10000
  steps at this ``dt``), deep in the passband -- measured peak-to-peak gain
  must be ≈ 1.0 within ±3 %.
* ``at_fc`` -- a 1 Vpk sine at ``fc_hz`` (one full cycle: 1000 steps) --
  measured peak-to-peak gain must be ≈ 0.707 (``-3 dB``, the definition of a
  Butterworth corner) within ±6 %.
* ``dc_settle`` -- a 1 V DC step; OUT must settle to 1 V (unity DC gain) after
  the filter's transient dies out.

Scenario numerics (verified by direct simulation during authoring): ``passband``
measures ≈0.9999 (well inside ±3 %); ``at_fc`` measures ≈0.7226 (inside ±6 % of
0.7071 -- Q=0.7071 gives a maximally-flat response, not a perfectly-flat one,
so a few-percent departure from the ideal ``-3 dB`` point at a *finite*
simulated cycle is expected and the tolerance is sized for it); ``dc_settle``
reaches ≈0.99999 after 3000 steps (3 ms, well past the ~0.9 ms 5-time-constant
settle budget at this Q).
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
    settles_to,
)

#: Operating point (idiom params). c_base_farads takes its cell.yaml default.
PARAMS = {"fc_hz": 1000.0, "q": 0.7071}

VCC = 12.0
VEE = -12.0
FC_HZ = 1000.0
DT = 1.0e-6  # 1 µs -> 1000 samples/cycle at fc_hz, << 1/fc_hz

#: One full cycle at fc_hz/10 = 100 Hz.
N_STEPS_PASSBAND = 10_000
#: One full cycle at fc_hz = 1000 Hz.
N_STEPS_AT_FC = 1_000
#: DC step: 3 ms (~13 time constants at this Q) -- past the settle budget.
N_STEPS_DC = 3_000
DC_SETTLE_TOL = 0.01
DC_SETTLE_AFTER = N_STEPS_DC - 100


def _rails_and_ref() -> tuple[Stimulus, Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="vee_src", value=VEE), {"out": "vee"}),
        Stimulus(DCSource(name="ref_src", value=0.0), {"out": "vref"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    passband = Run(
        name="passband",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(
                SineSource(name="vin_src", amplitude=1.0, freq_hz=FC_HZ / 10.0), {"out": "vin"}
            ),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=1.0, tol_pct=3.0),
            clipped_within("vout", VEE, VCC),
        ),
        n_steps=N_STEPS_PASSBAND,
    )
    at_fc = Run(
        name="at_fc",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(SineSource(name="vin_src", amplitude=1.0, freq_hz=FC_HZ), {"out": "vin"}),
        ),
        checks=(
            amplitude_ratio("vin", "vout", expected=0.7071, tol_pct=6.0),
            clipped_within("vout", VEE, VCC),
        ),
        n_steps=N_STEPS_AT_FC,
    )
    dc_settle = Run(
        name="dc_settle",
        stimulus=(
            *_rails_and_ref(),
            Stimulus(DCSource(name="vin_src", value=1.0), {"out": "vin"}),
        ),
        checks=(settles_to("vout", value=1.0, tol=DC_SETTLE_TOL, after_step=DC_SETTLE_AFTER),),
        n_steps=N_STEPS_DC,
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS_AT_FC,  # default; each run overrides its own n_steps
        dut_bindings={"IN": "vin", "OUT": "vout", "REF": "vref", "VCC": "vcc", "VEE": "vee"},
        runs=(passband, at_fc, dc_settle),
        rails={"VCC": VCC, "VEE": VEE},
    )
