"""Catalog loader/validator + idiom-collision tests."""

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog, CatalogError, CellPackageError, load_cell

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
GOLDEN_CATALOG = Path(__file__).parent.parent / "catalog"
CORE_CATALOG = GOLDEN_CATALOG / "core"


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
    functions=None,
    technology=None,
    embedding_json=None,
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
    if functions is not None:
        idioms["functions"] = list(functions)
    manifest = {
        "name": name,
        "version": version,
        "description": f"test cell {name}",
        "provenance": "synthetic test fixture",
        "license": "GPL-3.0-or-later",
    }
    if technology is not None:
        manifest["technology"] = technology
    data = {
        "manifest": manifest,
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
    if embedding_json is not None:
        import json

        (cell_dir / "embedding.json").write_text(json.dumps(embedding_json))
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
        cell_dir = CORE_CATALOG / cell_name
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
            "core/opamp-gain-noninverting@0.1.0",
            "core/opamp-gain-x4-noninverting@0.1.0",
            "core/conn-sensor-4wire@0.1.0",
            "core/conn-power-2pin@0.1.0",
            "core/conn-output-header@0.1.0",
            "core/decoupling@0.1.0",
            "core/unity-buffer@0.1.0",
            "core/opamp-gain-inverting@0.1.0",
            "core/vref-shunt@0.1.0",
            "core/output-clamp@0.1.0",
            "core/summing-offset-stage@0.1.0",
            "core/adc-driver-rc@0.1.0",
            "core/current-source-bjt@0.1.0",
            "core/power-input-conditioning@0.1.0",
        }
        assert all(c.library == "core" for c in catalog.cells.values())


class TestSeedStructuralCells:
    """SEED_PLAN.md sec 2: structural-only L0 cells (connectors + decoupling).

    Each asserts strict-clean loading and that the cell's golden netlist
    parses via the same reader :func:`infersynth.mcp_server.tools.run_gates`
    uses for the netlist-partition-equivalence gate.
    """

    @pytest.mark.parametrize(
        "cell_name,expected_nets",
        [
            (
                "conn-sensor-4wire",
                {"/EXC_P", "/SENSE_P", "/SENSE_N", "/EXC_N"},
            ),
            ("conn-power-2pin", {"/VIN", "/GND"}),
            ("conn-output-header", {"/OUT", "/REF", "/GND", "/SHIELD"}),
            ("decoupling", {"/VCC", "/GND"}),
        ],
    )
    def test_loads_strict_clean_and_golden_netlist_parses(self, cell_name, expected_nets):
        from infersynth.mcp_server.tools import _parse_golden_netlist

        cell_dir = CORE_CATALOG / cell_name
        cell = load_cell(cell_dir)  # strict by default
        assert cell.name == cell_name
        assert cell.depth["level"] == "L0"
        golden_name = cell.verification["golden_netlist"]
        partition = _parse_golden_netlist((cell_dir / golden_name).read_text())
        assert set(partition) == expected_nets
        assert all(partition[net] for net in partition)  # every net has >=1 pin

    def test_decoupling_binding_format_hint(self):
        cell = load_cell(CORE_CATALOG / "decoupling")
        assert cell.bindings == {"C1": "c_farads"}
        assert cell.binding_formats == {"C1": "capacitance"}


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

    def test_mapping_form_with_format_hint(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            params=self.GAIN,
            bindings={"C1": {"expr": "gain * 10", "format": "capacitance"}},
        )
        cell = load_cell(cell_dir)
        assert cell.bindings == {"C1": "gain * 10"}
        assert cell.binding_formats == {"C1": "capacitance"}

    def test_mapping_form_without_format_hint(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", params=self.GAIN, bindings={"R1": {"expr": "gain * 10"}}
        )
        cell = load_cell(cell_dir)
        assert cell.bindings == {"R1": "gain * 10"}
        assert cell.binding_formats == {}

    def test_mapping_form_unknown_key_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            params=self.GAIN,
            bindings={"R1": {"expr": "gain * 10", "units": "ohms"}},
        )
        with pytest.raises(CellPackageError, match=r"unknown key\(s\)"):
            load_cell(cell_dir)

    def test_mapping_form_missing_expr_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", params=self.GAIN, bindings={"R1": {"format": "capacitance"}}
        )
        with pytest.raises(CellPackageError, match="requires a string 'expr'"):
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


def _write_library(root: Path, name: str, tier: str = "official") -> Path:
    lib_dir = root / name
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "library.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} library",
                "tier": tier,
                "maintainer": "test",
            }
        )
    )
    return lib_dir


