"""NETFLOW stage 1 — rails, intra-chain wiring, WiringPlan, emission, ERC delta."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from infersynth.gates.erc import run_erc
from infersynth.netflow import build_plan, intra_chain_nets, resolve_rails
from infersynth.synthesize import synthesize

CORE = Path(__file__).resolve().parent.parent / "catalog"


# --- lightweight fakes so rail/intra rules can be exercised in isolation ---
@dataclass
class FakeCell:
    ports: dict[str, dict[str, str]]
    functions: tuple[str, ...] = ()


@dataclass
class FakeCatalog:
    cells: dict[str, FakeCell] = field(default_factory=dict)


@dataclass
class FakeInst:
    instname: str
    cell_key: str
    requirement_id: str = "R1"


def _p(direction: str, kind: str) -> dict[str, str]:
    return {"direction": direction, "kind": kind}


class TestResolveRails:
    def test_group_by_name_and_undriven_diagnostic(self) -> None:
        cat = FakeCatalog(
            {
                "amp": FakeCell(
                    {"VCC": _p("in", "power"), "VEE": _p("in", "power"),
                     "IN": _p("in", "electrical")}
                ),
                "dec": FakeCell({"VCC": _p("passive", "power")}),
            }
        )
        insts = [FakeInst("u_amp", "amp"), FakeInst("u_dec", "dec")]
        plan = resolve_rails(insts, cat)
        # VCC groups both instances by name; VEE is amp-only.
        assert plan.nets["VCC"] == (("u_amp", "VCC"), ("u_dec", "VCC"))
        assert plan.nets["VEE"] == (("u_amp", "VEE"),)
        # no source anywhere -> every rail undriven, loud diagnostic each.
        assert plan.driven == frozenset()
        assert len(plan.diagnostics) == 2
        assert all("undriven_rail" in d for d in plan.diagnostics)
        # signal ports are not rails.
        assert "IN" not in plan.nets

    def test_connector_passive_power_is_a_source(self) -> None:
        cat = FakeCatalog(
            {
                "conn": FakeCell(
                    {"VIN": _p("passive", "power"), "GND": _p("passive", "power")},
                    functions=("connectivity",),
                ),
                "amp": FakeCell({"GND": _p("in", "power")}),
            }
        )
        plan = resolve_rails([FakeInst("u_conn", "conn"), FakeInst("u_amp", "amp")], cat)
        assert plan.driven == frozenset({"VIN", "GND"})
        assert plan.diagnostics == ()

    def test_out_direction_power_port_is_a_source(self) -> None:
        cat = FakeCatalog({"reg": FakeCell({"VOUT": _p("out", "power")})})
        plan = resolve_rails([FakeInst("u_reg", "reg")], cat)
        assert plan.driven == frozenset({"VOUT"})

    def test_decoupling_passive_is_not_a_source(self) -> None:
        # passive power port on a NON-connector cell does not source a rail.
        cat = FakeCatalog({"dec": FakeCell({"VCC": _p("passive", "power")})})
        plan = resolve_rails([FakeInst("u", "dec")], cat)
        assert plan.driven == frozenset()
        assert len(plan.diagnostics) == 1


class TestIntraChain:
    def test_unique_pairing(self) -> None:
        cat = FakeCatalog(
            {
                "a": FakeCell({"IN": _p("in", "electrical"), "OUT": _p("out", "electrical")}),
                "b": FakeCell({"IN": _p("in", "electrical"), "OUT": _p("out", "electrical")}),
            }
        )
        chain = (("u_a", "a"), ("u_b", "b"))
        pairs, diags = intra_chain_nets("R1", chain, cat)
        assert pairs == [(("u_a", "OUT"), ("u_b", "IN"))]
        assert diags == []

    def test_ambiguous_multiple_ins(self) -> None:
        cat = FakeCatalog(
            {
                "a": FakeCell({"OUT": _p("out", "electrical")}),
                "b": FakeCell(
                    {"IN1": _p("in", "electrical"), "IN2": _p("in", "electrical")}
                ),
            }
        )
        pairs, diags = intra_chain_nets("R1", (("u_a", "a"), ("u_b", "b")), cat)
        assert pairs == []
        assert len(diags) == 1
        assert "ambiguous_pairing" in diags[0]
        assert "IN1" in diags[0] and "IN2" in diags[0]

    def test_ambiguous_no_output(self) -> None:
        cat = FakeCatalog(
            {
                "a": FakeCell({"IN": _p("in", "electrical")}),  # no producing port
                "b": FakeCell({"IN": _p("in", "electrical")}),
            }
        )
        pairs, diags = intra_chain_nets("R1", (("u_a", "a"), ("u_b", "b")), cat)
        assert pairs == []
        assert len(diags) == 1
        assert "(none)" in diags[0]


class TestBuildPlan:
    def _fixture(self):
        cat = FakeCatalog(
            {
                "a": FakeCell(
                    {"IN": _p("in", "electrical"), "OUT": _p("out", "electrical"),
                     "VCC": _p("in", "power")}
                ),
                "b": FakeCell(
                    {"IN": _p("in", "electrical"), "OUT": _p("out", "electrical"),
                     "VCC": _p("in", "power")}
                ),
            }
        )
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R1")]
        chains = {"R1": ("a", "b")}
        return cat, insts, chains

    def test_signal_net_naming_and_rails(self) -> None:
        cat, insts, chains = self._fixture()
        plan = build_plan(insts, chains, cat)
        sig = plan.signals
        assert len(sig) == 1
        assert sig[0].name == "n_R1_0"
        assert sig[0].members == (("u_a", "OUT"), ("u_b", "IN"))
        assert sig[0].driven is True
        # the interior link is wired; the chain's free endpoints (a.IN input,
        # b.OUT output) legitimately remain residual, the rest do not.
        assert plan.unwired_signal_ports == (("u_a", "IN"), ("u_b", "OUT"))
        assert {n.name for n in plan.rails} == {"VCC"}

    def test_deterministic(self) -> None:
        cat, insts, chains = self._fixture()
        assert build_plan(insts, chains, cat) == build_plan(insts, chains, cat)

    def test_single_cell_winner_no_signal_net(self) -> None:
        cat, insts, _ = self._fixture()
        # no multi-cell chain -> no intra net; both signal ports stay residual.
        plan = build_plan(insts, {"R1": ("a",)}, cat)
        assert plan.signals == ()
        assert ("u_a", "IN") in plan.unwired_signal_ports
        assert ("u_a", "OUT") in plan.unwired_signal_ports


DEMO_FRD = """# Demo sensor board

