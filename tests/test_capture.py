"""``infersynth capture`` tests (CATALOG_GROWTH.md sec C).

Unit coverage: hierarchical-label shape -> port direction mapping, the
power-name kind heuristic, ``${IS.*}`` slot discovery, ``--force`` semantics,
and new-library creation. The end-to-end test (``@pytest.mark.kicad``, needs
``kicad-cli``) captures an already-committed cell fragment
(``catalog/core/conn-sensor-4wire``, chosen because it carries no ``${IS.*}``
slots and no behavioral model — so gates should pass outright with only
``simulation`` SKIPPED) and checks the scaffold reproduces the original cell.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from infersynth.capture import (
    CaptureError,
    capture_cell,
    discover_param_slots,
    infer_ports,
)
from infersynth.catalog.loader import load_cell
from infersynth.gates import GateStatus, parse_golden_netlist
from infersynth.gates.netlist_equiv import compare_partitions

_KICAD_CLI = shutil.which("kicad-cli")
CATALOG_CORE = Path(__file__).resolve().parents[1] / "catalog" / "core"
CONN_SENSOR = CATALOG_CORE / "conn-sensor-4wire"


def _label_block(name: str, shape: str | None) -> str:
    shape_line = f'\n\t\t(shape {shape})' if shape is not None else ""
    return f'\t(hierarchical_label "{name}"{shape_line}\n\t\t(at 0 0 0)\n\t)\n'


# --------------------------------------------------------------------------- #
# Port inference: shape -> direction
# --------------------------------------------------------------------------- #
class TestPortInferenceShapes:
    def test_input_shape_is_in(self):
        ports = infer_ports(_label_block("SIG", "input"))
        assert ports["SIG"]["direction"] == "in"

    def test_output_shape_is_out(self):
        ports = infer_ports(_label_block("SIG", "output"))
        assert ports["SIG"]["direction"] == "out"

    def test_bidirectional_shape_is_passive(self):
        ports = infer_ports(_label_block("SIG", "bidirectional"))
        assert ports["SIG"]["direction"] == "passive"

    def test_tri_state_shape_is_passive(self):
        ports = infer_ports(_label_block("SIG", "tri_state"))
        assert ports["SIG"]["direction"] == "passive"

    def test_passive_shape_is_passive(self):
        ports = infer_ports(_label_block("SIG", "passive"))
        assert ports["SIG"]["direction"] == "passive"

    def test_missing_shape_defaults_to_passive(self):
        ports = infer_ports(_label_block("SIG", None))
        assert ports["SIG"]["direction"] == "passive"

    def test_unknown_shape_raises(self):
        with pytest.raises(CaptureError, match="unrecognized"):
            infer_ports(_label_block("SIG", "not_a_real_shape"))

    def test_repeated_label_same_shape_collapses_to_one_port(self):
        text = _label_block("OUT", "output") + _label_block("OUT", "output")
        ports = infer_ports(text)
        assert list(ports) == ["OUT"]

    def test_repeated_label_conflicting_shape_raises(self):
        text = _label_block("OUT", "output") + _label_block("OUT", "input")
        with pytest.raises(CaptureError, match="conflicting shapes"):
            infer_ports(text)

    def test_preserves_first_seen_order(self):
        text = (
            _label_block("B", "output") + _label_block("A", "input") + _label_block("C", "passive")
        )
        assert list(infer_ports(text)) == ["B", "A", "C"]


# --------------------------------------------------------------------------- #
# Port inference: power-name kind heuristic
# --------------------------------------------------------------------------- #
class TestPowerKindHeuristic:
    @pytest.mark.parametrize("name", ["VCC", "VDD", "VEE", "VSS", "GND", "V12", "VBATT", "V3V3"])
    def test_power_names_match(self, name):
        ports = infer_ports(_label_block(name, "input"))
        assert ports[name]["kind"] == "power"

    @pytest.mark.parametrize("name", ["DATA", "CLK", "SDA", "IN", "OUT", "RESET_N"])
    def test_non_power_names_do_not_match(self, name):
        ports = infer_ports(_label_block(name, "input"))
        assert ports[name]["kind"] == "electrical"

    def test_kind_heuristic_is_case_sensitive(self):
        """A mixed-case name starting with 'V' but not all-caps after it
        (e.g. a hypothetical "Vout") is not mistaken for a power rail —
        the regex requires [A-Z0-9_] after the leading V."""
        ports = infer_ports(_label_block("Vout", "output"))
        assert ports["Vout"]["kind"] == "electrical"


# --------------------------------------------------------------------------- #
# ${IS.*} slot discovery
# --------------------------------------------------------------------------- #
class TestSlotDiscovery:
    def test_no_slots(self):
        assert discover_param_slots("(kicad_sch (property \"Value\" \"10k\"))") == []

    def test_finds_unique_slots_in_order(self):
        text = '(property "Value" "${IS.R1}") (property "Value" "${IS.R2}")'
        assert discover_param_slots(text) == ["R1", "R2"]

    def test_dedupes_repeated_slot(self):
        text = '${IS.R1} ... ${IS.R1} ... ${IS.R2}'
        assert discover_param_slots(text) == ["R1", "R2"]


# --------------------------------------------------------------------------- #
# --force behavior (capture_cell shells to kicad-cli for the golden netlist
# and the gates verification step)
# --------------------------------------------------------------------------- #
@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
class TestForce:
    def test_refuses_without_force(self, tmp_path: Path):
        library = tmp_path / "lib"
        capture_cell(
            CONN_SENSOR / "fragment.kicad_sch", name="dup", library=library
        )
        with pytest.raises(CaptureError, match="already exists"):
            capture_cell(
                CONN_SENSOR / "fragment.kicad_sch", name="dup", library=library
            )

    def test_succeeds_and_overwrites_with_force(self, tmp_path: Path):
        library = tmp_path / "lib"
        cell_dir = library / "dup"
        capture_cell(CONN_SENSOR / "fragment.kicad_sch", name="dup", library=library)
        # Poison a file that a real overwrite must clobber.
        (cell_dir / "cell.yaml").write_text("poison: true\n")
        capture_cell(
            CONN_SENSOR / "fragment.kicad_sch", name="dup", library=library, force=True
        )
        data = yaml.safe_load((cell_dir / "cell.yaml").read_text())
        assert "poison" not in data
        assert data["manifest"]["name"] == "dup"


# --------------------------------------------------------------------------- #
# New-library creation
# --------------------------------------------------------------------------- #
@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
class TestLibraryCreation:
    def test_creates_library_yaml_for_new_library_dir(self, tmp_path: Path):
        library = tmp_path / "brand-new-lib"
        assert not library.exists()
        capture_cell(CONN_SENSOR / "fragment.kicad_sch", name="c1", library=library)
        lib_yaml = library / "library.yaml"
        assert lib_yaml.is_file()
        data = yaml.safe_load(lib_yaml.read_text())
        assert data["name"] == "brand-new-lib"
        assert data["tier"] == "local"
        assert data["description"]
        assert data["maintainer"]

    def test_does_not_clobber_existing_library_yaml(self, tmp_path: Path):
        library = tmp_path / "lib"
        library.mkdir()
        (library / "library.yaml").write_text(
            "name: lib\ndescription: pre-existing\ntier: local\nmaintainer: someone\n"
        )
        capture_cell(CONN_SENSOR / "fragment.kicad_sch", name="c1", library=library)
        data = yaml.safe_load((library / "library.yaml").read_text())
        assert data["description"] == "pre-existing"


# --------------------------------------------------------------------------- #
# End-to-end: capture a committed cell fragment and compare against itself
# --------------------------------------------------------------------------- #
@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
class TestEndToEnd:
    def test_capture_conn_sensor_4wire(self, tmp_path: Path):
        # Pretend the committed fragment is a user's freshly-drawn sheet.
        user_sheet = tmp_path / "users_sheet.kicad_sch"
        shutil.copyfile(CONN_SENSOR / "fragment.kicad_sch", user_sheet)

        library = tmp_path / "local"
        result = capture_cell(
            user_sheet,
            name="conn-sensor-4wire-capture",
            library=library,
            keywords=["sensor connector"],
        )

        cell_dir = result.cell_dir
        assert cell_dir == library / "conn-sensor-4wire-capture"

        # fragment.kicad_sch is a byte-identical copy.
        assert (cell_dir / "fragment.kicad_sch").read_bytes() == user_sheet.read_bytes()

        # cell.yaml validates strict-clean against the loader.
        pkg = load_cell(cell_dir)
        assert pkg.name == "conn-sensor-4wire-capture"
        assert pkg.costs == {}  # no costs section emitted; loader is fine with that

        # Inferred ports match the original committed cell's ports.
        original = load_cell(CONN_SENSOR)
        assert pkg.ports == original.ports

        # Golden netlist is equivalent to the original cell's golden netlist.
        captured_golden = parse_golden_netlist(cell_dir / "golden_netlist.txt")
        original_golden = parse_golden_netlist(CONN_SENSOR / "golden_netlist.txt")
        diff = compare_partitions(original_golden, captured_golden)
        assert diff.equivalent, diff.diagnostics

        # No ${IS.*} slots in this fragment -> no bindings, no idiom params.
        assert result.param_slots == []
        assert "bindings" not in yaml.safe_load((cell_dir / "cell.yaml").read_text())

        # Gates pass, with simulation SKIPPED (structural-only cell) not FAILED.
        report = result.gate_report
        assert report.ok, report.summary()
        by_gate = {r.gate: r.status for r in report.results}
        assert by_gate["validate"] == GateStatus.PASS
        assert by_gate["erc"] == GateStatus.PASS
        assert by_gate["netlist-partition-equivalence"] == GateStatus.PASS
        assert by_gate["simulation"] == GateStatus.SKIPPED
