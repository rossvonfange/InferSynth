"""NETFLOW stages 4+5 — declared-feeds consumption + design-scope convergence.

Covers :mod:`infersynth.netflow.feeds` (unique-pairing / qualified / ambiguous /
undecided / bundle-mates / cycle-closing) and :mod:`infersynth.netflow.converge`
(unique inference positive case + correct-ambiguity case), plus plan integration
and determinism. The ERC-zero goal-line (a machine-wired multi-cell signal path)
lives at the bottom, kicad-marked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from infersynth.catalog.interfaces import InterfaceDef, InterfaceGroup, RoleDef
from infersynth.gates.erc import run_erc
from infersynth.netflow import build_plan, converge_design, resolve_feeds
from infersynth.spec import FeedEdge
from infersynth.synthesize import synthesize

CORE = Path(__file__).resolve().parent.parent / "catalog"


# --- fakes (extend the stage-1 fakes with interface groups) ----------------
@dataclass
class FakeCell:
    ports: dict[str, dict[str, str]]
    functions: tuple[str, ...] = ()
    interfaces: dict[str, InterfaceGroup] = field(default_factory=dict)


@dataclass
class FakeCatalog:
    cells: dict[str, FakeCell] = field(default_factory=dict)
    interfaces: dict[str, InterfaceDef] = field(default_factory=dict)


@dataclass
class FakeInst:
    instname: str
    cell_key: str
    requirement_id: str


def _p(direction: str, kind: str) -> dict[str, str]:
    return {"direction": direction, "kind": kind}


def _io_cell() -> FakeCell:
    """A plain 1-in/1-out signal cell."""
    return FakeCell({"IN": _p("in", "electrical"), "OUT": _p("out", "electrical")})


# ==========================================================================
# feeds — unique pairing
# ==========================================================================
class TestFeedsUniquePairing:
    def _design(self):
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        return cat, insts

    def test_unique_pair_resolves_to_one_net(self) -> None:
        cat, insts = self._design()
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        assert len(res.nets) == 1
        net = res.nets[0]
        assert net.name == "f_R1_R2"
        assert net.members == (("u_a", "OUT"), ("u_b", "IN"))
        assert res.resolved == (FeedEdge("R1", "R2"),)
        assert res.diagnostics == () and res.requests == ()

    def test_src_anchors_on_chain_tail_dst_on_head(self) -> None:
        # R1 is a two-cell chain a->mid; the feed must leave the TAIL (mid.OUT).
        cat = FakeCatalog({"a": _io_cell(), "mid": _io_cell(), "b": _io_cell()})
        insts = [
            FakeInst("u_a", "a", "R1"),
            FakeInst("u_mid", "mid", "R1"),  # tail of R1 (appended last)
            FakeInst("u_b", "b", "R2"),
        ]
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        assert res.nets[0].members == (("u_b", "IN"), ("u_mid", "OUT"))


# ==========================================================================
# feeds — qualified dst_port
# ==========================================================================
class TestFeedsQualified:
    def test_dst_port_by_name_narrows_multi_input_dst(self) -> None:
        cat = FakeCatalog(
            {
                "a": _io_cell(),
                "b": FakeCell(
                    {
                        "IN1": _p("in", "electrical"),
                        "IN2": _p("in", "electrical"),
                        "OUT": _p("out", "electrical"),
                    }
                ),
            }
        )
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        # unqualified -> ambiguous; qualified with IN2 -> unique.
        assert resolve_feeds((FeedEdge("R1", "R2"),), insts, cat).requests
        res = resolve_feeds((FeedEdge("R1", "R2", "IN2"),), insts, cat)
        assert res.nets[0].members == (("u_a", "OUT"), ("u_b", "IN2"))
        assert res.diagnostics == ()

    def test_dst_port_unknown_is_diagnostic(self) -> None:
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        res = resolve_feeds((FeedEdge("R1", "R2", "NOPE"),), insts, cat)
        assert res.nets == ()
        assert any("feeds_bad_qualifier" in d for d in res.diagnostics)

    def test_dst_port_by_interface_role(self) -> None:
        defs = {"analog": InterfaceDef("analog", {"SIG": RoleDef("bidir")})}
        b = FakeCell(
            {"IN1": _p("in", "electrical"), "IN2": _p("in", "electrical")},
            interfaces={"port": InterfaceGroup("port", "analog", "peripheral", {"SIG": "IN2"})},
        )
        cat = FakeCatalog({"a": _io_cell(), "b": b}, interfaces=defs)
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        # role 'SIG' maps to IN2 -> the feed lands there.
        res = resolve_feeds((FeedEdge("R1", "R2", "SIG"),), insts, cat)
        assert res.nets[0].members == (("u_a", "OUT"), ("u_b", "IN2"))


# ==========================================================================
# feeds — ambiguous + undecided
# ==========================================================================
class TestFeedsAmbiguousUndecided:
    def test_ambiguous_pairing_emits_request_not_a_net(self) -> None:
        cat = FakeCatalog(
            {
                "a": _io_cell(),
                "b": FakeCell(
                    {"IN1": _p("in", "electrical"), "IN2": _p("in", "electrical")}
                ),
            }
        )
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        assert res.nets == ()
        assert len(res.requests) == 1
        req = res.requests[0]
        assert req.kind == "feeds-ambiguous"
        assert {(o.source, o.sink) for o in req.options} == {
            ("u_a.OUT", "u_b.IN1"),
            ("u_a.OUT", "u_b.IN2"),
        }
        assert res.unresolved and res.unresolved[0][0] == FeedEdge("R1", "R2")

    def test_no_producer_is_unresolvable(self) -> None:
        cat = FakeCatalog(
            {"a": FakeCell({"IN": _p("in", "electrical")}), "b": _io_cell()}
        )
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        assert res.nets == () and res.requests == ()
        assert any("feeds_unresolvable" in d for d in res.diagnostics)

    def test_undecided_endpoint_is_diagnostic(self) -> None:
        cat = FakeCatalog({"a": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1")]  # R2 instantiated nothing
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        assert res.nets == ()
        assert any("feeds_undecided" in d for d in res.diagnostics)
        assert res.unresolved[0][0] == FeedEdge("R1", "R2")


# ==========================================================================
# feeds — bundle mates (synthetic interface cells; catalog has none)
# ==========================================================================
class TestFeedsBundleMates:
    def _uart_design(self):
        defs = {"uart": InterfaceDef("uart", {"TX": RoleDef("out"), "RX": RoleDef("in")})}
        a = FakeCell(
            {"ATX": _p("out", "digital"), "ARX": _p("in", "digital")},
            interfaces={"m": InterfaceGroup("m", "uart", "initiator", {"TX": "ATX", "RX": "ARX"})},
        )
        b = FakeCell(
            {"BTX": _p("out", "digital"), "BRX": _p("in", "digital")},
            interfaces={"m": InterfaceGroup("m", "uart", "peripheral", {"TX": "BTX", "RX": "BRX"})},
        )
        cat = FakeCatalog({"a": a, "b": b}, interfaces=defs)
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        return cat, insts

    def test_bundle_pairs_one_net_per_role(self) -> None:
        cat, insts = self._uart_design()
        res = resolve_feeds((FeedEdge("R1", "R2"),), insts, cat)
        names = {n.name for n in res.nets}
        assert names == {"f_R1_R2_TX", "f_R1_R2_RX"}
        by_name = {n.name: n.members for n in res.nets}
        # by-role-name crossover: initiator.map[TX]=ATX pairs peripheral.map[TX]=BTX
        assert by_name["f_R1_R2_TX"] == (("u_a", "ATX"), ("u_b", "BTX"))
        assert by_name["f_R1_R2_RX"] == (("u_a", "ARX"), ("u_b", "BRX"))
        assert res.diagnostics == ()

    def test_bundle_ports_attach_to_right_instance_when_src_is_peripheral(self) -> None:
        # feed from the PERIPHERAL side to the INITIATOR side: ports must still
        # attach to the instance they belong to.
        cat, insts = self._uart_design()
        res = resolve_feeds((FeedEdge("R2", "R1"),), insts, cat)
        by_name = {n.name: n.members for n in res.nets}
        assert by_name["f_R2_R1_TX"] == (("u_a", "ATX"), ("u_b", "BTX"))


# ==========================================================================
# feeds — cycle-closing (declared back-edges are legal)
# ==========================================================================
class TestFeedsCycle:
    def test_declared_cycle_is_accepted(self) -> None:
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        # R1->R2 forward and R2->R1 back-edge (a control loop). Both resolve.
        res = resolve_feeds((FeedEdge("R1", "R2"), FeedEdge("R2", "R1")), insts, cat)
        assert len(res.nets) == 2
        assert len(res.resolved) == 2
        assert res.diagnostics == ()


# ==========================================================================
# convergence — unique positive + correct ambiguity
# ==========================================================================
class TestConverge:
    def test_unique_inference_positive(self) -> None:
        # exactly one free source and one free sink of a kind, different reqs.
        cat = FakeCatalog(
            {
                "src": FakeCell({"OUT": _p("out", "electrical")}),
                "snk": FakeCell({"IN": _p("in", "electrical")}),
            }
        )
        insts = [FakeInst("u_src", "src", "R1"), FakeInst("u_snk", "snk", "R2")]
        res = converge_design(insts, cat)
        assert len(res.nets) == 1
        assert res.nets[0].name == "c_0"
        assert res.nets[0].members == (("u_snk", "IN"), ("u_src", "OUT"))
        assert res.requests == ()

    def test_two_sinks_is_ambiguous_not_inferred(self) -> None:
        cat = FakeCatalog(
            {
                "src": FakeCell({"OUT": _p("out", "electrical")}),
                "snk": FakeCell({"IN": _p("in", "electrical")}),
            }
        )
        insts = [
            FakeInst("u_src", "src", "R1"),
            FakeInst("u_s1", "snk", "R2"),
            FakeInst("u_s2", "snk", "R3"),
        ]
        res = converge_design(insts, cat)
        assert res.nets == ()
        assert len(res.requests) == 1
        assert res.requests[0].kind == "converge-ambiguous"
        assert len(res.requests[0].options) == 2

    def test_already_wired_ports_excluded(self) -> None:
        cat = FakeCatalog(
            {
                "src": FakeCell({"OUT": _p("out", "electrical")}),
                "snk": FakeCell({"IN": _p("in", "electrical")}),
            }
        )
        insts = [FakeInst("u_src", "src", "R1"), FakeInst("u_snk", "snk", "R2")]
        res = converge_design(insts, cat, already_wired={("u_src", "OUT")})
        assert res.nets == () and res.requests == ()

    def test_same_requirement_ports_do_not_converge(self) -> None:
        # a lone in/out pair in the SAME requirement is intra-chain territory,
        # never a cross-requirement convergence inference.
        cat = FakeCatalog({"c": _io_cell()})
        insts = [FakeInst("u_c", "c", "R1")]
        assert converge_design(insts, cat).nets == ()


# ==========================================================================
# plan integration + determinism
# ==========================================================================
class TestPlanIntegration:
    def test_feeds_join_plan_and_convergence_runs_on_residual(self) -> None:
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        plan = build_plan(insts, {"R1": ("a",), "R2": ("b",)}, cat, feeds=(FeedEdge("R1", "R2"),))
        names = {n.name for n in plan.signals}
        assert "f_R1_R2" in names
        # feed wired a.OUT->b.IN; the free ends (a.IN, b.OUT) are one src + one
        # sink of the residual -> convergence uniquely closes them (c_0).
        assert any(n.name == "c_0" for n in plan.signals)
        assert plan.clean

    def test_converge_can_be_disabled(self) -> None:
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell()})
        insts = [FakeInst("u_a", "a", "R1"), FakeInst("u_b", "b", "R2")]
        plan = build_plan(insts, {}, cat, feeds=(FeedEdge("R1", "R2"),), converge=False)
        assert not any(n.name.startswith("c_") for n in plan.signals)
        assert plan.unwired_signal_ports  # residual left for a human

    def test_deterministic_two_runs(self) -> None:
        cat = FakeCatalog({"a": _io_cell(), "b": _io_cell(), "c": _io_cell()})
        insts = [
            FakeInst("u_a", "a", "R1"),
            FakeInst("u_b", "b", "R2"),
            FakeInst("u_c", "c", "R3"),
        ]
        feeds = (FeedEdge("R1", "R2"), FeedEdge("R2", "R3"))
        p1 = build_plan(insts, {}, cat, feeds=feeds)
        p2 = build_plan(insts, {}, cat, feeds=feeds)
        assert p1 == p2


# ==========================================================================
# goal-line — first machine-wired multi-cell signal path, ERC-zero
# ==========================================================================
GOALLINE_FRD = """# Signal-conditioning path, fully wired by declared feeds (NETFLOW goal-line)

