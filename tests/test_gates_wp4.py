"""WP4 oracle-gate tests: triage policy, netlist parsers, harness params, and
the end-to-end cell-CI gates (ERC + golden-netlist equivalence)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from infersynth.catalog.loader import load_cell
from infersynth.gates import (
    GateStatus,
    TriagePolicy,
    compare_partitions,
    parse_golden_netlist,
    parse_kicadxml,
)
from infersynth.gates.harness import HarnessError, generate_harness, harness_params
from infersynth.gates.netlist import NetlistError
from infersynth.gates.run import run_cell_gates, run_design_gates
from infersynth.gates.triage import ErcViolation

_KICAD_CLI = shutil.which("kicad-cli")
CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "core"
CELL1 = CATALOG / "opamp-gain-noninverting"
CELLX4 = CATALOG / "opamp-gain-x4-noninverting"


# --------------------------------------------------------------------------- #
# golden_netlist.txt parser
# --------------------------------------------------------------------------- #
class TestGoldenParser:
    def test_parses_committed_golden(self):
        part = parse_golden_netlist(CELL1 / "golden_netlist.txt")
        assert part["/FB"] == [("R1", "2"), ("R2", "1"), ("U1", "4")]
        assert part["/IN"] == [("U1", "3")]
        assert set(part) == {"/FB", "/GND", "/IN", "/OUT", "/VCC", "/VEE"}

    def test_comments_and_blanks_ignored(self, tmp_path: Path):
        f = tmp_path / "g.txt"
        f.write_text("# header\n\n/A: R1/1, R2/2\n  \n# trailing\n")
        part = parse_golden_netlist(f)
        assert part == {"/A": [("R1", "1"), ("R2", "2")]}

    def test_multichar_pin_and_ref(self, tmp_path: Path):
        f = tmp_path / "g.txt"
        f.write_text("/N: U12/A5, R3/10\n")
        assert parse_golden_netlist(f)["/N"] == [("U12", "A5"), ("R3", "10")]

    def test_missing_colon_raises(self, tmp_path: Path):
        f = tmp_path / "g.txt"
        f.write_text("R1/1, R2/2\n")
        with pytest.raises(NetlistError, match="missing ':'"):
            parse_golden_netlist(f)

    def test_bad_pin_token_raises(self, tmp_path: Path):
        f = tmp_path / "g.txt"
        f.write_text("/N: R1-1\n")
        with pytest.raises(NetlistError, match="not REF/PIN"):
            parse_golden_netlist(f)

    def test_empty_net_name_raises(self, tmp_path: Path):
        f = tmp_path / "g.txt"
        f.write_text(": R1/1\n")
        with pytest.raises(NetlistError, match="empty net name"):
            parse_golden_netlist(f)


# --------------------------------------------------------------------------- #
# kicadxml parser (with #-ref driver filtering)
# --------------------------------------------------------------------------- #
_XML = """<?xml version="1.0"?>
<export version="E">
  <nets>
    <net code="1" name="/dut/VCC">
      <node ref="U1" pin="5"/>
      <node ref="#FLG01" pin="1"/>
    </net>
    <net code="2" name="/dut/OUT">
      <node ref="R1" pin="1"/>
      <node ref="U1" pin="1"/>
    </net>
    <net code="3" name="all-drivers">
      <node ref="#PWR01" pin="1"/>
    </net>
  </nets>