- The board shall include a non-inverting amplifier gain stage with gain of 4.
- The board shall include a voltage reference.
- The board shall include decoupling.
- The board shall include an adc driver.
- The board shall include a 4-wire sensor input connector.
"""

PWR_FRD = """# Power board

- The board shall include a power input connector.
- The board shall include decoupling.
"""


class TestSynthesizeWiringPlanDeterminism:
    def test_plan_identical_across_runs(self, tmp_path: Path) -> None:
        frd = tmp_path / "demo.md"
        frd.write_text(DEMO_FRD, encoding="utf-8")
        r1 = synthesize(frd, CORE, tmp_path / "a", profile="prototype")
        r2 = synthesize(frd, CORE, tmp_path / "b", profile="prototype")
        assert r1.wiring_plan == r2.wiring_plan
        assert r1.wiring_plan is not None
        assert r1.wiring_plan.rails  # rails wired even with single-cell winners
        assert r1.wiring_plan.signals == ()  # independent winners -> no chains

    def test_no_wiring_flag(self, tmp_path: Path) -> None:
        frd = tmp_path / "demo.md"
        frd.write_text(DEMO_FRD, encoding="utf-8")
        r = synthesize(frd, CORE, tmp_path / "b", profile="prototype", wiring=False)
        assert r.wiring_plan is None
        assert "--no-wiring" in r.report_path.read_text(encoding="utf-8")


@pytest.mark.kicad
class TestEmission:
    def test_driven_design_wires_and_parses(self, tmp_path: Path) -> None:
        if shutil.which("kicad-cli") is None:
            pytest.skip("kicad-cli not on PATH")
        frd = tmp_path / "pwr.md"
        frd.write_text(PWR_FRD, encoding="utf-8")
        out = tmp_path / "build"
        r = synthesize(frd, CORE, out, profile="prototype")
        txt = r.root.read_text(encoding="utf-8")
        # sheet pins + coincident net-named global labels + a PWR_FLAG per driven rail
        assert '(pin "GND"' in txt
        assert '(global_label "GND"' in txt
        assert '(global_label "VIN"' in txt
        assert 'lib_id "power:PWR_FLAG"' in txt
        assert 'symbol "power:PWR_FLAG"' in txt  # lib symbol injected once
        # driven rails (VIN, GND) -> one PWR_FLAG each
        assert txt.count('lib_id "power:PWR_FLAG"') == 2
        proc = subprocess.run(
            ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
             "-o", str(out / "net.xml"), str(r.root)],
            capture_output=True, text=True, timeout=120,
        )
        assert (out / "net.xml").exists(), proc.stderr
        assert "Failed to load" not in proc.stderr

    def test_rail_wiring_drops_erc_errors(self, tmp_path: Path) -> None:
        if shutil.which("kicad-cli") is None:
            pytest.skip("kicad-cli not on PATH")
        frd = tmp_path / "demo.md"
        frd.write_text(DEMO_FRD, encoding="utf-8")
        baseline = synthesize(frd, CORE, tmp_path / "nowire", profile="prototype", wiring=False)
        wired = synthesize(frd, CORE, tmp_path / "wire", profile="prototype", wiring=True)

        def errs(root: Path) -> int:
            return sum(1 for v in run_erc(root) if v.severity == "error")

        base_err = errs(baseline.root)
        wired_err = errs(wired.root)
        # rails get wired even though the single-cell winners leave signal ports
        # unwired -> ERC error count must strictly drop.
        assert wired_err < base_err, f"baseline={base_err} wired={wired_err}"