- PWR-1 The board shall include a power input connector.
- SIG-1 The board shall include an adc driver. [feeds: SIG-2]
- SIG-2 The board shall include an adc driver. [feeds: SIG-3]
- SIG-3 The board shall include an adc driver. [feeds: SIG-1]
"""


class TestGoalLineFeedsWirePlan:
    """The feeds fully wire the 3-cell signal path (no kicad needed for this)."""

    def test_all_feeds_resolved_and_plan_clean(self, tmp_path: Path) -> None:
        frd = tmp_path / "goalline.md"
        frd.write_text(GOALLINE_FRD, encoding="utf-8")
        r = synthesize(frd, CORE, tmp_path / "b", profile="prototype")
        plan = r.wiring_plan
        assert plan is not None
        # 3 feed nets, one of them the declared cycle-closing back-edge.
        assert {n.name for n in plan.signals} == {
            "f_SIG_1_SIG_2",
            "f_SIG_2_SIG_3",
            "f_SIG_3_SIG_1",
        }
        assert len(plan.feeds_resolved) == 3
        assert plan.feeds_unresolved == ()
        assert plan.resolution_requests == ()
        assert plan.diagnostics == ()
        assert plan.unwired_signal_ports == ()
        assert plan.clean


@pytest.mark.kicad
class TestGoalLineErcZero:
    """The machine-wired multi-cell signal path reports ZERO ERC errors."""

    def test_first_machine_wired_signal_path_is_erc_zero(self, tmp_path: Path) -> None:
        if shutil.which("kicad-cli") is None:
            pytest.skip("kicad-cli not on PATH")
        frd = tmp_path / "goalline.md"
        frd.write_text(GOALLINE_FRD, encoding="utf-8")
        r = synthesize(frd, CORE, tmp_path / "build", profile="prototype")
        assert r.wiring_plan.clean
        errors = [v for v in run_erc(r.root) if v.severity == "error"]
        assert errors == [], [v.one_line() for v in errors]
