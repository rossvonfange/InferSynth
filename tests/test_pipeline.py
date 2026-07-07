"""WP-F1 fixed-point pipeline tests: convergence, gate-failure exclusion loop,
assisted-second-pass hint, resolutions_needed.json round-trip, determinism,
max_rounds cap, and CLI exit codes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from infersynth.catalog.loader import CellPackage
from infersynth.cli import main
from infersynth.gates.runner import GateReport, GateResult
from infersynth.match.allocation import Allocation, AllocationTable
from infersynth.pipeline import PipelineResult, run_pipeline

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "catalog"

DEMO_FRD = """# Demo sensor board

- The board shall include a non-inverting amplifier gain stage with gain of 4.
- The board shall include a voltage reference.
- The board shall include decoupling.
"""

GAP_FRD = """# Gap board

- The board shall include a flux capacitor.
"""

VREF_FRD = """# Reference board

- The board shall include a voltage reference for the rail.
"""


def pass_runner(cell: CellPackage) -> GateReport:
    """Deterministic always-pass cell-gate stub (keeps tests off kicad-cli)."""
    report = GateReport()
    report.results.append(GateResult.passed("stub", f"{cell.key} ok"))
    return report


def fail_runner(cell: CellPackage) -> GateReport:
    report = GateReport()
    report.results.append(GateResult.failed("stub", f"{cell.key} deliberately failed"))
    return report


@pytest.fixture()
def demo_frd(tmp_path: Path) -> Path:
    p = tmp_path / "demo.md"
    p.write_text(DEMO_FRD, encoding="utf-8")
    return p


@pytest.fixture()
def gap_frd(tmp_path: Path) -> Path:
    p = tmp_path / "gap.md"
    p.write_text(GAP_FRD, encoding="utf-8")
    return p


def _rewrite_cell(cell_dir: Path, *, name: str, keyword: str, bom_qty1: float) -> None:
    """Retarget a copied vref-shunt package: new name, sole keyword, bom price."""
    yaml_path = cell_dir / "cell.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    text = text.replace("name: vref-shunt", f"name: {name}")
    text = text.replace(
        "  keywords:\n    - voltage reference\n    - precision reference\n",
        f"  keywords:\n    - {keyword}\n",
    )
    text = text.replace(
        "bom: {qty1: 0.25, qty1k: 0.08}", f"bom: {{qty1: {bom_qty1}, qty1k: 0.08}}"
    )
    assert f"name: {name}" in text and keyword in text and str(bom_qty1) in text
    yaml_path.write_text(text, encoding="utf-8")


@pytest.fixture()
def vref_catalog(tmp_path: Path) -> Path:
    """A synthetic flat catalog: two competing vref cells for one requirement.

    ``vref-broken`` is CHEAPER (wins round 1) but its golden netlist partition is
    deliberately corrupted, so its own cell gates (netlist-partition-equivalence)
    FAIL. ``vref-good`` is the intact runner-up. Both derive from the real
    core/vref-shunt package; distinct keywords avoid an idiom collision while the
    requirement text recalls both.
    """
    cat = tmp_path / "vref_catalog"
    cat.mkdir()
    shutil.copyfile(CORE / "taxonomy.yaml", cat / "taxonomy.yaml")
    for name, keyword, price in (
        ("vref-broken", "voltage reference", 0.10),
        ("vref-good", "reference for the rail", 0.25),
    ):
        dst = cat / name
        shutil.copytree(CORE / "core" / "vref-shunt", dst)
        shutil.rmtree(dst / "model" / "__pycache__", ignore_errors=True)
        shutil.rmtree(dst / "testbench" / "__pycache__", ignore_errors=True)
        _rewrite_cell(dst, name=name, keyword=keyword, bom_qty1=price)
    # Deliberately break vref-broken: move a pin to a bogus net so the golden
    # partition no longer matches the (intact) fragment's exported netlist.
    golden = cat / "vref-broken" / "golden_netlist.txt"
    text = golden.read_text(encoding="utf-8")
    corrupted = text.replace("/VREF: R1/2, U1/1, U1/2", "/VREF: U1/1, U1/2\n/BOGUS: R1/2")
    assert corrupted != text
    golden.write_text(corrupted, encoding="utf-8")
    return cat


# --------------------------------------------------------------------------
# convergence + determinism
# --------------------------------------------------------------------------
class TestConvergence:
    def test_converges_in_one_round_on_demo(self, demo_frd: Path, tmp_path: Path) -> None:
        result = run_pipeline(
            demo_frd, CORE, tmp_path / "build",
            profile="prototype", cell_gate_runner=pass_runner,
        )
        # No refinement fires -> round 2 reproduces round 1 -> stable fixed point.
        assert result.converged
        assert len(result.rounds) == 2
        assert result.rounds[0].signature() == result.rounds[1].signature()
        assert result.all_decided
        assert result.pending_resolutions == ()
        assert not result.rounds[0].gate_exclusions
        assert not result.rounds[0].hints_applied
        assert result.synthesis is not None and result.synthesis.root.exists()

    def test_determinism_byte_identical_ledgers(self, demo_frd: Path, tmp_path: Path) -> None:
        r1 = run_pipeline(
            demo_frd, CORE, tmp_path / "a", profile="prototype", cell_gate_runner=pass_runner
        )
        r2 = run_pipeline(
            demo_frd, CORE, tmp_path / "b", profile="prototype", cell_gate_runner=pass_runner
        )
        assert r1.rounds == r2.rounds
        assert r1.pending_resolutions == r2.pending_resolutions
        assert r1.converged == r2.converged
        assert (
            (tmp_path / "a" / "resolutions_needed.json").read_bytes()
            == (tmp_path / "b" / "resolutions_needed.json").read_bytes()
        )

    def test_max_rounds_cap(self, demo_frd: Path, tmp_path: Path) -> None:
        # An always-failing gate runner keeps producing new exclusions, so the
        # loop cannot converge in one comparison -> the count-based budget binds.
        result = run_pipeline(
            demo_frd, CORE, tmp_path / "build",
            profile="prototype", max_rounds=2, cell_gate_runner=fail_runner,
        )
        assert len(result.rounds) == 2
        assert not result.converged
        # every round entered with the accumulated exclusions from the last
        assert result.rounds[1].excluded_before == tuple(
            sorted(ge.cell_key for ge in result.rounds[0].gate_exclusions)
        )

    def test_max_rounds_validation(self, demo_frd: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_rounds"):
            run_pipeline(demo_frd, CORE, tmp_path / "build", max_rounds=0)


# --------------------------------------------------------------------------
# (a) gate-failure constraint feedback
# --------------------------------------------------------------------------
class TestGateFailureFeedback:
    def test_round_two_picks_runner_up(self, tmp_path: Path, vref_catalog: Path) -> None:
        pytest.importorskip("subprocess")
        frd = tmp_path / "vref.md"
        frd.write_text(VREF_FRD, encoding="utf-8")

        result = run_pipeline(frd, vref_catalog, tmp_path / "build", profile="prototype")

        # round 1: the cheap-but-broken cell wins, its own gates fail -> revoked
        r1 = result.rounds[0]
        (rid,) = r1.winners  # exactly one lintable requirement
        assert r1.winners[rid] == ("vref-broken@0.1.0",)
        assert [ge.cell_key for ge in r1.gate_exclusions] == ["vref-broken@0.1.0"]
        assert "netlist" in r1.gate_exclusions[0].reason

        # round 2: re-match/re-decide without the revoked cell -> runner-up wins
        r2 = result.rounds[1]
        assert r2.excluded_before == ("vref-broken@0.1.0",)
        assert r2.winners[rid] == ("vref-good@0.1.0",)

        # the loop reaches a stable fixed point and the DESIGN holds the runner-up
        assert result.converged
        assert result.excluded_cells == ("vref-broken@0.1.0",)
        assert result.synthesis is not None
        instantiated = {i.cell_key for i in result.synthesis.instantiated}
        assert instantiated == {"vref-good@0.1.0"}


# --------------------------------------------------------------------------
# (b) assisted second pass (undecided -> hint re-match)
# --------------------------------------------------------------------------
class TestAssistedSecondPass:
    def test_undecided_hint_rematch(self, demo_frd: Path, tmp_path: Path) -> None:
        # R-3 = "voltage reference" scoped to a library with no matching cell
        # -> undecided-no-candidates in round 1; siblings win from "core", so
        # the hint ADDS "core" to R-3's allow list and round 2 decides it.
        allocations = AllocationTable(
            by_id={"R-3": Allocation(at="R-3", allow=("elsewhere",))}
        )
        result = run_pipeline(
            demo_frd, CORE, tmp_path / "build",
            profile="prototype", allocations=allocations, cell_gate_runner=pass_runner,
        )

        r1 = result.rounds[0]
        assert r1.undecided == {"R-3": "undecided-no-candidates"}
        (hint,) = r1.hints_applied
        assert hint.requirement_id == "R-3"
        assert hint.added_libraries == ("core",)
        assert hint.rule == "sibling-winner-libraries"
        assert "allocations" in hint.spec_edit

        r2 = result.rounds[1]
        assert "R-3" in r2.winners  # decided after the widened scope
        # additive only: round-1 winners unchanged in round 2
        for rid, cells in r1.winners.items():
            assert r2.winners[rid] == cells

        assert result.converged
        assert result.all_decided
        assert result.pending_resolutions == ()

    def test_no_hint_without_allocation(self, gap_frd: Path, tmp_path: Path) -> None:
        # A genuine catalog gap (no allocation to widen) never gets a hint:
        # it converges undecided and escalates via pending_resolutions.
        result = run_pipeline(
            gap_frd, CORE, tmp_path / "build",
            profile="prototype", cell_gate_runner=pass_runner,
        )
        assert result.converged
        assert not any(r.hints_applied for r in result.rounds)
        assert not result.all_decided
        kinds = {p.kind for p in result.pending_resolutions}
        assert kinds == {"no-candidates"}


# --------------------------------------------------------------------------
# resolutions_needed.json (the between-runs seam)
# --------------------------------------------------------------------------
class TestResolutionsArtifact:
    def test_schema_round_trip(self, gap_frd: Path, tmp_path: Path) -> None:
        out = tmp_path / "build"
        result = run_pipeline(
            gap_frd, CORE, out, profile="prototype", cell_gate_runner=pass_runner
        )
        assert result.resolutions_path == out / "resolutions_needed.json"
        on_disk = json.loads(result.resolutions_path.read_text(encoding="utf-8"))
        # byte-level round trip: the file IS the document
        assert on_disk == result.resolutions_document()
        assert on_disk["schema"] == "infersynth.pipeline.resolutions/v0"
        assert on_disk["converged"] is True
        assert on_disk["all_decided"] is False
        assert on_disk["rounds"] == len(result.rounds)
        (entry,) = on_disk["resolutions"]
        assert set(entry) == {"id", "kind", "subject", "options", "spec_edit"}
        assert entry["id"] == "undecided:R-2"
        assert entry["kind"] == "no-candidates"
        assert entry["subject"] == "R-2"
        assert entry["options"] == []
        # the SPEC EDIT names the durable artifacts that resolve it next run
        assert "allocation" in entry["spec_edit"] or "[use:" in entry["spec_edit"]

    def test_clean_run_writes_empty_list(self, demo_frd: Path, tmp_path: Path) -> None:
        result = run_pipeline(
            demo_frd, CORE, tmp_path / "build",
            profile="prototype", cell_gate_runner=pass_runner,
        )
        doc = json.loads(result.resolutions_path.read_text(encoding="utf-8"))
        assert doc["resolutions"] == []
        assert doc["all_decided"] is True

    def test_write_resolutions_off(self, demo_frd: Path, tmp_path: Path) -> None:
        out = tmp_path / "build"
        result = run_pipeline(
            demo_frd, CORE, out,
            profile="prototype", cell_gate_runner=pass_runner, write_resolutions=False,
        )
        assert result.resolutions_path is None
        assert not (out / "resolutions_needed.json").exists()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
class TestPipelineCli:
    def test_exit_0_converged_all_decided(
        self, demo_frd: Path, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import infersynth.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "run_cell_gates", pass_runner)
        rc = main(
            [
                "pipeline", "--frd", str(demo_frd), "--catalog", str(CORE),
                "--out", str(tmp_path / "build"), "--profile", "prototype",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "converged: True" in out
        assert "round 1:" in out and "round 2:" in out
        assert "resolutions_needed.json" in out

    def test_exit_3_pending_resolutions(
        self, gap_frd: Path, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import infersynth.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "run_cell_gates", pass_runner)
        rc = main(
            [
                "pipeline", "--frd", str(gap_frd), "--catalog", str(CORE),
                "--out", str(tmp_path / "build"),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 3
        assert "pending resolutions: 1" in out
        assert "[no-candidates] R-2" in out

    def test_exit_1_error(self, tmp_path: Path, capsys) -> None:
        rc = main(
            [
                "pipeline", "--frd", str(tmp_path / "missing.md"),
                "--catalog", str(CORE), "--out", str(tmp_path / "build"),
            ]
        )
        assert rc == 1

    def test_exit_2_no_frd(self, tmp_path: Path, capsys) -> None:
        rc = main(["pipeline", "--catalog", str(CORE), "--out", str(tmp_path / "build")])
        assert rc == 2


# --------------------------------------------------------------------------
# synthesize's additive exclude_cells seam (the whole synthesize.py footprint)
# --------------------------------------------------------------------------
class TestExcludeCellsSeam:
    def test_result_type_shape(self) -> None:
        # PipelineResult exposes the ledger + seam fields the docs promise.
        fields = set(PipelineResult.__dataclass_fields__)
        assert {
            "rounds", "pending_resolutions", "synthesis", "converged",
            "resolutions_path", "excluded_cells", "hint_allocations",
        } <= fields

    def test_synthesize_honors_exclusion(self, tmp_path: Path, vref_catalog: Path) -> None:
        from infersynth.synthesize import synthesize

        frd = tmp_path / "vref.md"
        frd.write_text(VREF_FRD, encoding="utf-8")
        result = synthesize(
            frd, vref_catalog, tmp_path / "build", profile="prototype",
            exclude_cells=frozenset({"vref-broken@0.1.0"}),
        )
        assert {i.cell_key for i in result.instantiated} == {"vref-good@0.1.0"}
