"""Verification gates (DESIGN.md section 7): runner, real and stub gates."""

from infersynth.gates.ams_simulation import AMS_GATE, ams_simulation_cell_gate
from infersynth.gates.design_netlist import design_netlist_gate, expected_pins_for_member
from infersynth.gates.erc import ErcError, erc_gate, kicad_cli_available, run_erc
from infersynth.gates.harness import (
    HarnessError,
    HarnessResult,
    generate_harness,
    harness_params,
)
from infersynth.gates.netlist import (
    NetlistError,
    export_netlist,
    parse_golden_netlist,
    parse_kicadxml,
)
from infersynth.gates.netlist_equiv import (
    PartitionDiff,
    compare_partitions,
    partition_equivalence_gate,
)
from infersynth.gates.report_io import (
    GateReportParseError,
    load_report_dict,
    report_to_dict,
)
from infersynth.gates.runner import GateReport, GateResult, GateRunner, GateStatus
from infersynth.gates.simulation import run_cell_simulation, simulation_cell_gate
from infersynth.gates.stubs import render_review_gate, simulation_gate
from infersynth.gates.triage import ErcViolation, TriageOutcome, TriagePolicy

__all__ = [
    "AMS_GATE",
    "ErcError",
    "ErcViolation",
    "GateReport",
    "GateReportParseError",
    "GateResult",
    "GateRunner",
    "GateStatus",
    "HarnessError",
    "HarnessResult",
    "NetlistError",
    "PartitionDiff",
    "TriageOutcome",
    "TriagePolicy",
    "ams_simulation_cell_gate",
    "compare_partitions",
    "design_netlist_gate",
    "erc_gate",
    "expected_pins_for_member",
    "export_netlist",
    "generate_harness",
    "harness_params",
    "kicad_cli_available",
    "load_report_dict",
    "parse_golden_netlist",
    "parse_kicadxml",
    "partition_equivalence_gate",
    "render_review_gate",
    "report_to_dict",
    "run_cell_simulation",
    "run_erc",
    "simulation_cell_gate",
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
