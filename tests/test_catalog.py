"""Catalog loader/validator + idiom-collision tests."""

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog, CatalogError, CellPackageError, load_cell

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


def write_cell(
    root: Path,
    name: str,
    version: str = "0.1.0",
    keywords=("2nd-order active low-pass",),
    params=None,
    disambiguation=None,
    depth=None,
    dirname: str | None = None,
) -> Path:
    """Write a minimal valid cell package under *root*."""
    cell_dir = root / (dirname or name)
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    idioms: dict = {"keywords": list(keywords)}
    if params is not None:
        idioms["params"] = params
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
        "depth": depth or {"level": "L0"},
    }
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
    return cell_dir


class TestLoadCell:
    def test_fixture_cell_validates(self):
        cell = load_cell(FIXTURES / "opamp-gain-noninverting")
        assert cell.name == "opamp-gain-noninverting"
        assert cell.version == "0.1.0"
        assert cell.key == "opamp-gain-noninverting@0.1.0"
        assert "non-inverting amplifier" in cell.keywords
        assert cell.depth["level"] == "L0"
        assert cell.idiom_params["gain"]["range"] == [1.0, 1000.0]

    def test_missing_fragment_fails(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1")
        (cell_dir / "fragment.kicad_sch").unlink()
        with pytest.raises(CellPackageError, match="missing fragment.kicad_sch"):
            load_cell(cell_dir)

    def test_missing_model_and_testbench_fail(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1")
        (cell_dir / "model").rmdir()
        with pytest.raises(CellPackageError, match="missing model/"):
            load_cell(cell_dir)

    def test_missing_manifest_field_fails(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1")
        data = yaml.safe_load((cell_dir / "cell.yaml").read_text())
        del data["manifest"]["provenance"]
        (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
        with pytest.raises(CellPackageError, match="manifest.provenance"):
            load_cell(cell_dir)

    def test_empty_keywords_fail(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", keywords=())
        with pytest.raises(CellPackageError, match="keywords"):
            load_cell(cell_dir)

    def test_l1_requires_layout_assumptions(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", depth={"level": "L1"})
        with pytest.raises(CellPackageError, match="layout_assumptions"):
            load_cell(cell_dir)

    def test_l1_with_layout_assumptions_ok(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            depth={"level": "L1", "layout_assumptions": {"layer_count": 2}},
        )
        assert load_cell(cell_dir).depth["level"] == "L1"

    def test_bad_range_fails(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", params={"fc": {"type": "float", "range": [10, 1]}})
        with pytest.raises(CellPackageError, match="min 10"):
            load_cell(cell_dir)


class TestCatalog:
    def test_loads_fixture_catalog(self):
        catalog = Catalog.load(FIXTURES)
        assert "opamp-gain-noninverting@0.1.0" in catalog.cells

    def test_duplicate_name_version_rejected(self, tmp_path):
        write_cell(tmp_path, "c1", dirname="a")
        write_cell(tmp_path, "c1", dirname="b")
        with pytest.raises(CatalogError, match="duplicate cell c1@0.1.0"):
            Catalog.load(tmp_path)

    def test_same_name_different_versions_ok(self, tmp_path):
        write_cell(tmp_path, "c1", version="0.1.0", dirname="a")
        write_cell(tmp_path, "c1", version="0.2.0", dirname="b")
        catalog = Catalog.load(tmp_path)
        assert len(catalog.cells) == 2


class TestIdiomCollisions:
    """The SEED_PLAN sallen-key vs mfb case: same idiom, needs disambiguation."""

    LOWPASS = {"fc_hz": {"type": "float", "range": [10.0, 100000.0]}}

    def test_overlapping_ranges_collide(self, tmp_path):
        write_cell(tmp_path, "sallen-key-lowpass-2", params=self.LOWPASS)
        write_cell(tmp_path, "mfb-lowpass-2", params=self.LOWPASS)
        with pytest.raises(CatalogError, match="idiom collision"):
            Catalog.load(tmp_path)

    def test_no_params_at_all_collide(self, tmp_path):
        write_cell(tmp_path, "a-cell")
        write_cell(tmp_path, "b-cell")
        with pytest.raises(CatalogError, match="idiom collision"):
            Catalog.load(tmp_path)

    def test_disjoint_ranges_do_not_collide(self, tmp_path):
        write_cell(
            tmp_path, "lp-low", params={"fc_hz": {"type": "float", "range": [10.0, 1000.0]}}
        )
        write_cell(
            tmp_path, "lp-high", params={"fc_hz": {"type": "float", "range": [2000.0, 1e6]}}
        )
        catalog = Catalog.load(tmp_path)
        assert catalog.idiom_collisions() == []

    def test_disambiguation_waives_collision(self, tmp_path):
        write_cell(tmp_path, "sallen-key-lowpass-2", params=self.LOWPASS)
        write_cell(
            tmp_path,
            "mfb-lowpass-2",
            params=self.LOWPASS,
            disambiguation="prefer sallen-key unless inverting output is acceptable",
        )
        catalog = Catalog.load(tmp_path)
        assert catalog.idiom_collisions() == []

    def test_different_keywords_do_not_collide(self, tmp_path):
        write_cell(tmp_path, "a-cell", keywords=("low-pass",))
        write_cell(tmp_path, "b-cell", keywords=("high-pass",))
        assert Catalog.load(tmp_path).idiom_collisions() == []

    def test_disjoint_allowed_sets_do_not_collide(self, tmp_path):
        write_cell(tmp_path, "a-cell", params={"order": {"type": "int", "allowed": [2]}})
        write_cell(tmp_path, "b-cell", params={"order": {"type": "int", "allowed": [4]}})
        assert Catalog.load(tmp_path).idiom_collisions() == []
