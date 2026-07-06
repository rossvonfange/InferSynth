"""Gate orchestration for the CLI (BUILD_PLAN WP4).

Two entry points:

* :func:`run_cell_gates` — the cell-CI flow: validate the cell package,
  generate its ERC harness, run ERC on the harness root, and prove the
  harness netlist is partition-equivalent to the cell's ``golden_netlist.txt``.
* :func:`run_design_gates` — run ERC on an arbitrary design root and,
  optionally, compare its netlist to a golden partition source.

Both return a :class:`GateReport`; the CLI prints its summary and exits nonzero
when any gate fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from infersynth.catalog.loader import CellPackage, load_cell
from infersynth.gates.erc import erc_gate
from infersynth.gates.harness import generate_harness
from infersynth.gates.netlist import export_netlist, parse_golden_netlist
from infersynth.gates.netlist_equiv import partition_equivalence_gate
from infersynth.gates.runner import GateReport, GateResult
from infersynth.gates.triage import TriagePolicy

__all__ = ["run_cell_gates", "run_design_gates"]


def _netlist_gate(schematic_root: Path, golden_partition) -> GateResult:
    """Export *schematic_root*'s netlist and compare it to *golden_partition*."""
    netlist_partition = export_netlist(schematic_root)
    # The existing partition gate compares modulo net naming; feed golden as the
    # reference ("ir_partition") and the exported netlist as the candidate.
    return partition_equivalence_gate(
        {"ir_partition": golden_partition, "netlist_partition": netlist_partition}
    )


def run_cell_gates(
    cell: str | Path | CellPackage, policy: TriagePolicy | None = None
) -> GateReport:
    """Validate a cell, harness it, and run ERC + golden-netlist equivalence."""
    report = GateReport()
    if isinstance(cell, CellPackage):
        pkg = cell
        report.results.append(GateResult.passed("validate", f"{pkg.key} (pre-loaded)"))
    else:
        pkg = load_cell(cell)  # raises CellPackageError on invalid cell
        report.results.append(GateResult.passed("validate", f"{pkg.key} valid"))

    policy = policy or TriagePolicy.default()
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_harness(pkg, tmp)
        report.results.append(erc_gate({"schematic_path": result.root, "triage_policy": policy}))

        golden_name = pkg.verification.get("golden_netlist")
        if not golden_name:
            report.results.append(
                GateResult.skipped(
                    "netlist-partition-equivalence",
                    "cell declares no verification.golden_netlist",
                )
            )
        else:
            golden = parse_golden_netlist(pkg.path / golden_name)
            report.results.append(_netlist_gate(result.root, golden))
    return report


def run_design_gates(
    root: str | Path,
    golden: str | Path | None = None,
    policy: TriagePolicy | None = None,
) -> GateReport:
    """Run ERC on an arbitrary design *root*; optionally compare to *golden*."""
    root = Path(root)
    policy = policy or TriagePolicy.default()
    report = GateReport()
    report.results.append(erc_gate({"schematic_path": root, "triage_policy": policy}))
    if golden is not None:
        golden_partition = parse_golden_netlist(golden)
        report.results.append(_netlist_gate(root, golden_partition))
    else:
        report.results.append(
            GateResult.skipped(
                "netlist-partition-equivalence", "no --golden partition supplied"
            )
        )
    return report
