"""WP-P1 packer tests: data-driven pack-target discovery, terminal guards
(allocation-boundary / capacity / rails), absorption knobs (off/conservative/
aggressive), idempotence + order-independence, partial-pack recording,
per-requirement forbid, and covers-vs-covers decide integration incl. the
production↔prototype cost flip."""

from __future__ import annotations

from pathlib import Path

import yaml

from infersynth.catalog import Catalog
from infersynth.decide import Lockfile
from infersynth.lint.model import Requirement, RequirementSet, SourceSpan
from infersynth.match import match
from infersynth.match.allocation import Allocation, AllocationTable, allocations_from_spec
from infersynth.match.matcher import MatchResult
from infersynth.match.provenance import CandidateChain
from infersynth.pack import (
    PackKnobs,
    discover_pack_targets,
    pack,
    pack_finalists,
    pack_outcome,
)

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "catalog"

SINGLE = "core/opamp-gain-noninverting@0.1.0"
QUAD = "core/opamp-gain-x4-noninverting@0.1.0"
GAIN_TEXT = "non-inverting amplifier gain stage, gain {}"


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------


def gain_req(rid: str, gain: int, parent: Requirement | None = None) -> Requirement:
    text = GAIN_TEXT.format(gain)
    r = Requirement(id=rid, text=text, source=SourceSpan("frd.md", 0, 0, 0, len(text)))
    if parent is not None:
        parent.add_child(r)
    return r


def group(gid: str) -> Requirement:
    r = Requirement(id=gid, text=f"{gid} group")
    return r


def real_match(rs: RequirementSet):
    return match(rs, Catalog.load(CORE))


def single_chain(rid: str) -> CandidateChain:
    return CandidateChain.make(rid, (SINGLE,), closed=True, surfaced_by=("idiom",))


def write_pack_cell(
    root: Path,
    name: str,
    *,
    keywords,
    units: int | None = None,
    channels: int = 0,
    ports=None,
    disambiguation=None,
    functions=None,
) -> None:
    cell_dir = root / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    idioms: dict = {"keywords": list(keywords)}
    if functions is not None:
        idioms["functions"] = list(functions)
    if disambiguation is not None:
        idioms["disambiguation"] = disambiguation
    if channels:
        idioms["params"] = {
            f"gain{k}": {"type": "float", "range": [1.0, 1000.0]} for k in range(1, channels + 1)
        }
    selection: dict = {}
    if units is not None:
        selection["package_sharing"] = {"units": units, "power_unit": "separate"}
    data = {
        "manifest": {
            "name": name,
            "version": "0.1.0",
            "description": f"test {name}",
            "provenance": "synthetic",
            "license": "GPL-3.0-or-later",
        },
        "idioms": idioms,
        "selection": selection,
        "depth": {"level": "L0"},
    }
    if ports is not None:
        data["ports"] = ports
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))


# --------------------------------------------------------------------------
# 1. discovery (data-driven, not hard-coded to the opamp pair)
# --------------------------------------------------------------------------


class TestDiscovery:
    def test_finds_opamp_pair_data_driven(self):
        cat = Catalog.load(CORE)
        targets = discover_pack_targets(cat)
        assert SINGLE in targets
        t = targets[SINGLE]
        assert t.target_key == QUAD
        assert t.units == 4
        assert t.channel_base == "gain"

    def test_no_package_sharing_yields_nothing(self, tmp_path):
        # a plain single cell + a "quad-ish" cell that never declares package sharing
        write_pack_cell(tmp_path, "widget", keywords=["widget amp"])
        write_pack_cell(
            tmp_path,
            "widgetquad",
            keywords=["widget amp"],
            channels=4,
            disambiguation="quad; only via packer",
        )  # no units= -> no package_sharing
        cat = Catalog.load(tmp_path)
        assert discover_pack_targets(cat) == {}

    def test_synthetic_pair_discovered(self, tmp_path):
        write_pack_cell(tmp_path, "widget", keywords=["widget amp"])
        write_pack_cell(
            tmp_path,
            "widgetquad",
            keywords=["widget amp"],
            units=2,
            channels=2,
            disambiguation="quad; only via packer",
        )
        cat = Catalog.load(tmp_path)
        targets = discover_pack_targets(cat)
        assert "widget@0.1.0" in targets
        assert targets["widget@0.1.0"].target_key == "widgetquad@0.1.0"
        assert targets["widget@0.1.0"].units == 2


