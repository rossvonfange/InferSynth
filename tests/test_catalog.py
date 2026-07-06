"""Catalog loader/validator + idiom-collision tests."""

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog, CatalogError, CellPackageError, load_cell

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
GOLDEN_CATALOG = Path(__file__).parent.parent / "catalog"


def write_cell(
    root: Path,
    name: str,
    version: str = "0.1.0",
    keywords=("2nd-order active low-pass",),
    params=None,
    disambiguation=None,
    depth=None,
    dirname: str | None = None,
    extra_sections=None,
    ports=None,
    bindings=None,
    verification=None,
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
    if ports is not None:
        data["ports"] = ports
    if bindings is not None:
        data["bindings"] = bindings
    if verification is not None:
        data["verification"] = verification
    data.update(extra_sections or {})
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


class TestSchemaV1GoldenCells:
    """WP1 item 4: golden-cell yaml round-trip against the committed catalog."""

    @pytest.mark.parametrize(
        "cell_name", ["opamp-gain-noninverting", "opamp-gain-x4-noninverting"]
    )
    def test_round_trip_matches_yaml(self, cell_name):
        cell_dir = GOLDEN_CATALOG / cell_name
        raw = yaml.safe_load((cell_dir / "cell.yaml").read_text())
        cell = load_cell(cell_dir)  # strict by default
        assert cell.name == raw["manifest"]["name"]
        assert cell.version == raw["manifest"]["version"]
        assert cell.manifest == raw["manifest"]
        assert cell.idioms == raw["idioms"]
        assert cell.selection == raw["selection"]
        assert cell.ports == raw["ports"]
        assert cell.bindings == raw["bindings"]
        assert cell.verification == raw["verification"]
        assert cell.depth == raw["depth"]

    def test_golden_catalog_validates_strict(self):
        catalog = Catalog.load(GOLDEN_CATALOG)
        assert set(catalog.cells) == {
            "opamp-gain-noninverting@0.1.0",
            "opamp-gain-x4-noninverting@0.1.0",
        }


class TestStrictMode:
    def test_typoed_section_rejected_in_strict_mode(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"verificaton": {}})
        with pytest.raises(CellPackageError, match="unknown top-level section 'verificaton'"):
            load_cell(cell_dir)

    def test_typoed_section_tolerated_with_strict_false(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"verificaton": {}})
        assert load_cell(cell_dir, strict=False).name == "c1"

    def test_catalog_load_forwards_strict(self, tmp_path):
        write_cell(tmp_path, "c1", extra_sections={"bindngs": {}})
        with pytest.raises(CatalogError, match="unknown top-level section 'bindngs'"):
            Catalog.load(tmp_path)
        assert "c1@0.1.0" in Catalog.load(tmp_path, strict=False).cells


class TestPortsSection:
    def test_valid_ports_normalized(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            ports={
                "IN": {"direction": "in", "kind": "electrical"},
                "VCC": {"direction": "in", "kind": "power"},
                "SENSE": {},  # defaults: passive/electrical
            },
        )
        cell = load_cell(cell_dir)
        assert cell.ports["IN"] == {"direction": "in", "kind": "electrical"}
        assert cell.ports["SENSE"] == {"direction": "passive", "kind": "electrical"}

    def test_illegal_direction_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", ports={"IN": {"direction": "input", "kind": "electrical"}}
        )
        with pytest.raises(CellPackageError, match="illegal direction 'input'"):
            load_cell(cell_dir)

    def test_illegal_kind_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", ports={"IN": {"direction": "in", "kind": "analog"}}
        )
        with pytest.raises(CellPackageError, match="illegal kind 'analog'"):
            load_cell(cell_dir)

    def test_unknown_port_key_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            ports={"IN": {"direction": "in", "kind": "electrical", "voltage": 5}},
        )
        with pytest.raises(CellPackageError, match=r"ports.IN: unknown key\(s\)"):
            load_cell(cell_dir)


class TestBindingsSection:
    GAIN = {"gain": {"type": "float", "range": [1.0, 1000.0]}}

    def test_valid_bindings_load(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", params=self.GAIN, bindings={"R1": "gain * 10"})
        assert load_cell(cell_dir).bindings == {"R1": "gain * 10"}

    def test_unparseable_expression_rejected(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", params=self.GAIN, bindings={"R1": "gain *"})
        with pytest.raises(CellPackageError, match="bindings.R1"):
            load_cell(cell_dir)

    def test_undeclared_free_name_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", params=self.GAIN, bindings={"R1": "gain * rf_ohms"}
        )
        with pytest.raises(CellPackageError, match="'rf_ohms', which is not a declared"):
            load_cell(cell_dir)

    def test_forbidden_expression_rejected_at_load(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", params=self.GAIN, bindings={"R1": "__import__('os')"}
        )
        with pytest.raises(CellPackageError, match="bindings.R1"):
            load_cell(cell_dir)

    def test_non_string_expression_rejected(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", params=self.GAIN, bindings={"R1": 1000})
        with pytest.raises(CellPackageError, match="must be an expression string"):
            load_cell(cell_dir)


class TestVerificationSection:
    def test_golden_netlist_must_exist(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", verification={"golden_netlist": "golden_netlist.txt"}
        )
        with pytest.raises(CellPackageError, match="'golden_netlist.txt' does not exist"):
            load_cell(cell_dir)

    def test_golden_netlist_present_ok(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", verification={"golden_netlist": "golden_netlist.txt"}
        )
        (cell_dir / "golden_netlist.txt").write_text("/OUT: R1/1\n")
        cell = load_cell(cell_dir)
        assert cell.verification == {"golden_netlist": "golden_netlist.txt"}
