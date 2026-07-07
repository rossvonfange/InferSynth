"""WP-D1 decision-engine tests: cost composition (covers-vs-covers), profile
flip, unpriced handling, lockfile override + determinism, trace round-trip,
tie-break determinism, undecided pass-through, and CLI exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog
from infersynth.decide import (
    CostVector,
    Lockfile,
    build_trace,
    compose,
    cost_vector_for_cell,
    decide,
    load_profile,
    render_text,
    score_candidates,
)
from infersynth.decide import lockfile as lockfile_mod
from infersynth.decide.trace import SCHEMA_ID
from infersynth.match.matcher import MatchResult
from infersynth.match.provenance import Candidate, CandidateChain
from infersynth.match.resolution import make_resolution_request

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# fixtures: a hand-built catalog + match result (no FRD needed for unit tests)
# --------------------------------------------------------------------------


class _FakeCell:
    """The minimal cell surface decide/lockfile touch: ``.key`` and ``.costs``."""

    def __init__(self, key: str, costs: dict) -> None:
        self.key = key
        self.bare_key = key.split("/")[-1]
        self.costs = costs


def fake_catalog(costs_by_key: dict[str, dict]) -> Catalog:
    cat = Catalog()
    for key, costs in costs_by_key.items():
        cat.cells[key] = _FakeCell(key, costs)  # type: ignore[assignment]
    return cat


def chain(req_id: str, *cells: str) -> CandidateChain:
    return CandidateChain.make(
        req_id, tuple(cells), closed=True, surfaced_by=("idiom",) * len(cells)
    )


def match_result(chains: dict[str, tuple[CandidateChain, ...]], **kw) -> MatchResult:
    candidates = {
        rid: tuple(
            Candidate(requirement_id=rid, cell_key=cell, surfaced_by="idiom")
            for ch in chs
            for cell in ch.cells
        )
        for rid, chs in chains.items()
    }
    return MatchResult(candidates=candidates, chains=chains, **kw)


# a "555 discrete" vs "PIC programmable" pair — the SELECTION §6 flip in miniature
COSTS_555 = {
    "bom": {"qty1": 1.0, "qty1k": 0.9},
    "area_mm2": 60,
    "power_mw": 0,
    "part_count": 5,
    "dev_hours": 0,
    "production_steps": [],
    "sourcing_risk": 0,
}
COSTS_PIC = {
    "bom": {"qty1": 1.2, "qty1k": 0.3},
    "area_mm2": 20,
    "power_mw": 0,
    "part_count": 1,
    "dev_hours": 10,
    "production_steps": ["programming"],
    "sourcing_risk": 1,
}


# --------------------------------------------------------------------------
# 1. cost vectors + covers-vs-covers composition
# --------------------------------------------------------------------------


class TestCostComposition:
    def test_unpriced_when_no_costs_block(self):
        assert cost_vector_for_cell(None).unpriced
        assert cost_vector_for_cell({}).unpriced
        assert not cost_vector_for_cell(COSTS_555).unpriced

    def test_compose_is_vector_sum_over_a_cover(self):
        a = cost_vector_for_cell({"bom": {"qty1": 1.0}, "area_mm2": 10, "part_count": 2})
        b = cost_vector_for_cell({"bom": {"qty1": 0.5}, "area_mm2": 4, "part_count": 1})
        total = compose([a, b])
        assert total.bom_at("qty1") == pytest.approx(1.5)
        assert total.area_mm2 == pytest.approx(14)
        assert total.part_count == pytest.approx(3)
        assert not total.unpriced

    def test_unpriced_is_contagious_across_a_cover(self):
        a = cost_vector_for_cell({"bom": {"qty1": 1.0}})
        total = compose([a, CostVector(unpriced=True)])
        assert total.unpriced

    def test_production_steps_concatenate(self):
        a = cost_vector_for_cell({"production_steps": ["program"]})
        b = cost_vector_for_cell({"production_steps": ["calibrate"]})
        assert compose([a, b]).production_steps == ("program", "calibrate")

    def test_covers_compete_as_complete_covers_not_per_cell(self):
        # a 1-cell cover vs a 2-cell cover for the SAME requirement: the 2-cell
        # cover's cost is the SUM of its cells (SELECTION §6 rule 1), so a pair
        # of cheap cells can beat one pricier cell as a complete cover.
        cat = fake_catalog(
            {
                "core/big@1": {"bom": {"qty1k": 1.00}, "area_mm2": 100, "part_count": 4},
                "core/small-a@1": {"bom": {"qty1k": 0.20}, "area_mm2": 15, "part_count": 1},
                "core/small-b@1": {"bom": {"qty1k": 0.20}, "area_mm2": 15, "part_count": 1},
            }
        )
        mr = match_result(
            {
                "R-1": (
                    chain("R-1", "core/big@1"),
                    chain("R-1", "core/small-a@1", "core/small-b@1"),
                )
            }
        )
        dec = decide(mr, cat, "production")
        winner = dec.outcomes["R-1"].winner
        # 2-cell composed cover (0.40 bom, 30 area, 2 parts) beats the 1-cell (1.00 bom)
        assert winner.chain.cells == ("core/small-a@1", "core/small-b@1")
        assert winner.cost.area_mm2 == pytest.approx(30)
        assert winner.cost.part_count == pytest.approx(2)


# --------------------------------------------------------------------------
# 1b. per-cell cost breakdown on the finalist (Loom API-hardening #2)
# --------------------------------------------------------------------------


class TestPerCellCostBreakdown:
    """``Finalist.cell_costs`` exposes the un-composed per-cell vectors alongside
    the composed ``cost`` — so a consumer gets per-cell area even when the
    composed vector is unpriced-contagious."""

    def test_breakdown_is_in_chain_order_and_sums_to_composed(self):
        cat = fake_catalog(
            {
                "core/a@1": {"bom": {"qty1": 1.0}, "area_mm2": 10, "part_count": 2},
                "core/b@1": {"bom": {"qty1": 0.5}, "area_mm2": 4, "part_count": 1},
            }
        )
        mr = match_result({"R-1": (chain("R-1", "core/a@1", "core/b@1"),)})
        fin = decide(mr, cat, "prototype").outcomes["R-1"].winner
        assert [k for k, _ in fin.cell_costs] == ["core/a@1", "core/b@1"]
        assert fin.cost_for_cell("core/a@1").area_mm2 == pytest.approx(10)
        assert fin.cost_for_cell("core/b@1").area_mm2 == pytest.approx(4)
        assert sum(cv.area_mm2 for _, cv in fin.cell_costs) == pytest.approx(fin.cost.area_mm2)

    def test_breakdown_survives_unpriced_contagion(self):
        # one unpriced cell zeroes the COMPOSED vector (unpriced-contagious),
        # but the priced cell keeps its own area in the breakdown.
        cat = fake_catalog(
            {"core/priced@1": {"area_mm2": 42, "bom": {"qty1": 1.0}}, "core/free@1": {}}
        )
        mr = match_result({"R-1": (chain("R-1", "core/priced@1", "core/free@1"),)})
        fin = decide(mr, cat, "prototype").outcomes["R-1"].winner
        assert fin.cost.unpriced                       # composed chain is unpriced
        assert fin.cost.area_mm2 == 0.0                # zeroed by contagion
        assert fin.cost_for_cell("core/priced@1").area_mm2 == pytest.approx(42)
        assert fin.cost_for_cell("core/free@1").unpriced
        assert fin.cost_for_cell("core/absent@1") is None

    def test_cost_composition_is_unchanged_backward_compat(self):
        # the composed cost still composes exactly as before (no behavior change).
        cat = fake_catalog(
            {
                "core/a@1": {"bom": {"qty1": 1.0}, "area_mm2": 10},
                "core/b@1": {"bom": {"qty1": 0.5}, "area_mm2": 4},
            }
        )
        mr = match_result({"R-1": (chain("R-1", "core/a@1", "core/b@1"),)})
        fin = decide(mr, cat, "prototype").outcomes["R-1"].winner
        assert fin.cost.area_mm2 == pytest.approx(14)
        assert fin.cost.bom_at("qty1") == pytest.approx(1.5)


# --------------------------------------------------------------------------
# 2. profile flip (the PIC-vs-555 flip in miniature)
# --------------------------------------------------------------------------


class TestProfileFlip:
    def _decide(self, profile):
        cat = fake_catalog({"lib/d555@1": COSTS_555, "lib/pic@1": COSTS_PIC})
        mr = match_result(
            {"R-1": (chain("R-1", "lib/d555@1"), chain("R-1", "lib/pic@1"))}
        )
        return decide(mr, cat, profile).outcomes["R-1"].winner.chain.cells[0]

    def test_prototype_picks_the_discrete_no_firmware_part(self):
        assert self._decide("prototype") == "lib/d555@1"

    def test_production_picks_the_low_bom_programmable_part(self):
        assert self._decide("production") == "lib/pic@1"

    def test_inline_weights_flip_like_named_profiles(self):
        # dev-heavy inline weights ⇒ discrete; bom-heavy@qty1k ⇒ programmable
        assert self._decide({"dev_hours": 20, "production_steps": 10}) == "lib/d555@1"
        assert self._decide({"bom": 20, "bom_qty": "qty1k"}) == "lib/pic@1"


# --------------------------------------------------------------------------
# 3. unpriced-cell handling
# --------------------------------------------------------------------------


class TestUnpriced:
    def test_unpriced_scores_worst_and_loses(self):
        cat = fake_catalog({"lib/priced@1": COSTS_PIC, "lib/free@1": {}})
        mr = match_result(
            {"R-1": (chain("R-1", "lib/priced@1"), chain("R-1", "lib/free@1"))}
        )
        outcome = decide(mr, cat, "production").outcomes["R-1"]
        assert outcome.winner.chain.cells == ("lib/priced@1",)
        free = next(f for f in outcome.finalists if f.chain.cells == ("lib/free@1",))
        assert free.unpriced
        # worst-in-set on every dimension ⇒ normalized 1.0 everywhere
        assert all(col[outcome.finalists.index(free)] == 1.0 for col in outcome.normalized.values())

    def test_unpriced_flag_reaches_the_trace(self):
        cat = fake_catalog({"lib/free@1": {}})
        mr = match_result({"R-1": (chain("R-1", "lib/free@1"),)})
        dec = decide(mr, cat, "production")
        tr = build_trace(mr, cat, dec)
        assert tr["requirements"][0]["finalists"][0]["unpriced"] is True


# --------------------------------------------------------------------------
# 4. lockfile override + determinism (no clock reads in decide())
# --------------------------------------------------------------------------


class TestLockfile:
    def test_override_replaces_cell_costs(self):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        lock = Lockfile(
            generated_at="2026-07-06T00:00:00Z",
            source="test",
            entries={"lib/pic@1": {"bom": {"qty1k": 99.0}}},
            path="costs.lock.json",
        )
        mr = match_result({"R-1": (chain("R-1", "lib/pic@1"),)})
        dec = decide(mr, cat, "production", lockfile=lock)
        assert dec.outcomes["R-1"].winner.cost.bom_at("qty1k") == pytest.approx(99.0)
        assert dec.lockfile_identity == "costs.lock.json@2026-07-06T00:00:00Z"

    def test_lockfile_partial_override_keeps_other_fields(self):
        lock = Lockfile("t", "t", {"c": {"bom": {"qty1": 5.0}}})
        merged = lock.apply_overrides("c", {"bom": {"qty1": 1.0}, "area_mm2": 42})
        assert merged["bom"] == {"qty1": 5.0}
        assert merged["area_mm2"] == 42

    def test_decide_never_imports_the_clock(self):
        # SELECTION §8: synthesis never reads the wall clock. The decision engine
        # module must not even have datetime in scope — clock reads live only in
        # lockfile.write (a user action).
        import infersynth.decide.engine as engine

        assert not hasattr(engine, "datetime")

    def test_determinism_two_runs_identical_trace(self):
        cat = fake_catalog({"lib/d555@1": COSTS_555, "lib/pic@1": COSTS_PIC})
        mr = match_result(
            {"R-1": (chain("R-1", "lib/d555@1"), chain("R-1", "lib/pic@1"))}
        )
        t1 = json.dumps(build_trace(mr, cat, decide(mr, cat, "production")))
        t2 = json.dumps(build_trace(mr, cat, decide(mr, cat, "production")))
        assert t1 == t2

    def test_write_lockfile_uses_supplied_timestamp(self, tmp_path):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC, "lib/free@1": {}})
        out = tmp_path / "costs.lock.json"
        lock = lockfile_mod.write(cat, out, timestamp="2020-01-01T00:00:00Z")
        assert lock.generated_at == "2020-01-01T00:00:00Z"
        data = json.loads(out.read_text())
        assert data["generated_at"] == "2020-01-01T00:00:00Z"
        # only priced cells get an entry; unpriced cells are never invented
        assert "lib/pic@1" in data["entries"]
        assert "lib/free@1" not in data["entries"]

    def test_refresh_from_live_is_not_implemented(self):
        with pytest.raises(NotImplementedError):
            lockfile_mod.refresh_from_live()

    def test_write_then_load_round_trips(self, tmp_path):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        out = tmp_path / "costs.lock.json"
        lockfile_mod.write(cat, out, timestamp="2021-01-01T00:00:00Z")
        loaded = lockfile_mod.load(out)
        assert loaded.entries["lib/pic@1"]["bom"]["qty1k"] == 0.3


# --------------------------------------------------------------------------
# 5. trace schema round-trip
# --------------------------------------------------------------------------


class TestTrace:
    def test_schema_round_trips_through_json(self):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        mr = match_result({"R-1": (chain("R-1", "lib/pic@1"),)})
        tr = build_trace(mr, cat, decide(mr, cat, "production"))
        assert tr == json.loads(json.dumps(tr))
        assert tr["schema"] == SCHEMA_ID
        req = tr["requirements"][0]
        assert req["candidates_considered"][0]["surfaced_by"] == "idiom"
        assert req["finalists"][0]["cost_vector"]["bom"] == {"qty1": 1.2, "qty1k": 0.3}

    def test_render_text_is_nonempty_and_names_the_profile(self):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        mr = match_result({"R-1": (chain("R-1", "lib/pic@1"),)})
        text = render_text(build_trace(mr, cat, decide(mr, cat, "production")))
        assert "production" in text
        assert "lib/pic@1" in text

    def test_justification_has_no_bare_similarity_score(self):
        # SELECTION §7: the winner's justification is stated in deterministic
        # terms only, never a similarity score.
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        mr = match_result({"R-1": (chain("R-1", "lib/pic@1"),)})
        dec = decide(mr, cat, "production")
        assert "cost" in dec.outcomes["R-1"].justification
        assert "semantic" not in dec.outcomes["R-1"].justification


# --------------------------------------------------------------------------
# 6. tie-break determinism
# --------------------------------------------------------------------------


class TestTieBreak:
    def test_equal_cost_breaks_lexicographically(self):
        same = {"bom": {"qty1k": 0.5}, "area_mm2": 10, "part_count": 1}
        cat = fake_catalog({"lib/zzz@1": same, "lib/aaa@1": dict(same)})
        mr = match_result(
            {"R-1": (chain("R-1", "lib/zzz@1"), chain("R-1", "lib/aaa@1"))}
        )
        outcome = decide(mr, cat, "production").outcomes["R-1"]
        assert outcome.winner.chain.cells == ("lib/aaa@1",)
        assert "tie" in outcome.justification
        assert "lexicographic" in outcome.justification


# --------------------------------------------------------------------------
# 7. undecided pass-through (never guessed)
# --------------------------------------------------------------------------


class TestUndecided:
    def test_resolution_request_stays_undecided(self):
        cat = fake_catalog({"lib/a@1": COSTS_555, "lib/b@1": COSTS_PIC})
        chains = (chain("R-1", "lib/a@1"), chain("R-1", "lib/b@1"))
        scored = tuple(c.with_score(s) for c, s in zip(chains, (0.0, 1.0), strict=True))
        rr = make_resolution_request("R-1", "frd.md", None, scored, variance=0.25)
        mr = match_result({"R-1": chains}, resolution_requests=(rr,))
        dec = decide(mr, cat, "production")
        assert dec.outcomes["R-1"].winner is None
        assert dec.outcomes["R-1"].status == "undecided-resolution-request"
        assert "R-1" in dec.undecided()
        assert not dec.all_decided
        # carried through, never dropped
        assert dec.resolution_requests == (rr,)

    def test_no_candidates_stays_undecided(self):
        cat = fake_catalog({})
        mr = match_result({"R-1": ()})
        dec = decide(mr, cat, "production")
        assert dec.outcomes["R-1"].status == "undecided-no-candidates"
        assert dec.outcomes["R-1"].winner is None

    def test_mixed_decided_and_undecided(self):
        cat = fake_catalog({"lib/pic@1": COSTS_PIC})
        mr = match_result(
            {"R-1": (chain("R-1", "lib/pic@1"),), "R-2": ()}
        )
        dec = decide(mr, cat, "production")
        assert dec.outcomes["R-1"].decided
        assert not dec.outcomes["R-2"].decided
        assert set(dec.winners()) == {"R-1"}


# --------------------------------------------------------------------------
# 8. profile loader errors
# --------------------------------------------------------------------------


class TestProfileLoader:
    def test_unknown_named_profile_errors(self):
        with pytest.raises(ValueError, match="unknown weight profile"):
            load_profile("nonsense")

    def test_unknown_inline_dimension_errors(self):
        with pytest.raises(ValueError, match="unknown cost dimension"):
            load_profile({"bom": 5, "bogus": 1})

    def test_score_candidates_degenerate_range_is_deterministic(self):
        # identical vectors ⇒ zero range ⇒ every dim contributes 0 (no NaN)
        v = cost_vector_for_cell({"bom": {"qty1": 1.0}, "area_mm2": 5})
        scores, _ = score_candidates([v, v], load_profile("production"))
        assert scores == [0.0, 0.0]


# --------------------------------------------------------------------------
# 9. CLI exit codes (0 decided-all / 3 undecided / 1 error)
# --------------------------------------------------------------------------


def _write_full_cell(catalog_dir: Path, name: str, keywords, costs) -> None:
    cell_dir = catalog_dir / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    data = {
        "manifest": {
            "name": name,
            "version": "0.1.0",
            "description": f"test cell {name}",
            "provenance": "synthetic test fixture",
            "license": "GPL-3.0-or-later",
        },
        "idioms": {"keywords": list(keywords)},
        "selection": {},
        "depth": {"level": "L0"},
        "costs": costs,
    }
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))


class TestCli:
    def test_exit_0_when_all_decided(self, tmp_path, capsys):
        from infersynth.cli import main

        cat = tmp_path / "catalog"
        cat.mkdir()
        _write_full_cell(cat, "widget", ["frobnicator widget"], COSTS_PIC)
        frd = tmp_path / "f.md"
        frd.write_text("# FRD\n\n- R-1: The system shall provide a frobnicator widget.\n")
        rc = main(["decide", "--frd", str(frd), "--catalog", str(cat), "--profile", "production"])
        assert rc == 0
        assert "decided:  1/1" in capsys.readouterr().out

    def test_exit_3_when_undecided(self, tmp_path, capsys):
        from infersynth.cli import main

        cat = tmp_path / "catalog"
        cat.mkdir()
        _write_full_cell(cat, "widget", ["frobnicator widget"], COSTS_PIC)
        frd = tmp_path / "f.md"
        frd.write_text("# FRD\n\n- R-1: The system shall provide an unmatchable quux.\n")
        rc = main(["decide", "--frd", str(frd), "--catalog", str(cat), "--profile", "production"])
        assert rc == 3
        assert "undecided" in capsys.readouterr().err

    def test_exit_1_on_error(self, tmp_path, capsys):
        from infersynth.cli import main

        frd = tmp_path / "f.md"
        frd.write_text("- R-1: shall do a thing.\n")
        rc = main(["decide", "--frd", str(frd), "--catalog", str(tmp_path / "nope")])
        assert rc == 1

    def test_trace_written_to_file(self, tmp_path):
        from infersynth.cli import main

        cat = tmp_path / "catalog"
        cat.mkdir()
        _write_full_cell(cat, "widget", ["frobnicator widget"], COSTS_PIC)
        frd = tmp_path / "f.md"
        frd.write_text("# FRD\n\n- R-1: The system shall provide a frobnicator widget.\n")
        out = tmp_path / "trace.json"
        main(["decide", "--frd", str(frd), "--catalog", str(cat), "--trace", str(out)])
        data = json.loads(out.read_text())
        assert data["schema"] == SCHEMA_ID

    def test_costs_lock_cli(self, tmp_path, capsys):
        from infersynth.cli import main

        cat = tmp_path / "catalog"
        cat.mkdir()
        _write_full_cell(cat, "widget", ["frobnicator widget"], COSTS_PIC)
        out = tmp_path / "costs.lock.json"
        rc = main(
            ["costs", "lock", "--catalog", str(cat), "-o", str(out),
             "--timestamp", "2026-07-06T00:00:00Z"]
        )
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["generated_at"] == "2026-07-06T00:00:00Z"
        assert "widget@0.1.0" in data["entries"]