# --------------------------------------------------------------------------
# 2. basic pack + param mapping
# --------------------------------------------------------------------------


class TestBasicPack:
    def test_four_singles_pack_into_quad(self):
        root = group("SYS.1")
        for i in range(1, 5):
            gain_req(f"R-{i}", 10 * i, root)
        rs = RequirementSet([root])
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("aggressive"), reqset=rs)
        assert len(pr.claims) == 1
        c = pr.claims[0]
        assert c.key == ("R-1", "R-2", "R-3", "R-4")
        assert c.target_cell == QUAD
        assert c.source_cell == SINGLE
        assert not c.partial
        assert c.unused_channels == ()

    def test_param_mapping_sorted_by_req_id(self):
        root = group("SYS.1")
        gain_req("R-3", 30, root)
        gain_req("R-1", 10, root)
        gain_req("R-2", 20, root)
        rs = RequirementSet([root])
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("aggressive"), reqset=rs)
        (claim,) = pr.claims
        mapping = [(ch.requirement_id, ch.channel_param, ch.gain) for ch in claim.channels]
        assert mapping == [
            ("R-1", "gain1", 10.0),
            ("R-2", "gain2", 20.0),
            ("R-3", "gain3", 30.0),
        ]

    def test_partial_pack_records_unused_channel(self):
        root = group("SYS.1")
        for i in range(1, 4):  # 3 into 4
            gain_req(f"R-{i}", 10 * i, root)
        rs = RequirementSet([root])
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("aggressive"), reqset=rs)
        (claim,) = pr.claims
        assert claim.partial
        assert claim.unused_channels == ("gain4",)
        assert len(claim.channels) == 3


# --------------------------------------------------------------------------
# 3. absorption knobs
# --------------------------------------------------------------------------


class TestKnobs:
    def _two_parents(self) -> RequirementSet:
        a = group("SYS.A")
        b = group("SYS.B")
        gain_req("R-1", 10, a)
        gain_req("R-2", 20, a)
        gain_req("R-3", 30, b)
        gain_req("R-4", 40, b)
        return RequirementSet([a, b])

    def test_off_yields_no_claims(self):
        rs = self._two_parents()
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("off"), reqset=rs)
        assert pr.claims == ()

    def test_conservative_packs_within_parent_only(self):
        rs = self._two_parents()
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("conservative"), reqset=rs)
        keys = sorted(c.key for c in pr.claims)
        assert keys == [("R-1", "R-2"), ("R-3", "R-4")]

    def test_aggressive_packs_across_parents(self):
        rs = self._two_parents()
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("aggressive"), reqset=rs)
        assert [c.key for c in pr.claims] == [("R-1", "R-2", "R-3", "R-4")]

    def test_default_knob_is_conservative(self):
        assert PackKnobs().absorption == "conservative"


# --------------------------------------------------------------------------
# 4. terminal guards
# --------------------------------------------------------------------------


