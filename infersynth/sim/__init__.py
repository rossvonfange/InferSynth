"""Deterministic pure-Python behavioral simulation tier (sim-gate v0).

This is the graceful-bootstrap simulation tier below the SystemC-AMS end-state
(DESIGN.md §4): a tiny fixed-timestep dataflow kernel (:mod:`.kernel`), stimulus
sources (:mod:`.sources`), trace assertions (:mod:`.checks`), and the testbench
convention types (:mod:`.testbench`). Zero dependencies beyond the standard
library; no ``random`` or ``time`` anywhere (SELECTION.md §8 repeatability).

See the module docstring of :mod:`infersynth.sim.kernel` for the swap seam to a
future SystemC-AMS kernel, and docs/SIM.md for the behavior/testbench contracts.
"""

from __future__ import annotations

from infersynth.sim.checks import Check, amplitude_ratio, clipped_within, settles_to
from infersynth.sim.kernel import (
    Block,
    BoundBlock,
    Simulation,
    SimulationError,
    topological_order,
)
from infersynth.sim.sources import DCSource, SineSource
from infersynth.sim.testbench import Run, Stimulus, Testbench

__all__ = [
    "Block",
    "BoundBlock",
    "Check",
    "DCSource",
    "Run",
    "Simulation",
    "SimulationError",
    "SineSource",
    "Stimulus",
    "Testbench",
    "amplitude_ratio",
    "clipped_within",
    "settles_to",
    "topological_order",
]
