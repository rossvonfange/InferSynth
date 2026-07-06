"""NETFLOW stage 1 — inferred net intent for synthesized designs.

Rail resolver + intra-chain wiring consumption, assembled into a deterministic
:class:`~infersynth.netflow.plan.WiringPlan` (NETFLOW.md, "Three tiers of
intent", tier 1). Emission of a plan onto a design lives in
:mod:`infersynth.compile_kicad.wiring`.
"""

from __future__ import annotations

from infersynth.netflow.intra import intra_chain_nets
from infersynth.netflow.plan import Net, WiringPlan, build_plan
from infersynth.netflow.rails import RailPlan, resolve_rails

__all__ = [
    "Net",
    "WiringPlan",
    "build_plan",
    "RailPlan",
    "resolve_rails",
    "intra_chain_nets",
]