</export>
"""


class TestKicadXmlParser:
    def test_filters_hash_refs_and_empty_nets(self, tmp_path: Path):
        f = tmp_path / "nl.xml"
        f.write_text(_XML)
        part = parse_kicadxml(f)
        assert part["/dut/VCC"] == [("U1", "5")]  # #FLG01 dropped
        assert part["/dut/OUT"] == [("R1", "1"), ("U1", "1")]
        assert "all-drivers" not in part  # only #PWR01 -> emptied -> omitted

    def test_bad_xml_raises(self, tmp_path: Path):
        f = tmp_path / "nl.xml"
        f.write_text("<not-closed>")
        with pytest.raises(NetlistError):
            parse_kicadxml(f)


# --------------------------------------------------------------------------- #
# triage policy
# --------------------------------------------------------------------------- #
def _v(code, severity):
    return ErcViolation(code=code, severity=severity, description=f"{code} desc")


class TestTriagePolicy:
    def test_default_fails_errors_notes_warnings(self):
        pol = TriagePolicy.default()
        out = pol.triage([_v("power_pin_not_driven", "error"), _v("single_label", "warning")])
        assert not out.ok
        assert [f.code for f in out.failures] == ["power_pin_not_driven"]
        assert any("single_label" in n for n in out.notes)

    def test_allowlisted_code_passes_with_note(self):
        pol = TriagePolicy.from_dict(
            {"allow_codes": {"power_pin_not_driven": "harness drives via PWR_FLAG"}}
        )
        out = pol.triage([_v("power_pin_not_driven", "error")])
        assert out.ok
        assert any("allowlisted power_pin_not_driven" in n for n in out.notes)

    def test_from_dict_list_allowlist_and_custom_severities(self):
        pol = TriagePolicy.from_dict(
            {"fail_severities": ["error", "warning"], "allow_codes": ["x"]}
        )
        out = pol.triage([_v("y", "warning"), _v("x", "warning")])
        assert [f.code for f in out.failures] == ["y"]  # warning now fails
        assert any("allowlisted x" in n for n in out.notes)  # unless allowlisted

    def test_roundtrip_dict(self):
        pol = TriagePolicy.from_dict({"fail_severities": ["error"], "allow_codes": {"a": "b"}})
        assert pol.to_dict() == {"fail_severities": ["error"], "allow_codes": {"a": "b"}}


# --------------------------------------------------------------------------- #
# harness parameter binding
# --------------------------------------------------------------------------- #
class TestHarnessParams:
    def test_midpoint_and_default(self):
        params = harness_params(load_cell(CELL1))
        assert params["gain"] == pytest.approx(500.5)  # midpoint of [1, 1000]
        assert params["rg_ohms"] == 1000.0  # declared default

    def test_first_allowed_and_midpoints(self):
        params = harness_params(load_cell(CELLX4))
        assert params["channels"] == 4  # first (only) allowed value
        assert params["gain1"] == pytest.approx(500.5)
        assert params["rg_ohms"] == 1000.0

    def test_param_without_allowed_default_or_range_raises(self, tmp_path: Path):
        cell = load_cell(CELL1)
        object.__setattr__(cell, "idioms", {"params": {"orphan": {"type": "float"}}})
        with pytest.raises(HarnessError, match="orphan"):
            harness_params(cell)


# --------------------------------------------------------------------------- #
# mutated-netlist failure (moved pin -> gate fails with a moved-pin diagnostic)
# --------------------------------------------------------------------------- #
def test_moved_pin_fails_with_diagnostic():
    golden = parse_golden_netlist(CELL1 / "golden_netlist.txt")
    mutated = {k: list(v) for k, v in golden.items()}
    # Move U1/4 off /FB and onto /GND — a wiring defect the oracle must catch.
    mutated["/FB"] = [p for p in mutated["/FB"] if p != ("U1", "4")]
    mutated["/GND"] = mutated["/GND"] + [("U1", "4")]
    diff = compare_partitions(golden, mutated, "golden", "netlist")
    assert not diff.equivalent
    assert any("only in" in d for d in diff.diagnostics)


# --------------------------------------------------------------------------- #
# end-to-end cell-CI gates (real kicad-cli)
# --------------------------------------------------------------------------- #
@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
@pytest.mark.parametrize("cell_dir", [CELL1, CELLX4], ids=["single", "x4"])
def test_cell_gates_pass_end_to_end(cell_dir):
    report = run_cell_gates(cell_dir)
    assert report.ok, report.summary()
    by_gate = {r.gate: r for r in report.results}
    assert by_gate["validate"].status is GateStatus.PASS
    assert by_gate["erc"].status is GateStatus.PASS  # zero ERC errors on the harness
    assert by_gate["netlist-partition-equivalence"].status is GateStatus.PASS


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_harness_erc_reports_zero_errors(tmp_path: Path):
    from infersynth.gates.erc import run_erc

    result = generate_harness(load_cell(CELL1), tmp_path)
    violations = run_erc(result.root)
    assert [v for v in violations if v.severity == "error"] == []


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_design_gates_against_golden_pass(tmp_path: Path):
    # Drive the arbitrary-design path: harness a cell, then check its root
    # against the cell's golden netlist via run_design_gates.
    result = generate_harness(load_cell(CELL1), tmp_path)
    report = run_design_gates(result.root, golden=CELL1 / "golden_netlist.txt")
    assert report.ok, report.summary()


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_design_gates_detect_mutated_golden(tmp_path: Path):
    # A wrong golden must FAIL the equivalence gate end-to-end.
    result = generate_harness(load_cell(CELL1), tmp_path)
    bad = tmp_path / "bad_golden.txt"
    # OUT should hold R1/1 + U1/1; drop U1/1 so the partitions disagree.
    bad.write_text("/OUT: R1/1\n/IN: U1/3\n")
    report = run_design_gates(result.root, golden=bad)
    assert not report.ok
