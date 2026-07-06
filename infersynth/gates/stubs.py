"""Stub gates with settled interfaces (DESIGN.md section 7, gates 1, 2, 4).

Each returns SKIPPED with a reason until its backend lands; the GateRunner
report makes that loud. The context-key contracts documented here are the
interfaces the real implementations will honor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from infersynth.gates.runner import GateResult

__all__ = ["erc_gate", "simulation_gate", "render_review_gate"]


def erc_gate(context: Mapping[str, Any]) -> GateResult:
    """ERC-zero via ``kicad-cli`` (DESIGN section 7 gate 2). Context keys:

    * ``schematic_path``: root .kicad_sch of the generated design
    * ``erc_allowlist``: documented benign-warning allowlist (optional)

    Will run full-hierarchy ERC from the root sheet and fail on any error not
    on the allowlist.
    """
    return GateResult.skipped(
        "erc", "not implemented: kicad-cli ERC backend not wired up in this pass"
    )


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
