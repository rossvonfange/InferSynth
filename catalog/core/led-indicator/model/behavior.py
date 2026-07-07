"""Behavioral model for led-indicator (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries the
cell's resolved idiom params: ``i_led_amps``, ``vin_volts``, ``vf_volts``
(``vin_volts``/``i_led_amps`` only participate in the ``bindings`` math that
derives R1 — the behavior model itself does not consume them; see below).
The block's ports match cell.yaml ``ports`` exactly: IN, GND.

**Sink-only block, honestly documented (v0 tier finding worth recording for
future cell authors):** cell.yaml declares only two ports, IN and GND, both
``direction: in`` — an LED status indicator has no electrical *output* a
downstream cell could consume; the "output" is a physical light, not a
signal. The simulation gate (``infersynth/gates/simulation.py``) requires
``set(dut.inputs) | set(dut.outputs) == set(cell.yaml ports)`` exactly, so
this model's ``outputs`` MUST be the empty tuple ``()`` — a diagnostic output
port (e.g. an ``I_LED`` current estimate) is not legal here, because it would
add a port cell.yaml doesn't declare and fail that exact-match check.

The v0 kernel (``infersynth/sim/kernel.py``) turns out to already support
this shape cleanly: :meth:`Simulation.run` traces every signal bound to *any*
block's ``inputs`` **or** ``outputs`` (see its ``signals.update(bb.in_signals)``
/ ``signals.update(bb.out_signals)`` calls), so IN and GND are still fully
recorded in the traces dict even though nothing in this cell drives an
output — a sink-only block is a first-class citizen of the kernel, not a
degenerate case. This is the "kernel supports it" branch of the honest
design choice the cell author had to make; no diagnostic-signal workaround
was needed.

What this model actually does, given it has no output to compute: it
validates its inputs sanely (both must be finite floats — a crash here would
still correctly fail the sim gate) and returns ``{}`` every step. It is a
v0 "check-light-only" stand-in — it proves DC bias on IN doesn't crash the
kernel and that the cell's ports round-trip through ``make_behavior``
correctly; it does not model LED optical output, forward-voltage clamping,
or the ANODE-node dynamics inside the fragment (those live in R1/D1's real
SPICE models, out of scope for this ideal-tier block).
"""

from __future__ import annotations

from collections.abc import Mapping


class LedIndicatorSink:
    """A sink-only diagnostic stub: consumes IN/GND, drives no output."""

    inputs = ("IN", "GND")
    outputs = ()

    def __init__(self, name: str = "led_indicator"):
        self.name = name
        #: last-seen values, kept for direct (non-kernel) inspection/testing
        #: only -- NOT exposed as a Block output port (see module docstring).
        self.last_in = 0.0
        self.last_gnd = 0.0

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        self.last_in = float(inputs["IN"])
        self.last_gnd = float(inputs["GND"])
        return {}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block. No idiom params affect this sink-only stub."""
    return LedIndicatorSink()
