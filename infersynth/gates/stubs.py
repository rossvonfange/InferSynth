"""Stub gates with settled interfaces (DESIGN.md section 7, gates 1, 4).

Each returns SKIPPED with a reason until its backend lands; the GateRunner
report makes that loud. The context-key contracts documented here are the
interfaces the real implementations will honor. (The ERC and netlist gates are
implemented in :mod:`infersynth.gates.erc` / :mod:`infersynth.gates.netlist`.)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from infersynth.catalog.loader import CellPackage
from infersynth.gates.runner import GateResult

__all__ = ["simulation_gate", "render_review_gate"]


def simulation_gate(context: Mapping[str, Any]) -> GateResult:
    """Simulation gate (DESIGN section 7 gate 1), two tiers.

    Context keys:

    * ``cell``: a :class:`~infersynth.catalog.loader.CellPackage` (or a cell
      directory path) — runs the **behavioral sim-gate v0** tier
      (:func:`infersynth.gates.simulation.simulation_cell_gate`): a real,
      deterministic pure-Python run for cells carrying ``model/behavior.py`` +
      ``testbench/tb.py`` (DESIGN.md §4 graceful-bootstrap tier).
    * ``elaborated`` / ``testbench``: the SystemC-AMS end-state tier — emit the
      top-level, compile, run, compare. Not yet wired; SKIPPED loudly.

    With neither key present, SKIPPED (loudly): a skipped gate is unverified.
    """
    from infersynth.gates.simulation import simulation_cell_gate

    cell = context.get("cell")
    if cell is not None:
        if not isinstance(cell, CellPackage):
            from infersynth.catalog.loader import load_cell

            cell = load_cell(cell)
        return simulation_cell_gate(cell)
    return GateResult.skipped(
        "simulation", "not implemented: SystemC-AMS emission/toolchain not wired up in this pass"
    )


def render_review_gate(context: Mapping[str, Any]) -> GateResult:
    """Rendered-output review (DESIGN section 7 gate 4; cosmetic, non-blocking).

    Context keys:

    * ``svg_path``: rendered schematic SVG

    Will run a vision pass for overlap/legibility defects and report (never
    block).
    """
    return GateResult.skipped(
        "render-review", "not implemented: render/vision backend not wired up in this pass"
    )
