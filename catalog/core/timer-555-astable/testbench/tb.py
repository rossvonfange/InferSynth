"""Testbench for timer-555-astable (sim-gate v0 tier, docs/SIM.md).

Exports ``PARAMS`` (the operating point) and ``make_testbench(params) ->
Testbench``. ``PARAMS`` is empty — ``freq_hz``/``duty``/``c_farads`` all take
their cell.yaml defaults (1000 Hz, 0.6, 100 nF), same convention as
adc-driver-rc's testbench.

One scenario, ``square_wave``, driven for exactly 10 full periods at 100
samples/period (``dt`` = period/100, ``n_steps`` = 10*100 = 1000) — enough
samples per period that a mean-value duty measurement is accurate to about
1% (1/100), comfortably inside the check's tolerance, and enough whole
periods (10) that the duty measurement isn't skewed by a partial cycle at
either end of the run.

Checks:

* ``duty_by_mean`` -- the model has no dedicated "mean value" check in
  ``infersynth.sim.checks``, so this testbench builds one inline (a `Check`
  is just a name + a pure ``traces -> (ok, detail)`` predicate, per
  ``infersynth/sim/checks.py``'s own docstring) — mean(OUT) over the run
  must equal GND + duty*(VCC-GND) within 5%.
* ``clipped_within`` -- OUT never leaves the [GND, VCC] rails (an ideal
  rail-to-rail square wave, no ringing/overshoot in this v0 tier).
"""

from __future__ import annotations

from collections.abc import Mapping

from infersynth.sim import Check, DCSource, Run, Stimulus, Testbench, clipped_within

#: Operating point (idiom params); freq_hz/duty/c_farads all take their
#: cell.yaml defaults.
PARAMS: dict[str, object] = {}

VCC = 5.0
GND = 0.0
FREQ_HZ = 1000.0
DUTY = 0.6
PERIOD = 1.0 / FREQ_HZ

SAMPLES_PER_PERIOD = 100
N_PERIODS = 10
DT = PERIOD / SAMPLES_PER_PERIOD
N_STEPS = SAMPLES_PER_PERIOD * N_PERIODS  # exactly 10 whole periods, no partial cycle

#: Mean-value tolerance for the duty measurement (fraction of the VCC-GND span).
DUTY_TOL_FRAC = 0.05


def _mean_duty(sig: str, expected_duty: float, tol_frac: float) -> Check:
    """mean(``sig``) over the whole run must be GND + duty*(VCC-GND) ± tol_frac*(VCC-GND)."""

    def fn(traces: Mapping[str, list]) -> tuple[bool, str]:
        if sig not in traces:
            return False, f"signal {sig!r} not in traces (have {sorted(traces)})"
        samples = traces[sig]
        if not samples:
            return False, f"signal {sig!r} has no samples"
        measured_mean = sum(samples) / len(samples)
        span = VCC - GND
        expected_mean = GND + expected_duty * span
        tol = tol_frac * span
        ok = abs(measured_mean - expected_mean) <= tol
        return ok, (
            f"mean({sig})={measured_mean:.6g} vs expected {expected_mean:.6g} "
            f"(duty={expected_duty:g}) ±{tol:.6g}"
        )

    return Check(name=f"duty_by_mean({sig})", fn=fn)


def _rails() -> tuple[Stimulus, Stimulus]:
    return (
        Stimulus(DCSource(name="vcc_src", value=VCC), {"out": "vcc"}),
        Stimulus(DCSource(name="gnd_src", value=GND), {"out": "gnd"}),
    )


def make_testbench(params: Mapping[str, object]) -> Testbench:
    square_wave = Run(
        name="square_wave",
        stimulus=_rails(),
        checks=(
            _mean_duty("out", expected_duty=DUTY, tol_frac=DUTY_TOL_FRAC),
            clipped_within("out", lo=GND, hi=VCC),
        ),
    )

    return Testbench(
        dt=DT,
        n_steps=N_STEPS,
        dut_bindings={"OUT": "out", "VCC": "vcc", "GND": "gnd"},
        runs=(square_wave,),
        rails={"VCC": VCC, "GND": GND},
    )
