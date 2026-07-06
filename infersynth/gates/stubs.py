"""Stub gates with settled interfaces (DESIGN.md section 7, gates 1, 4).

Each returns SKIPPED with a reason until its backend lands; the GateRunner
report makes that loud. The context-key contracts documented here are the
interfaces the real implementations will honor. (The ERC and netlist gates are
implemented in :mod:`infersynth.gates.erc` / :mod:`infersynth.gates.netlist`.)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from infersynth.gates.runner import GateResult

__all__ = ["simulation_gate", "render_review_gate"]


def simulation_gate(context: Mapping[str, Any]) -> GateResult:
    """Emitted SystemC-AMS top vs. the spec's testbench (DESIGN section 7 gate 1).

    Context keys:

    * ``elaborated``: the ElaboratedDesign to emit
    * ``testbench``: the spec's testbench artifacts

    Will emit the SystemC-AMS top-level, compile, run, and compare against
    expected results. SKIPPED (loudly) where the toolchain is not installed.
    """
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