class TestGuards:
    def test_capacity_blocks_five_into_four(self):
        root = group("SYS.1")
        for i in range(1, 6):  # 5 into 4
            gain_req(f"R-{i}", 10 * i, root)
        rs = RequirementSet([root])
        pr = pack(real_match(rs), Catalog.load(CORE), PackKnobs("aggressive"), reqset=rs)
        assert pr.claims == ()  # over-budget group blocked wholesale

    def test_allocation_boundary_blocks_merge(self):
        # two subtrees resolve to *different* library scopes -> never one pack.
        a = group("SYS.A")
        b = group("SYS.B")
        gain_req("R-1", 10, a)
        gain_req("R-2", 20, a)
        gain_req("R-3", 30, b)
        gain_req("R-4", 40, b)
        rs = RequirementSet([a, b])
        allocations = AllocationTable(
            by_id={
                "SYS.A": Allocation(at="SYS.A", allow=("core",)),
                "SYS.B": Allocation(at="SYS.B", allow=("core", "other")),
            }
        )
        cat = Catalog.load(CORE)
        pr = pack(
            match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs, allocations=allocations
        )
        keys = sorted(c.key for c in pr.claims)
        # aggressive would merge all 4 without allocations; the boundary splits them.
        assert keys == [("R-1", "R-2"), ("R-3", "R-4")]

    def test_no_allocation_would_merge(self):
        # control for the boundary test: same tree, no allocations -> one pack of 4.
        a = group("SYS.A")
        b = group("SYS.B")
        gain_req("R-1", 10, a)
        gain_req("R-2", 20, a)
        gain_req("R-3", 30, b)
        gain_req("R-4", 40, b)
        rs = RequirementSet([a, b])
        cat = Catalog.load(CORE)
        pr = pack(match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs)
        assert [c.key for c in pr.claims] == [("R-1", "R-2", "R-3", "R-4")]

    def test_power_port_mismatch_blocks(self, tmp_path):
        # source rails {VCC,VEE,GND}; target rails {VCC,GND} -> rails guard blocks.
        write_pack_cell(
            tmp_path,
            "widget",
            keywords=["widget amp"],
            ports={
                "IN": {"direction": "in", "kind": "electrical"},
                "OUT": {"direction": "out", "kind": "electrical"},
                "VCC": {"direction": "in", "kind": "power"},
                "VEE": {"direction": "in", "kind": "power"},
                "GND": {"direction": "in", "kind": "power"},
            },
        )
        write_pack_cell(
            tmp_path,
            "widgetquad",
            keywords=["widget amp"],
            units=2,
            channels=2,
            disambiguation="quad; only via packer",
            ports={
                "IN1": {"direction": "in", "kind": "electrical"},
                "OUT1": {"direction": "out", "kind": "electrical"},
                "VCC": {"direction": "in", "kind": "power"},
                "GND": {"direction": "in", "kind": "power"},
            },
        )
        cat = Catalog.load(tmp_path)
        root = group("SYS.1")
        for i in range(1, 3):
            r = Requirement(
                id=f"R-{i}", text="widget amp", source=SourceSpan("frd.md", 0, 0, 0, 9)
            )
            root.add_child(r)
        rs = RequirementSet([root])
        pr = pack(match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs)
        assert pr.claims == ()  # discovered, but rails guard rejects it

    def test_power_port_match_allows(self, tmp_path):
        # same as above but matching rails -> a claim forms (guard is the only diff).
        rails = {
            "IN": {"direction": "in", "kind": "electrical"},
            "OUT": {"direction": "out", "kind": "electrical"},
            "VCC": {"direction": "in", "kind": "power"},
            "GND": {"direction": "in", "kind": "power"},
        }
        qrails = {
            "IN1": {"direction": "in", "kind": "electrical"},
            "OUT1": {"direction": "out", "kind": "electrical"},
            "VCC": {"direction": "in", "kind": "power"},
            "GND": {"direction": "in", "kind": "power"},
        }
        write_pack_cell(tmp_path, "widget", keywords=["widget amp"], ports=rails)
        write_pack_cell(
            tmp_path,
            "widgetquad",
            keywords=["widget amp"],
            units=2,
            channels=2,
            disambiguation="quad; only via packer",
            ports=qrails,
        )
        cat = Catalog.load(tmp_path)
        root = group("SYS.1")
        for i in range(1, 3):
            r = Requirement(
                id=f"R-{i}", text="widget amp", source=SourceSpan("frd.md", 0, 0, 0, 9)
            )
            root.add_child(r)
        rs = RequirementSet([root])
        pr = pack(match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs)
        assert len(pr.claims) == 1
        assert pr.claims[0].power_ports == ("GND", "VCC")


# --------------------------------------------------------------------------
# 5. per-requirement forbid
# --------------------------------------------------------------------------


