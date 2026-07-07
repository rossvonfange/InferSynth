"""Tests for the design-level netlist partition-equivalence gate (round 2 /
SEED_PLAN §2, design scope): infersynth.gates.design_netlist.

Layout mirrors tests/test_gates_wp4.py: correspondence/serialization units run
everywhere; anything that exports a real netlist is @pytest.mark.kicad.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.gates.design_netlist import (
    design_netlist_gate,
    expected_pins_for_member,
    plan_from_dict,
    plan_to_dict,
)
from infersynth.gates.run import run_design_gates
from infersynth.gates.runner import GateStatus
from infersynth.netflow.plan import Net, WiringPlan
from infersynth.spec import load_spec
from infersynth.synthesize import SynthesisResult, synthesize

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "catalog"
FRDS = REPO / "examples" / "frds"
_KICAD_CLI = shutil.which("kicad-cli")

# Two single-cell winners with rail ports only -> a small design whose plan is
# exactly the VCC/GND rail nets (test_synthesize.py's demo FRD, minus the amp
# chain — keeps the kicad-marked tests fast).
TWO_CELL_FRD = """# Two-cell rail board

- The board shall include a voltage reference.
- The board shall include decoupling.
"""


def _instance_cells(result: SynthesisResult) -> dict[str, str]:
    return {inst.instname: inst.cell_key for inst in result.instantiated}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CORE)


@pytest.fixture(scope="module")
def two_cell(tmp_path_factory: pytest.TempPathFactory) -> SynthesisResult:
    tmp = tmp_path_factory.mktemp("two_cell")
    frd = tmp / "two_cell.md"
    frd.write_text(TWO_CELL_FRD, encoding="utf-8")
    return synthesize(frd, CORE, tmp / "build", profile="prototype")


# ---------------------------------------------------------------------------
# port -> pin correspondence (no kicad-cli needed: pure golden-netlist lookup)
# ---------------------------------------------------------------------------


class TestExpectedPinsForMember:
    def test_port_resolves_to_golden_pins(self, catalog: Catalog) -> None:
        # decoupling's golden_netlist.txt: /VCC -> C1/1, /GND -> C1/2.
        cells = {"dec_01": "core/decoupling@0.1.0"}
        assert expected_pins_for_member("dec_01", "VCC", cells, catalog) == (("C1", "1"),)
        assert expected_pins_for_member("dec_01", "GND", cells, catalog) == (("C1", "2"),)

    def test_port_with_multiple_pins(self, catalog: Catalog) -> None:
        # vref-shunt's /VREF hier label sits on three pins (R1/2, U1/1, U1/2):
        # one member -> the full pin set, not just one pin.
        cells = {"ref_01": "core/vref-shunt@0.1.0"}
        pins = expected_pins_for_member("ref_01", "VREF", cells, catalog)
        assert pins is not None
        assert set(pins) == {("R1", "2"), ("U1", "1"), ("U1", "2")}

    def test_unresolvable_members_return_none(self, catalog: Catalog) -> None:
        cells = {"dec_01": "core/decoupling@0.1.0"}
        assert expected_pins_for_member("ghost", "VCC", cells, catalog) is None
        assert expected_pins_for_member("dec_01", "NO_SUCH_PORT", cells, catalog) is None
        assert (
            expected_pins_for_member("bad", "VCC", {"bad": "core/nope@9.9.9"}, catalog)
            is None
        )


class TestPlanRoundTrip:
    def test_to_dict_from_dict(self) -> None:
        plan = WiringPlan(
            nets=(
                Net(kind="rail", name="VCC", driven=True,
                    members=(("ref_01", "VCC"), ("dec_01", "VCC"))),
                Net(kind="signal", name="N_ref_out", driven=True,
                    members=(("ref_01", "VREF"),)),
            ),
            diagnostics=(),
            unwired_signal_ports=(),
        )
        cells = {"ref_01": "core/vref-shunt@0.1.0", "dec_01": "core/decoupling@0.1.0"}
        data = plan_to_dict(plan, cells)
        plan2, cells2 = plan_from_dict(data)
        assert cells2 == cells
        assert [(n.kind, n.name, n.members) for n in plan2.nets] == [
            (n.kind, n.name, n.members) for n in plan.nets
        ]


# ---------------------------------------------------------------------------
# the gate itself against real exports (kicad-cli)
# ---------------------------------------------------------------------------


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
class TestDesignNetlistGate:
    def test_clean_design_passes(self, two_cell: SynthesisResult, catalog: Catalog) -> None:
        plan = two_cell.wiring_plan
        assert plan is not None and plan.rails
        result = design_netlist_gate(two_cell.root, plan, _instance_cells(two_cell), catalog)
        assert result.status == GateStatus.PASS, result.diagnostics
        assert f"{len(plan.nets)} plan net(s)" in result.diagnostics[0]

    def test_disconnected_members_fail_named(
        self, two_cell: SynthesisResult, catalog: Catalog
    ) -> None:
        # Mutate the plan: merge two really-separate rails (VCC + GND) into one
        # claimed net. Their pins cannot all be mutually connected in the
        # export, and the diagnostic must name the exact net and members.
        plan = two_cell.wiring_plan
        assert plan is not None
        rails = {n.name: n for n in plan.rails}
        merged = Net(
            kind="rail",
            name="VCC",
            driven=True,
            members=rails["VCC"].members + rails["GND"].members,
        )
        bad = WiringPlan(nets=(merged,), diagnostics=(), unwired_signal_ports=())
        result = design_netlist_gate(two_cell.root, bad, _instance_cells(two_cell), catalog)
        assert result.status == GateStatus.FAIL
        assert "'VCC'" in result.diagnostics[0]
        assert "not mutually connected" in result.diagnostics[0]

    def test_unresolvable_member_fails_named(
        self, two_cell: SynthesisResult, catalog: Catalog
    ) -> None:
        plan = two_cell.wiring_plan
        assert plan is not None
        vcc = next(n for n in plan.rails if n.name == "VCC")
        mutated = Net(
            kind="rail", name="VCC", driven=True,
            members=vcc.members + (("ghost_99", "VCC"),),
        )
        bad = WiringPlan(nets=(mutated,), diagnostics=(), unwired_signal_ports=())
        result = design_netlist_gate(two_cell.root, bad, _instance_cells(two_cell), catalog)
        assert result.status == GateStatus.FAIL
        assert "ghost_99.VCC" in result.diagnostics[0]

    def test_run_design_gates_includes_and_skips(
        self, two_cell: SynthesisResult, catalog: Catalog
    ) -> None:
        # supplied -> the gate runs inside run_design_gates; omitted -> LOUD skip.
        plan = two_cell.wiring_plan
        assert plan is not None
        report = run_design_gates(
            two_cell.root, wiring_plan=plan,
            instance_cells=_instance_cells(two_cell), catalog=catalog,
        )
        by_name = {r.gate: r for r in report.results}
        assert by_name["design-netlist-partition-equivalence"].status == GateStatus.PASS
        report = run_design_gates(two_cell.root)
        by_name = {r.gate: r for r in report.results}
        assert by_name["design-netlist-partition-equivalence"].status == GateStatus.SKIPPED


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
class TestBridgeSenseEndToEnd:
    def test_design_netlist_gate_passes(self, tmp_path: Path) -> None:
        # Self-contained: the FRD+spec are COPIED into tmp as of this branch
        # and synthesized from there, so concurrent edits to the shared
        # examples/frds/ files between collection and run can't skew the test.
        for name in ("07_bridgesense_1.md", "07_bridgesense_1.spec.yaml"):
            shutil.copy(FRDS / name, tmp_path / name)
        spec = load_spec(tmp_path / "07_bridgesense_1.spec.yaml")
        assert spec.frd is not None
        result = synthesize(
            spec.frd, CORE, tmp_path / "build",
            profile=spec.profile or "prototype",
            rail_aliases=dict(spec.rail_aliases) if spec.rail_aliases else None,
            verify=True,
        )
        assert result.all_decided and not result.skipped
        plan = result.wiring_plan
        assert plan is not None
        assert not plan.diagnostics and not plan.unwired_signal_ports

        # the synthesize() verify path already ran + reported the gate ...
        assert result.design_netlist_summary is not None
        assert result.design_netlist_summary.startswith("[pass]")
        report_md = result.report_path.read_text(encoding="utf-8")
        assert "Design-level netlist partition-equivalence" in report_md

        # ... and the persisted wiring_plan.json round-trips through the
        # gates --design path to the same PASS.
        plan_json = tmp_path / "build" / "wiring_plan.json"
        assert plan_json.is_file()
        import json

        plan2, cells2 = plan_from_dict(json.loads(plan_json.read_text(encoding="utf-8")))
        gate = design_netlist_gate(result.root, plan2, cells2, Catalog.load(CORE))
        assert gate.status == GateStatus.PASS, gate.diagnostics
        assert f"{len(plan.nets)} plan net(s)" in gate.diagnostics[0]
