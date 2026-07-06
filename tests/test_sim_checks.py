"""Tests for the trace-assertion helpers."""

from __future__ import annotations

import math

from infersynth.sim import amplitude_ratio, clipped_within, inverted, settles_to


def test_amplitude_ratio_pass():
    traces = {"vin": [-1.0, 0.0, 1.0], "vout": [-4.0, 0.0, 4.0]}
    ok, detail = amplitude_ratio("vin", "vout", expected=4.0, tol_pct=1.0).evaluate(traces)
    assert ok
    assert "measured gain 4" in detail


def test_amplitude_ratio_fail_outside_tol():
    traces = {"vin": [-1.0, 1.0], "vout": [-4.2, 4.2]}
    ok, _ = amplitude_ratio("vin", "vout", expected=4.0, tol_pct=1.0).evaluate(traces)
    assert not ok


def test_amplitude_ratio_flat_input_fails():
    traces = {"vin": [1.0, 1.0], "vout": [4.0, 4.0]}
    ok, detail = amplitude_ratio("vin", "vout", expected=4.0, tol_pct=1.0).evaluate(traces)
    assert not ok
    assert "flat" in detail


def test_amplitude_ratio_missing_signal():
    ok, detail = amplitude_ratio("vin", "vout", 4.0, 1.0).evaluate({"vin": [1.0]})
    assert not ok
    assert "not in traces" in detail


def test_clipped_within_pass_and_fail():
    ok, _ = clipped_within("v", -12.0, 12.0).evaluate({"v": [-11.9, 0.0, 11.9]})
    assert ok
    bad, detail = clipped_within("v", -12.0, 12.0).evaluate({"v": [-13.0, 0.0, 11.9]})
    assert not bad
    assert "-13" in detail


def test_settles_to():
    traces = {"v": [0.0, 2.0, 4.9, 5.0, 5.01, 4.99]}
    ok, _ = settles_to("v", value=5.0, tol=0.05, after_step=3).evaluate(traces)
    assert ok
    bad, _ = settles_to("v", value=5.0, tol=0.05, after_step=0).evaluate(traces)
    assert not bad


def test_settles_to_no_tail():
    ok, detail = settles_to("v", 5.0, 0.1, after_step=10).evaluate({"v": [5.0, 5.0]})
    assert not ok
    assert "no samples after" in detail


def _sine(n: int = 100, scale: float = 1.0, phase: float = 0.0) -> list[float]:
    return [scale * math.sin(2.0 * math.pi * i / n + phase) for i in range(n)]


def test_inverted_pass_on_perfect_antiphase():
    xs = _sine(scale=1.0)
    ys = _sine(scale=-4.0)  # perfectly anti-correlated, scaled by -4
    ok, detail = inverted("vin", "vout").evaluate({"vin": xs, "vout": ys})
    assert ok
    assert "correlation" in detail


def test_inverted_fails_on_same_phase():
    xs = _sine(scale=1.0)
    ys = _sine(scale=4.0)  # in-phase, not inverted
    ok, _ = inverted("vin", "vout").evaluate({"vin": xs, "vout": ys})
    assert not ok


def test_inverted_flat_signal_fails():
    ok, detail = inverted("vin", "vout").evaluate({"vin": [1.0, 1.0], "vout": [-1.0, -2.0]})
    assert not ok
    assert "flat" in detail


def test_inverted_missing_signal():
    ok, detail = inverted("vin", "vout").evaluate({"vin": [1.0]})
    assert not ok
    assert "not in traces" in detail
