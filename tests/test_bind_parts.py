"""MPN-level part binding: candidate-schema validation + binder pick rule
(SEED_PLAN acceptance criterion 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.bind.parts import PartBinding, bind_parts, fragment_refs
from infersynth.catalog import CellPackageError, load_cell

CORE = Path(__file__).resolve().parent.parent / "catalog" / "core"


def _write_cell(
    root: Path,
    *,
    refs: dict[str, list[str]],
    candidates,
    name: str = "c1",
) -> Path:
    """A minimal loadable cell with a golden_netlist.txt over *refs* (net ->
    ref list) and the given ``selection.candidates`` payload."""
    cell_dir = root / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    golden = "".join(
        f"/{net}: " + ", ".join(f"{r}/1" for r in rs) + "\n" for net, rs in refs.items()
    )
    (cell_dir / "golden_netlist.txt").write_text(golden)
    data = {
        "manifest": {
            "name": name,
            "version": "0.1.0",
            "description": "seed-binding test cell",
            "provenance": "synthetic",
            "license": "GPL-3.0-or-later",
        },
        "idioms": {"keywords": ["test"]},
        "selection": {"candidates": candidates},
        "verification": {"golden_netlist": "golden_netlist.txt"},
        "depth": {"level": "L0"},
    }
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
    return cell_dir


def _cand(mpn, refs, mfr="ACME", fp="Resistor_SMD:R_0603_1608Metric"):
    return {"mpn": mpn, "manufacturer": mfr, "footprint": fp, "maps": {r: True for r in refs}}


class TestCandidateSchema:
    def test_valid_candidates_load(self, tmp_path: Path) -> None:
        cell = load_cell(
            _write_cell(
                tmp_path,
                refs={"N": ["R1", "R2"]},
                candidates=[_cand("RC0603FR-0710KL", ["R1", "R2"])],
            )
        )
        assert cell.selection["candidates"][0]["mpn"] == "RC0603FR-0710KL"

    def test_unknown_candidate_key_rejected(self, tmp_path: Path) -> None:
        bad = _cand("M", ["R1"])
        bad["colour"] = "red"
        with pytest.raises(CellPackageError, match="unknown key"):
            load_cell(_write_cell(tmp_path, refs={"N": ["R1"]}, candidates=[bad]))

    def test_missing_required_field_rejected(self, tmp_path: Path) -> None:
        bad = {"manufacturer": "ACME", "footprint": "F", "maps": {"R1": True}}
        with pytest.raises(CellPackageError, match="mpn is required"):
            load_cell(_write_cell(tmp_path, refs={"N": ["R1"]}, candidates=[bad]))

    def test_empty_maps_rejected(self, tmp_path: Path) -> None:
        bad = {"mpn": "M", "manufacturer": "A", "footprint": "F", "maps": {}}
        with pytest.raises(CellPackageError, match="maps must be a non-empty mapping"):
            load_cell(_write_cell(tmp_path, refs={"N": ["R1"]}, candidates=[bad]))

    def test_map_ref_not_in_fragment_rejected(self, tmp_path: Path) -> None:
        # R9 is not a fragment ref (golden netlist only has R1/R2).
        with pytest.raises(CellPackageError, match=r"references 'R9'.*not a fragment ref"):
            load_cell(
                _write_cell(
                    tmp_path,
                    refs={"N": ["R1", "R2"]},
                    candidates=[_cand("M", ["R1", "R2", "R9"])],
                )
            )

    def test_candidates_must_be_list(self, tmp_path: Path) -> None:
        with pytest.raises(CellPackageError, match="candidates must be a list"):
            load_cell(_write_cell(tmp_path, refs={"N": ["R1"]}, candidates={"nope": 1}))

    def test_sourcing_is_freeform_but_must_be_mapping(self, tmp_path: Path) -> None:
        ok = _cand("M", ["R1"])
        ok["sourcing"] = {"lcsc": "C123", "octopart": "xyz"}
        cell = load_cell(_write_cell(tmp_path, refs={"N": ["R1"]}, candidates=[ok]))
        assert cell.selection["candidates"][0]["sourcing"]["lcsc"] == "C123"


class TestBindParts:
    def test_first_covering_candidate_wins(self, tmp_path: Path) -> None:
        cell = load_cell(
            _write_cell(
                tmp_path,
                refs={"N": ["R1"]},
                candidates=[_cand("FIRST", ["R1"]), _cand("SECOND", ["R1"])],
            )
        )
        res = bind_parts(cell)
        assert res.complete
        assert res.bindings["R1"] == PartBinding("FIRST", "ACME", "Resistor_SMD:R_0603_1608Metric")

    def test_incomplete_coverage_reported(self, tmp_path: Path) -> None:
        cell = load_cell(
            _write_cell(
                tmp_path,
                refs={"A": ["R1"], "B": ["R2"], "C": ["R3"]},
                candidates=[_cand("M", ["R1"])],
            )
        )
        res = bind_parts(cell)
        assert not res.complete
        assert res.unbound == ("R2", "R3")
        assert set(res.bindings) == {"R1"}

    def test_value_parametric_refs_share_one_candidate(self, tmp_path: Path) -> None:
        cell = load_cell(
            _write_cell(
                tmp_path,
                refs={"N": ["R1", "R2", "R3"]},
                candidates=[_cand("RC0603", ["R1", "R2", "R3"])],
            )
        )
        res = bind_parts(cell)
        assert {b.mpn for b in res.bindings.values()} == {"RC0603"}
        assert len(res.bindings) == 3

    def test_all_core_cells_fully_bound(self) -> None:
        # SEED_PLAN crit 4: every core cell has a complete, orderable binding.
        for cell_dir in sorted(CORE.iterdir()):
            if not (cell_dir / "cell.yaml").is_file():
                continue
            cell = load_cell(cell_dir)
            res = bind_parts(cell)
            assert res.complete, f"{cell_dir.name} unbound: {res.unbound}"
            assert res.refs == tuple(sorted(fragment_refs(cell)))
            for binding in res.bindings.values():
                assert binding.mpn and binding.manufacturer and binding.footprint
