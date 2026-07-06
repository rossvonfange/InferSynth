"""Tests for the DC and sine stimulus sources."""

from __future__ import annotations

import math

from infersynth.sim import DCSource, Simulation, SineSource


def test_dc_source_is_constant():
    s = DCSource(name="dc", value=3.3)
    assert s.inputs == ()
    assert s.outputs == ("out",)
    assert s.step(0.0, 1.0, {}) == {"out": 3.3}
    assert s.step(99.0, 1.0, {}) == {"out": 3.3}


def test_sine_source_shape():
    s = SineSource(name="sin", amplitude=2.0, freq_hz=1.0)
    # quarter period at f=1Hz is t=0.25 -> sin(pi/2)=1 -> value = amplitude
    assert math.isclose(s.step(0.25, 0.0, {})["out"], 2.0, abs_tol=1e-12)
    assert math.isclose(s.step(0.0, 0.0, {})["out"], 0.0, abs_tol=1e-12)


def test_sine_offset_and_phase():
    s = SineSource(name="sin", amplitude=1.0, freq_hz=1.0, offset=5.0, phase=math.pi / 2)
    # phase pi/2 at t=0 -> cos(0)=1 -> offset + amplitude
    assert math.isclose(s.step(0.0, 0.0, {})["out"], 6.0, abs_tol=1e-12)


def test_sine_peak_to_peak_over_a_cycle():
    sim = Simulation(dt=1e-3, n_steps=1000)  # 1 kHz sampling, 1 Hz sine -> 1 cycle
    sim.add(SineSource(name="sin", amplitude=2.5, freq_hz=1.0), {"out": "v"})
    trace = sim.run()["v"]
    assert math.isclose(max(trace) - min(trace), 5.0, rel_tol=1e-3)


def test_custom_port_name():
    s = DCSource(name="dc", value=1.0, port="vcc")
    assert s.outputs == ("vcc",)
    assert s.step(0.0, 1.0, {}) == {"vcc": 1.0}