class TestLibraryLayout:
    """SELECTION.md sec 1: two-level (library/cell) layout, plus the flat
    layout kept for tests/fixtures — detected by presence of library.yaml."""

    def test_real_catalog_is_two_level_under_core(self):
        catalog = Catalog.load(GOLDEN_CATALOG)
        cell = catalog.cells["core/decoupling@0.1.0"]
        assert cell.library == "core"
        assert cell.key == "core/decoupling@0.1.0"
        assert cell.bare_key == "decoupling@0.1.0"

    def test_flat_layout_still_loads_with_no_library(self, tmp_path):
        write_cell(tmp_path, "c1")
        catalog = Catalog.load(tmp_path)
        cell = catalog.cells["c1@0.1.0"]
        assert cell.library is None
        assert cell.key == "c1@0.1.0"

    def test_two_level_subdir_without_library_yaml_errors(self, tmp_path):
        lib = _write_library(tmp_path, "core")
        write_cell(lib, "c1")
        (tmp_path / "stray").mkdir()  # a plain dir, no library.yaml
        with pytest.raises(CatalogError, match="expected library.yaml"):
            Catalog.load(tmp_path)

    def test_library_yaml_missing_field_rejected(self, tmp_path):
        lib = tmp_path / "core"
        lib.mkdir()
        (lib / "library.yaml").write_text(
            yaml.safe_dump({"name": "core", "tier": "official"})
        )
        write_cell(lib, "c1")
        with pytest.raises(CatalogError, match="description is required"):
            Catalog.load(tmp_path)

    def test_library_yaml_bad_tier_rejected(self, tmp_path):
        lib = _write_library(tmp_path, "core", tier="bogus")
        write_cell(lib, "c1")
        with pytest.raises(CatalogError, match="tier must be one of"):
            Catalog.load(tmp_path)


class TestBareNameResolution:
    """SELECTION.md sec 1: bare name@version resolves iff unambiguous."""

    def _two_libraries(self, tmp_path) -> tuple[Path, Path]:
        lib_a = _write_library(tmp_path, "core")
        lib_b = _write_library(tmp_path, "community", tier="community")
        write_cell(lib_a, "widget")
        return lib_a, lib_b

    def test_full_key_lookup(self, tmp_path):
        self._two_libraries(tmp_path)
        catalog = Catalog.load(tmp_path)
        assert catalog.get("core/widget@0.1.0").name == "widget"

    def test_bare_key_resolves_when_unambiguous(self, tmp_path):
        self._two_libraries(tmp_path)
        catalog = Catalog.load(tmp_path)
        cell = catalog.get("widget@0.1.0")
        assert cell.key == "core/widget@0.1.0"

    def test_bare_key_ambiguous_across_libraries_raises(self, tmp_path):
        lib_a, lib_b = self._two_libraries(tmp_path)
        write_cell(lib_b, "widget")
        catalog = Catalog.load(tmp_path)
        with pytest.raises(CatalogError, match="ambiguous cell reference 'widget@0.1.0'"):
            catalog.get("widget@0.1.0")

    def test_unknown_ref_raises(self, tmp_path):
        self._two_libraries(tmp_path)
        catalog = Catalog.load(tmp_path)
        with pytest.raises(CatalogError, match="no cell matches"):
            catalog.get("nope@9.9.9")


