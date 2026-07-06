"""WP-M1 matcher tests: idiom recall, allocation scoping, bidirectional
propagation, determinism, and ensemble-variance ResolutionRequests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog
from infersynth.lint.model import Requirement, RequirementSet, SourceSpan
from infersynth.lint.parse_md import parse_frd_text
from infersynth.lint.vocab import Vocabulary
from infersynth.match import (
    AllocationError,
    EndpointSpec,
    MatchKnobs,
    SimGateScorer,
    StructuralScorer,
    allocations_from_spec,
    match,
    propagate_chains,
    resolve_scope,
)
from infersynth.match.allocation import AllocationTable
from infersynth.match.provenance import Candidate
from infersynth.match.recall import idiom_recall

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "catalog"


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------


def write_cell(
    root: Path,
    name: str,
    *,
    keywords,
    ports=None,
    functions=None,
    library=None,
    version="0.1.0",
    disambiguation=None,
) -> Path:
    """Write a minimal valid cell package (optionally under a library dir)."""
    base = root / library if library else root
    cell_dir = base / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    idioms = {"keywords": list(keywords)}
    if functions is not None:
        idioms["functions"] = list(functions)
    if disambiguation is not None:
        idioms["disambiguation"] = disambiguation
    data = {
        "manifest": {
            "name": name,
            "version": version,
            "description": f"test cell {name}",
            "provenance": "synthetic test fixture",
            "license": "GPL-3.0-or-later",
        },
        "idioms": idioms,
        "selection": {},
        "depth": {"level": "L0"},
    }
    if ports is not None:
        data["ports"] = ports
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
    return cell_dir


def write_library(root: Path, library: str, tier="local") -> None:
    lib_dir = root / library
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "library.yaml").write_text(
        yaml.safe_dump(
            {"name": library, "description": f"{library} lib", "tier": tier, "maintainer": "t"}
        )
    )


def one_req(text: str, req_id: str = "R-1") -> RequirementSet:
    req = Requirement(
        id=req_id, text=text, source=SourceSpan("frd.md", 0, 0, 0, len(text))
    )
    return RequirementSet([req])


# --------------------------------------------------------------------------
# 1. idiom recall
# --------------------------------------------------------------------------


class TestIdiomRecall:
    def test_gain_stage_surfaces_opamp_cells(self):
        cat = Catalog.load(CORE)
        rs = one_req("non-inverting amplifier gain stage, gain 100")
        res = match(rs, cat)
        keys = {c.cell_key for c in res.candidates["R-1"]}
        assert "core/opamp-gain-noninverting@0.1.0" in keys
        # every candidate carries idiom provenance (SELECTION §7)
        assert all(c.surfaced_by == "idiom" for c in res.candidates["R-1"])

    def test_candidates_sorted_deterministically(self):
        cat = Catalog.load(CORE)
        rs = one_req("amplifier gain stage")
        res = match(rs, cat)
        cands = res.candidates["R-1"]
        assert list(cands) == sorted(cands)

    def test_two_cell_fixture_recall(self, tmp_path):
        write_cell(tmp_path, "gainstage", keywords=["gain stage"])
        write_cell(tmp_path, "regulator", keywords=["voltage regulator"])
        cat = Catalog.load(tmp_path)
        res = match(one_req("i need a gain stage"), cat)
        keys = {c.cell_key for c in res.candidates["R-1"]}
        assert keys == {"gainstage@0.1.0"}

    def test_no_match_emits_no_primitive(self, tmp_path):
        write_cell(tmp_path, "gainstage", keywords=["gain stage"])
        cat = Catalog.load(tmp_path)
        res = match(one_req("a quantum flux capacitor"), cat)
        assert res.candidates["R-1"] == ()
        codes = [d.code for d in res.diagnostics]
        assert codes == ["frd.no-primitive"]

    def test_recall_helper_splits_by_scope(self, tmp_path):
        write_library(tmp_path, "amps")
        write_library(tmp_path, "power")
        write_cell(tmp_path, "gainstage", keywords=["gain stage"], library="amps")
        cat = Catalog.load(tmp_path)
        vocab = Vocabulary.from_catalog(cat)
        req = list(one_req("gain stage"))[0]
        from infersynth.match.allocation import ResolvedScope

        scope = ResolvedScope(allowed=frozenset({"power"}), has_allocation=True)
        in_scope, out = idiom_recall(req, vocab, cat, scope)
        assert in_scope == ()
        assert {c.cell_key for c in out} == {"amps/gainstage@0.1.0"}


# --------------------------------------------------------------------------
# 2. allocation
# --------------------------------------------------------------------------


class TestAllocationParsing:
    def test_parse_from_spec_mapping(self):
        spec = {"allocations": [{"at": "SYS.1", "allow": ["amps"], "deny": ["power"]}]}
        table = allocations_from_spec(spec)
        alloc = table.get("SYS.1")
        assert alloc.allow == ("amps",)
        assert alloc.deny == ("power",)

    def test_parse_none(self):
        assert not allocations_from_spec(None)
        assert not allocations_from_spec({})

    def test_duplicate_at_rejected(self):
        with pytest.raises(AllocationError, match="duplicate"):
            allocations_from_spec([{"at": "SYS.1"}, {"at": "SYS.1"}])

    def test_unknown_key_rejected(self):
        with pytest.raises(AllocationError, match="unknown"):
            allocations_from_spec([{"at": "SYS.1", "bogus": 1}])


class TestAllocationScope:
    def _tree(self):
        # SYS.1 -> SYS.1.1 -> SYS.1.1.1
        root = Requirement(id="SYS.1", text="root")
        mid = Requirement(id="SYS.1.1", text="mid")
        leaf = Requirement(id="SYS.1.1.1", text="leaf")
        root.add_child(mid)
        mid.add_child(leaf)
        return root, mid, leaf

    def test_inherited_down(self):
        root, mid, leaf = self._tree()
        table = allocations_from_spec([{"at": "SYS.1", "allow": ["amps"]}])
        scope = resolve_scope(leaf, table, frozenset({"amps", "power"}))
        assert scope.allowed == frozenset({"amps"})
        assert scope.has_allocation

    def test_nearer_overrides(self):
        root, mid, leaf = self._tree()
        table = allocations_from_spec(
            [
                {"at": "SYS.1", "allow": ["amps"]},
                {"at": "SYS.1.1", "allow": ["power"]},
            ]
        )
        scope = resolve_scope(leaf, table, frozenset({"amps", "power"}))
        assert scope.allowed == frozenset({"power"})

    def test_deny_beats_allow(self):
        root, mid, leaf = self._tree()
        table = allocations_from_spec(
            [{"at": "SYS.1", "allow": ["amps", "power"], "deny": ["power"]}]
        )
        scope = resolve_scope(leaf, table, frozenset({"amps", "power"}))
        assert scope.allowed == frozenset({"amps"})

    def test_no_allocation_all_compete(self):
        root, mid, leaf = self._tree()
        scope = resolve_scope(leaf, AllocationTable(), frozenset({"amps", "power"}))
        assert scope.allowed is None
        assert not scope.has_allocation

    def test_empty_scope_flagged(self):
        root, mid, leaf = self._tree()
        table = allocations_from_spec([{"at": "SYS.1", "allow": ["amps"], "deny": ["amps"]}])
        scope = resolve_scope(leaf, table, frozenset({"amps", "power"}))
        assert scope.empty


class TestAllocationInMatch:
    def _catalog(self, tmp_path):
        write_library(tmp_path, "amps")
        write_library(tmp_path, "power")
        # same keyword in two libraries -> declare a disambiguation rule on
        # one so the catalog's idiom-collision gate accepts both (recall still
        # surfaces both; disambiguation is a winner-picking hint, not a filter)
        write_cell(tmp_path, "gainstage", keywords=["gain stage"], library="amps")
        write_cell(
            tmp_path,
            "ldo",
            keywords=["gain stage"],
            library="power",
            disambiguation="power-tier variant",
        )
        return Catalog.load(tmp_path)

    def test_deny_filters_candidate(self, tmp_path):
        cat = self._catalog(tmp_path)
        rs = one_req("gain stage", req_id="SYS.1")
        table = allocations_from_spec([{"at": "SYS.1", "deny": ["power"]}])
        res = match(rs, cat, allocations=table)
        keys = {c.cell_key for c in res.candidates["SYS.1"]}
        assert keys == {"amps/gainstage@0.1.0"}

    def test_scoped_out_emits_allocation_empty(self, tmp_path):
        cat = self._catalog(tmp_path)
        rs = one_req("gain stage", req_id="SYS.1")
        # allow only a library that has no matching cell
        table = allocations_from_spec([{"at": "SYS.1", "allow": ["nonexistent"]}])
        res = match(rs, cat, allocations=table)
        assert res.candidates["SYS.1"] == ()
        assert [d.code for d in res.diagnostics] == ["frd.allocation-empty"]

    def test_unallocated_warns_under_strict(self, tmp_path):
        cat = self._catalog(tmp_path)
        rs = one_req("gain stage", req_id="SYS.1")
        res = match(rs, cat, knobs=MatchKnobs(allocation="strict"))
        assert "frd.unallocated" in [d.code for d in res.diagnostics]

    def test_unallocated_absent_under_lenient(self, tmp_path):
        cat = self._catalog(tmp_path)
        rs = one_req("gain stage", req_id="SYS.1")
        res = match(rs, cat)  # lenient default
        assert "frd.unallocated" not in [d.code for d in res.diagnostics]


# --------------------------------------------------------------------------
# 3. bidirectional propagation
# --------------------------------------------------------------------------


class TestPropagation:
    def _chain_catalog(self, tmp_path):
        write_cell(
            tmp_path,
            "gain",
            keywords=["gain stage"],
            ports={
                "IN": {"direction": "in", "kind": "electrical"},
                "OUT": {"direction": "out", "kind": "electrical"},
            },
        )
        write_cell(
            tmp_path,
            "adc",
            keywords=["adc driver"],
            ports={
                "AIN": {"direction": "in", "kind": "electrical"},
                "DOUT": {"direction": "out", "kind": "digital"},
            },
        )
        return Catalog.load(tmp_path)

    def test_two_hop_chain_closes(self, tmp_path):
        cat = self._chain_catalog(tmp_path)
        cands = (
            Candidate("R-1", "gain@0.1.0", "idiom"),
            Candidate("R-1", "adc@0.1.0", "idiom"),
        )
        ep = EndpointSpec(inputs=("electrical",), outputs=("digital",))
        chains = propagate_chains("R-1", cands, cat, ep, budget=64)
        chain_cells = {c.cells for c in chains}
        # gain -> adc bridges electrical-in to digital-out (the 2-hop chain)
        assert ("gain@0.1.0", "adc@0.1.0") in chain_cells
        # adc alone also closes (electrical-in -> digital-out)
        assert ("adc@0.1.0",) in chain_cells
        # gain alone does NOT close (produces electrical, not the digital sink)
        assert ("gain@0.1.0",) not in chain_cells
        assert all(c.closed for c in chains)

    def test_trivial_chains_without_endpoints(self, tmp_path):
        cat = self._chain_catalog(tmp_path)
        cands = (Candidate("R-1", "gain@0.1.0", "idiom"),)
        chains = propagate_chains("R-1", cands, cat, None, budget=64)
        assert [c.cells for c in chains] == [("gain@0.1.0",)]
        assert chains[0].closed

    def test_budget_bounds_expansion(self, tmp_path):
        cat = self._chain_catalog(tmp_path)
        cands = (
            Candidate("R-1", "gain@0.1.0", "idiom"),
            Candidate("R-1", "adc@0.1.0", "idiom"),
        )
        ep = EndpointSpec(inputs=("electrical",), outputs=("digital",))
        # budget=1: only one forward-frontier seed expands; enumeration is bounded
        chains = propagate_chains("R-1", cands, cat, ep, budget=1)
        assert len(chains) <= 2

    def test_no_closure_emits_no_primitive(self, tmp_path):
        cat = self._chain_catalog(tmp_path)
        # both keywords in one requirement so both cells surface as candidates
        rs = one_req("gain stage adc driver")
        # require a POWER output no cell produces -> no chain closes
        ep = {"R-1": EndpointSpec(inputs=("electrical",), outputs=("power",))}
        res = match(rs, cat, endpoints=ep)
        assert res.chains["R-1"] == ()
        assert "frd.no-primitive" in [d.code for d in res.diagnostics]


# --------------------------------------------------------------------------
# 4. determinism (SELECTION §8)
# --------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_twice_identical(self):
        cat = Catalog.load(CORE)
        frd = "# f\n- SYS.1 non-inverting amplifier gain stage\n- SYS.2 output header\n"
        rs1 = parse_frd_text(frd, "frd.md")
        rs2 = parse_frd_text(frd, "frd.md")
        a = match(rs1, cat)
        b = match(rs2, cat)
        assert a.candidates == b.candidates
        assert a.chains == b.chains
        assert a.diagnostics == b.diagnostics
        assert a.resolution_requests == b.resolution_requests

    def test_chain_order_is_total(self, tmp_path):
        write_cell(tmp_path, "b", keywords=["gain stage"])
        write_cell(tmp_path, "a", keywords=["gain stage"], disambiguation="variant a")
        cat = Catalog.load(tmp_path)
        res = match(one_req("gain stage"), cat)
        chains = res.chains["R-1"]
        assert list(chains) == sorted(chains)


# --------------------------------------------------------------------------
# 5. ensemble variance -> ResolutionRequest (RECON_HARVEST §3)
# --------------------------------------------------------------------------


class _SpreadScorer:
    """Deterministic scorer with deliberate spread over a synthetic catalog.

    Earlier fixtures leaned on real core-catalog properties (model coverage,
    then rule-free candidate counts) and broke whenever the catalog evolved;
    the variance signal is a property of the MACHINERY, so it gets a synthetic
    two-cell catalog where both cells are rule-free and the scorer alone
    manufactures the spread."""

    def score(self, chain, catalog):
        return 0.0 if any("beta" in key for key in chain.cells) else 1.0


class TestVarianceResolution:
    def _two_cell_catalog(self, tmp_path):
        # distinct keywords (identical ones would trip the idiom-collision
        # gate); the requirement text below contains both, so both cells
        # surface for the same requirement
        write_cell(tmp_path, "alpha", keywords=["gain stage"])
        write_cell(tmp_path, "beta", keywords=["amplifier stage"])
        return Catalog.load(tmp_path)

    _REQ = "an amplifier stage acting as a gain stage"

    def test_variance_emits_resolution_request(self, tmp_path):
        cat = self._two_cell_catalog(tmp_path)
        res = match(one_req(self._REQ, req_id="SYS.1"), cat, scorer=_SpreadScorer())
        assert len(res.resolution_requests) == 1
        rr = res.resolution_requests[0]
        assert rr.requirement_id == "SYS.1"
        assert rr.variance > 0
        assert rr.diagnostic.code == "frd.ambiguous"
        assert "frd.ambiguous" in [d.code for d in res.diagnostics]

    def test_high_threshold_suppresses_request(self, tmp_path):
        cat = self._two_cell_catalog(tmp_path)
        res = match(
            one_req(self._REQ, req_id="SYS.1"),
            cat,
            knobs=MatchKnobs(variance_threshold=1.0),
            scorer=_SpreadScorer(),
        )
        assert res.resolution_requests == ()

    def test_single_candidate_no_request(self, tmp_path):
        write_cell(tmp_path, "only", keywords=["gain stage"])
        cat = Catalog.load(tmp_path)
        res = match(one_req("gain stage"), cat)
        assert res.resolution_requests == ()

    def test_sim_gate_scorer_runs(self):
        cat = Catalog.load(CORE)
        res = match(
            one_req("non-inverting amplifier gain stage"),
            cat,
            scorer=SimGateScorer(),
        )
        chain = res.chains["R-1"][0]
        assert chain.score == pytest.approx(1.0)

    def test_structural_scorer_default(self):
        cat = Catalog.load(CORE)
        res = match(one_req("bypass capacitor decoupling"), cat, scorer=StructuralScorer())
        # decoupling ships no behavioral model -> structural score 0
        chain = res.chains["R-1"][0]
        assert chain.score == 0.0


# --------------------------------------------------------------------------
# 6. WP-M2 boundary — semantic recall is additive, never changes strict output
# --------------------------------------------------------------------------


class TestWPBoundary:
    def test_semantic_recall_no_longer_raises(self):
        # WP-M2 wired recall='semantic' up for real (infersynth/match/embed.py,
        # infersynth/match/recall.py::semantic_recall); see tests/test_embed.py
        # for the full semantic-recall test suite. This just pins the
        # WP-M1/WP-M2 seam: requesting it must not raise.
        cat = Catalog.load(CORE)
        match(one_req("gain stage"), cat, knobs=MatchKnobs(recall="semantic"))

    def test_strict_mode_candidate_set_unaffected_by_semantic_layer(self):
        # SELECTION §4/§8 acceptance: none of the seed catalog's cells ship
        # an embedding.json, so recall='semantic' has nothing to add — the
        # candidate set for any existing fixture spec must be byte-identical
        # to strict-mode's (the semantic layer is strictly additive, never
        # removes/reorders idiom candidates).
        cat = Catalog.load(CORE)
        req_text = "non-inverting amplifier gain stage, gain 100"
        strict = match(one_req(req_text), cat, knobs=MatchKnobs(recall="strict"))
        semantic = match(one_req(req_text), cat, knobs=MatchKnobs(recall="semantic"))
        assert strict.candidates == semantic.candidates
