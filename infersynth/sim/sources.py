"""Stimulus blocks for testbenches (DC and sine), for the v0 dataflow kernel.

Both are **stateless** — each ``step`` is a pure function of ``t`` — so a run is
bit-identical on repeat with no state to reset (SELECTION.md §8). Each source
has a single output port; the default port name is ``"out"``, bound to a signal
by the testbench. ``math`` is the only import (no ``random``, no ``time``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["DCSource", "SineSource"]


@dataclass
class DCSource:
    """A constant source: emits ``value`` on its output port every step."""

    name: str
    value: float
    port: str = "out"

    @property
    def inputs(self) -> tuple[str, ...]:
        return ()

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.port,)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        return {self.port: float(self.value)}


@dataclass
class SineSource:
    """A sine source: ``offset + amplitude * sin(2*pi*freq_hz*t + phase)``.

    Stateless — computed straight from ``t`` — so ``freq_hz`` and ``dt`` set the
    samples-per-cycle deterministically with no accumulated phase drift.
    """

    name: str
    amplitude: float
    freq_hz: float
    offset: float = 0.0
    phase: float = 0.0
    port: str = "out"

    @property
    def inputs(self) -> tuple[str, ...]:
        return ()

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.port,)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        value = self.offset + self.amplitude * math.sin(
            2.0 * math.pi * self.freq_hz * t + self.phase
        )
        return {self.port: value}