class TestFunctionsTaxonomy:
    """SELECTION.md sec 3: idioms.functions validated against taxonomy.yaml."""

    TAXONOMY = {"version": 1, "functions": {"timing": {"desc": "oscillators"}}}

    def test_valid_function_tag_on_real_cell(self):
        cell = load_cell(CORE_CATALOG / "opamp-gain-noninverting")
        assert cell.functions == ("amplification",)

    def test_unknown_tag_rejected(self, tmp_path):
        (tmp_path / "taxonomy.yaml").write_text(yaml.safe_dump(self.TAXONOMY))
        write_cell(tmp_path, "c1", functions=["nope"])
        with pytest.raises(CatalogError, match="unknown tag 'nope'"):
            Catalog.load(tmp_path)

    def test_dotted_refinement_validates_against_root_tag(self, tmp_path):
        (tmp_path / "taxonomy.yaml").write_text(yaml.safe_dump(self.TAXONOMY))
        write_cell(tmp_path, "c1", functions=["timing.astable"])
        catalog = Catalog.load(tmp_path)
        cell = next(iter(catalog.cells.values()))
        assert cell.functions == ("timing.astable",)

    def test_no_taxonomy_in_catalog_rejects_functions_claim(self, tmp_path):
        # A flat fixture-style catalog with no taxonomy.yaml at all.
        write_cell(tmp_path, "c1", functions=["timing"])
        with pytest.raises(CatalogError, match="no taxonomy in catalog"):
            Catalog.load(tmp_path)

    def test_bad_functions_shape_rejected(self, tmp_path):
        (tmp_path / "taxonomy.yaml").write_text(yaml.safe_dump(self.TAXONOMY))
        cell_dir = write_cell(tmp_path, "c1")
        data = yaml.safe_load((cell_dir / "cell.yaml").read_text())
        data["idioms"]["functions"] = "timing"  # not a list
        (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
        with pytest.raises(CatalogError, match="idioms.functions must be a list"):
            Catalog.load(tmp_path)


class TestManifestTechnology:
    def test_valid_technology_accepted(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", technology="passive")
        assert load_cell(cell_dir).manifest["technology"] == "passive"

    def test_invalid_technology_rejected(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", technology="quantum-dot")
        with pytest.raises(CellPackageError, match="manifest.technology must be one of"):
            load_cell(cell_dir)


class TestCostsSection:
    """SELECTION.md sec 6: validator-checked, unused by v1."""

    OK = {
        "bom": {"qty1": 1.0, "qty1k": 0.5},
        "area_mm2": 10,
        "power_mw": 1,
        "part_count": 2,
        "dev_hours": 0,
        "production_steps": ["programming"],
        "sourcing_risk": 1,
    }

    def test_valid_costs_load(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": self.OK})
        cell = load_cell(cell_dir)
        assert cell.costs["area_mm2"] == 10
        assert cell.costs["bom"]["qty1k"] == 0.5

    def test_unknown_cost_key_rejected_in_strict_mode(self, tmp_path):
        bad = {**self.OK, "bogus_key": 1}
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": bad})
        with pytest.raises(CellPackageError, match=r"costs: unknown key\(s\)"):
            load_cell(cell_dir)

    def test_unknown_cost_key_tolerated_with_strict_false(self, tmp_path):
        bad = {**self.OK, "bogus_key": 1}
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": bad})
        assert load_cell(cell_dir, strict=False).name == "c1"

    def test_bad_sourcing_risk_rejected(self, tmp_path):
        bad = {**self.OK, "sourcing_risk": 7}
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": bad})
        with pytest.raises(CellPackageError, match="sourcing_risk must be an int in 0..3"):
            load_cell(cell_dir)

    def test_bad_bom_key_rejected(self, tmp_path):
        bad = {**self.OK, "bom": {"notqty": 1.0}}
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": bad})
        with pytest.raises(CellPackageError, match="must match 'qtyN'"):
            load_cell(cell_dir)

    def test_bad_production_steps_rejected(self, tmp_path):
        bad = {**self.OK, "production_steps": "programming"}
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"costs": bad})
        with pytest.raises(CellPackageError, match="production_steps must be a list"):
            load_cell(cell_dir)


class TestReservedSections:
    """SELECTION.md sec 5: capacity/absorbs — accepted, schema-checked,
    unused by v1 (the v2 packer consumes them)."""

    def test_capacity_valid(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", extra_sections={"capacity": {"timers": 4, "gpio": 12}}
        )
        cell = load_cell(cell_dir)
        assert cell.capacity == {"timers": 4, "gpio": 12}

    def test_capacity_non_int_rejected(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1", extra_sections={"capacity": {"timers": "four"}})
        with pytest.raises(CellPackageError, match="capacity.timers must be an int"):
            load_cell(cell_dir)

    def test_absorbs_valid_template_path_need_not_exist(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            extra_sections={
                "absorbs": [
                    {
                        "function": "timing.astable",
                        "consumes": {"timers": 1, "gpio": 1},
                        "template": "templates/astable/",
                    }
                ]
            },
        )
        cell = load_cell(cell_dir)
        assert cell.absorbs[0]["template"] == "templates/astable/"
        # The template path is schema-checked only — its target is not
        # required to exist (template emission is v3 scope).
        assert not (cell_dir / "templates" / "astable").exists()

    def test_absorbs_missing_function_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            extra_sections={"absorbs": [{"consumes": {}, "template": "t/"}]},
        )
        with pytest.raises(CellPackageError, match=r"absorbs\[0\]\.function"):
            load_cell(cell_dir)

    def test_absorbs_unknown_key_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            extra_sections={
                "absorbs": [{"function": "x", "template": "t/", "bogus": 1}]
            },
        )
        with pytest.raises(CellPackageError, match=r"unknown key\(s\)"):
            load_cell(cell_dir)


class TestEmbeddingJson:
    """SELECTION.md sec 4: {model_id, dim, vector} cache, reserved for v1."""

    def test_valid_embedding_loaded(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", embedding_json={"model_id": "m1", "dim": 3, "vector": [0.1, 0.2, 0.3]}
        )
        cell = load_cell(cell_dir)
        assert cell.embedding == {"model_id": "m1", "dim": 3, "vector": [0.1, 0.2, 0.3]}

    def test_absent_embedding_is_none(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1")
        assert load_cell(cell_dir).embedding is None

    def test_dim_mismatch_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path, "c1", embedding_json={"model_id": "m1", "dim": 4, "vector": [0.1, 0.2, 0.3]}
        )
        with pytest.raises(CellPackageError, match=r"len\(vector\)=3 != dim=4"):
            load_cell(cell_dir)

    def test_invalid_json_rejected(self, tmp_path):
        cell_dir = write_cell(tmp_path, "c1")
        (cell_dir / "embedding.json").write_text("{not json")
        with pytest.raises(CellPackageError, match="invalid JSON"):
            load_cell(cell_dir)

    def test_unknown_key_rejected(self, tmp_path):
        cell_dir = write_cell(
            tmp_path,
            "c1",
            embedding_json={"model_id": "m1", "dim": 1, "vector": [0.1], "extra": True},
        )
        with pytest.raises(CellPackageError, match=r"unknown key\(s\)"):
            load_cell(cell_dir)
