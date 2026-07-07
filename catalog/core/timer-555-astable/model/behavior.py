"""Behavioral model for timer-555-astable (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries the
cell's resolved idiom params: ``freq_hz``, ``duty``, ``c_farads``. The block's
ports match cell.yaml ``ports`` exactly: OUT, VCC, GND.

Ideal astable square-wave oscillator, referenced to GND::

    phase = frac(t * freq_hz)               # 0.0 <= phase < 1.0
    OUT = VCC   if phase < duty
        = GND   otherwise

This is a **stateless** model (a pure function of ``t``, like the sim tier's
``SineSource``) — the real NE555's RC charge/discharge transient shape is not
modeled at all; this v0 tier only reproduces the astable's steady-state
output *waveform* (an ideal rail-to-rail square wave at the datasheet-derived
frequency/duty cycle), which is exactly what a downstream consumer of this
cell's OUT pin needs to know. ``c_farads`` does not appear in this model: it
only participates in the ``bindings`` math (deriving RA/RB alongside
``freq_hz``/``duty``), not in the ideal output waveform itself.
"""

from __future__ import annotations

from collections.abc import Mapping


class Astable555:
    """Ideal 555 astable: a rail-to-rail square wave at (freq_hz, duty)."""

    inputs = ("VCC", "GND")
    outputs = ("OUT",)

    def __init__(self, freq_hz: float, duty: float, name: str = "timer_555_astable"):
        self.name = name
        self.freq_hz = float(freq_hz)
        self.duty = float(duty)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        vcc = inputs["VCC"]
        gnd = inputs["GND"]
        phase = (t * self.freq_hz) % 1.0
        return {"OUT": vcc if phase < self.duty else gnd}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``freq_hz``, ``duty``)."""
    return Astable555(
        freq_hz=float(params["freq_hz"]),  # type: ignore[arg-type]
        duty=float(params["duty"]),  # type: ignore[arg-type]
    )
