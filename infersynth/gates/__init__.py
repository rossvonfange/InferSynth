"""Verification gates (DESIGN.md section 7): runner, real and stub gates."""

from infersynth.gates.netlist_equiv import (
    PartitionDiff,
    compare_partitions,
    partition_equivalence_gate,
)
from infersynth.gates.runner import GateReport, GateResult, GateRunner, GateStatus
from infersynth.gates.stubs import erc_gate, render_review_gate, simulation_gate

__all__ = [
    "GateReport",
    "GateResult",
    "GateRunner",
    "GateStatus",
    "PartitionDiff",
    "compare_partitions",
    "erc_gate",
    "partition_equivalence_gate",
    "render_review_gate",
    "simulation_gate",
]


def default_runner() -> GateRunner:
    """A GateRunner registered with the four DESIGN section 7 gates in order."""
    runner = GateRunner()
    runner.register("simulation", simulation_gate)
    runner.register("erc", erc_gate)
    runner.register("netlist-partition-equivalence", partition_equivalence_gate)
    runner.register("render-review", render_review_gate)
    return runner