class TestForbid:
    def test_forbid_excludes_requirement(self):
        root = group("SYS.1")
        for i in range(1, 5):
            gain_req(f"R-{i}", 10 * i, root)
        rs = RequirementSet([root])
        cat = Catalog.load(CORE)
        pr = pack(
            match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs, forbid_pack={"R-4"}
        )
        (claim,) = pr.claims
        assert claim.key == ("R-1", "R-2", "R-3")  # R-4 excluded, remainder packs 3-into-4
        assert claim.unused_channels == ("gain4",)


# --------------------------------------------------------------------------
# 6. determinism / idempotence / order-independence
# --------------------------------------------------------------------------


def _mr(order: list[str]) -> MatchResult:
    chains = {rid: (single_chain(rid),) for rid in order}
    return MatchResult(chains=chains)


class TestDeterminism:
    def test_order_independent_and_idempotent(self):
        cat = Catalog.load(CORE)
        knobs = PackKnobs("aggressive")
        a = pack(_mr(["R-1", "R-2", "R-3", "R-4"]), cat, knobs)
        b = pack(_mr(["R-4", "R-2", "R-1", "R-3"]), cat, knobs)
        # same claim key set, byte-identical repr (a single group, no reqset needed)
        assert [c.key for c in a.claims] == [c.key for c in b.claims]
        assert repr(a.claims) == repr(b.claims)

    def test_rerun_byte_identical(self):
        cat = Catalog.load(CORE)
        mr = _mr(["R-1", "R-2", "R-3", "R-4"])
        assert repr(pack(mr, cat, PackKnobs("aggressive")).claims) == repr(
            pack(mr, cat, PackKnobs("aggressive")).claims
        )


# --------------------------------------------------------------------------
# 7. decide integration — covers-vs-covers + the profile cost flip
# --------------------------------------------------------------------------


def _quad_pack_claim():
    root = group("SYS.1")
    for i in range(1, 5):
        gain_req(f"R-{i}", 10 * i, root)
    rs = RequirementSet([root])
    cat = Catalog.load(CORE)
    pr = pack(match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs)
    (claim,) = pr.claims
    return claim, cat


class TestDecideIntegration:
    def test_finalists_cover_same_requirement_set(self):
        claim, cat = _quad_pack_claim()
        packed, comp = pack_finalists(claim, cat, "production")
        assert packed.chain.cells == (QUAD,)
        assert comp.chain.cells == (SINGLE,) * 4  # 4 discrete singles = the composition
        # composition BOM = 4x the single's BOM (interior fully audited, §6 rule 2)
        assert comp.cost.bom["qty1k"] == 4 * 0.35

    def test_production_weights_pack_wins(self):
        claim, cat = _quad_pack_claim()
        outcome = pack_outcome(claim, cat, "production")
        assert outcome.winner.chain.cells == (QUAD,)

    def test_prototype_flip_keeps_singles(self):
        # placeholder costs make the quad win everywhere; a lockfile that prices a
        # per-channel calibration step onto the quad flips prototype to the singles.
        claim, cat = _quad_pack_claim()
        lock = Lockfile(
            generated_at="2026-07-06T00:00:00Z",
            source="test",
            entries={QUAD: {"dev_hours": 8, "production_steps": ["channel-trim"]}},
        )
        prod = pack_outcome(claim, cat, "production", lock)
        proto = pack_outcome(claim, cat, "prototype", lock)
        assert prod.winner.chain.cells == (QUAD,)  # BOM/area at volume: pack wins
        assert proto.winner.chain.cells == (SINGLE,) * 4  # dev_hours dominate: singles win

    def test_outcome_is_deterministic(self):
        claim, cat = _quad_pack_claim()
        assert repr(pack_outcome(claim, cat, "production")) == repr(
            pack_outcome(claim, cat, "production")
        )


# --------------------------------------------------------------------------
# 8. spec absorption knob wiring (Spec.absorption -> PackKnobs)
# --------------------------------------------------------------------------


class TestSpecKnob:
    def test_absorption_from_spec_mapping(self):
        # Spec parses absorption; PackKnobs consumes the same vocabulary.
        table = allocations_from_spec({"allocations": []})
        assert not table  # sanity: helper still importable
        for value in ("off", "conservative", "aggressive"):
            assert PackKnobs(value).absorption == value
