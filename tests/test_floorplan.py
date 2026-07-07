"""Tests for the auto-floorplanner + placed-board emission (docs/FLOORPLAN.md)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.floorplan.extents import estimate_extent, pin_count
from infersynth.floorplan.plan import (
    ComponentLite,
    NetLite,
    PlacementHints,
    check_no_overlap,
    floorplan,
    sanitize_ref_prefix,
)
from infersynth.spec import PlacementSpec, SpecError, load_spec

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"
BRIDGE_SPEC = REPO / "examples" / "frds" / "07_bridgesense_1.spec.yaml"
_KICAD_CLI = shutil.which("kicad-cli")
_PCBNEW_PY = Path("/home/cycix/Desktop/fai-tuner/KiCAD-MCP-Server/venv/bin/python")


# --- a tiny synthetic 3-stage chain: a -> b -> c ----------------------------
def _chain_inputs():
    instances = ["a_src", "b_mid", "c_snk"]
    instance_cells = {"a_src": "x/A", "b_mid": "x/B", "c_snk": "x/C"}
    nets = [
        NetLite("signal", "f_A_B", (("a_src", "OUT"), ("b_mid", "IN"))),
        NetLite("signal", "f_B_C", (("b_mid", "OUT"), ("c_snk", "IN"))),
        NetLite("rail", "GND", (("a_src", "GND"), ("b_mid", "GND"), ("c_snk", "GND"))),
    ]
    components = [
        ComponentLite("U101", "OPA", "Package_TO_SOT_SMD:SOT-23-5"),
        ComponentLite("R101", "10k", "Resistor_SMD:R_0603_1608Metric"),
        ComponentLite("U201", "OPA", "Package_TO_SOT_SMD:SOT-23-5"),
        ComponentLite("U301", "OPA", "Package_TO_SOT_SMD:SOT-23-5"),
        ComponentLite("C301", "1n", "Capacitor_SMD:C_0603_1608Metric"),
    ]
    port_dirs = {
        "x/A": {"OUT": "out", "GND": "in"},
        "x/B": {"IN": "in", "OUT": "out", "GND": "in"},
        "x/C": {"IN": "in", "GND": "in"},
    }
    return instances, instance_cells, nets, components, port_dirs


def test_flow_order_is_topological():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs))
    depth = {c.instance: c.flow_depth for c in fp.clusters}
    assert depth == {"a_src": 0, "b_mid": 1, "c_snk": 2}
    # packed/flow order (clusters are placed in sorted flow order) follows depth.
    assert [c.instance for c in fp.clusters] == ["a_src", "b_mid", "c_snk"]


def test_passive_connector_orients_flow():
    # a passive->in net (a sensor connector feeding a conditioner) is oriented
    # passive=source, in=sink even with no explicit 'out' port.
    instances = ["sns", "cnd"]
    cells = {"sns": "x/CONN", "cnd": "x/CND"}
    nets = [NetLite("signal", "f_S_C", (("sns", "P"), ("cnd", "IN")))]
    comps = [
        ComponentLite("J101", "H", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"),
        ComponentLite("R201", "1k", "Resistor_SMD:R_0603_1608Metric"),
    ]
    port_dirs = {"x/CONN": {"P": "passive"}, "x/CND": {"IN": "in"}}
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs))
    depth = {c.instance: c.flow_depth for c in fp.clusters}
    assert depth["sns"] == 0 and depth["cnd"] == 1


def test_edge_hint_pulls_to_band():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    hints = PlacementHints(port_dirs=port_dirs, edges={"b_mid": "west", "c_snk": "east"})
    fp = floorplan(instances, cells, nets, comps, hints)
    band = {c.instance: c.band for c in fp.clusters}
    assert band["b_mid"] == "west" and band["c_snk"] == "east" and band["a_src"] == "flow"
    # west sorts before flow; east after (packed order).
    assert [c.instance for c in fp.clusters] == ["b_mid", "a_src", "c_snk"]


def test_edge_hint_resolves_requirement_id_prefix():
    # placement edges keyed by requirement id resolve to the sanitized instance.
    instances = ["sns_01_conn", "cnd_01_bridge"]
    cells = {"sns_01_conn": "x/A", "cnd_01_bridge": "x/B"}
    nets = [NetLite("signal", "f", (("sns_01_conn", "OUT"), ("cnd_01_bridge", "IN")))]
    comps = [ComponentLite("U101", "v", "Resistor_SMD:R_0603_1608Metric")]
    port_dirs = {"x/A": {"OUT": "out"}, "x/B": {"IN": "in"}}
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs,
                                                                 edges={"SNS-01": "north"}))
    band = {c.instance: c.band for c in fp.clusters}
    assert band["sns_01_conn"] == "north"


def test_default_bands_west_east():
    # a rail-driving instance defaults west; a signal-sink connector defaults east.
    instances = ["reg", "chain", "outc"]
    cells = {"reg": "x/REG", "chain": "x/CH", "outc": "x/OUT"}
    nets = [
        NetLite("rail", "VOUT", (("reg", "VOUT"), ("chain", "VCC"))),
        NetLite("signal", "f", (("chain", "OUT"), ("outc", "IN"))),
    ]
    comps = [
        ComponentLite("U101", "reg", "Package_TO_SOT_SMD:SOT-23-5"),
        ComponentLite("U201", "op", "Package_TO_SOT_SMD:SOT-23-5"),
        ComponentLite("J301", "h", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"),
    ]
    port_dirs = {
        "x/REG": {"VOUT": "out"},
        "x/CH": {"VCC": "in", "OUT": "out"},
        "x/OUT": {"IN": "passive"},
    }
    hints = PlacementHints(port_dirs=port_dirs, connectors=frozenset({"outc"}))
    fp = floorplan(instances, cells, nets, comps, hints)
    band = {c.instance: c.band for c in fp.clusters}
    assert band["reg"] == "west"
    assert band["outc"] == "east"
    assert band["chain"] == "flow"


def test_no_overlap_invariant():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs))
    assert check_no_overlap(fp) == []


def test_no_overlap_with_fixed_board():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    hints = PlacementHints(port_dirs=port_dirs, board=(60.0, 60.0))
    fp = floorplan(instances, cells, nets, comps, hints)
    assert fp.board_w_mm >= 60.0
    assert check_no_overlap(fp) == []


def test_tiny_fixed_board_grows_height_with_diagnostic():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    hints = PlacementHints(port_dirs=port_dirs, board=(40.0, 5.0))
    fp = floorplan(instances, cells, nets, comps, hints)
    assert fp.board_h_mm > 5.0
    assert any("exceeds spec board height" in d for d in fp.diagnostics)
    assert check_no_overlap(fp) == []


def test_determinism():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    hints = PlacementHints(port_dirs=port_dirs)
    a = floorplan(instances, cells, nets, comps, hints)
    b = floorplan(instances, cells, nets, comps, hints)
    assert a == b


def test_ref_century_maps_to_instance():
    # U101 -> instance 1, U201 -> instance 2, U301 -> instance 3.
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs))
    by_inst = {c.instance: {f.ref for f in c.footprints} for c in fp.clusters}
    assert by_inst["a_src"] == {"U101", "R101"}
    assert by_inst["b_mid"] == {"U201"}
    assert by_inst["c_snk"] == {"U301", "C301"}


def test_unmapped_ref_is_diagnosed_not_placed():
    instances, cells, nets, comps, port_dirs = _chain_inputs()
    comps = comps + [ComponentLite("U901", "x", "Resistor_SMD:R_0603_1608Metric")]
    fp = floorplan(instances, cells, nets, comps, PlacementHints(port_dirs=port_dirs))
    assert "U901" not in {f.ref for f in fp.footprints}
    assert any("U901" in d for d in fp.diagnostics)


def test_estimate_extent_and_pin_count():
    assert estimate_extent("Resistor_SMD:R_0603_1608Metric") == (2.2, 1.4)
    assert estimate_extent("Package_TO_SOT_SMD:SOT-23-5") == (3.2, 3.2)
    assert pin_count("pinheader_1x04_p2.54mm") == 4
    # unknown footprint falls back to the generous default.
    assert estimate_extent("Weird:Thing") == (5.0, 5.0)


def test_sanitize_ref_prefix():
    assert sanitize_ref_prefix("SNS-01") == "sns_01"
    assert sanitize_ref_prefix("AMP-01") == "amp_01"


# --- spec placement loader ---------------------------------------------------
def _write_spec(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "s.spec.yaml"
    p.write_text(body)
    return p


def test_spec_placement_loads(tmp_path):
    spec = load_spec(_write_spec(tmp_path,
        "placement:\n"
        "  board: {width_mm: 80, height_mm: 60}\n"
        "  edges: {SNS-01: north, OUT-01: east}\n"
    ))
    assert isinstance(spec.placement, PlacementSpec)
    assert spec.placement.board.width_mm == 80.0
    assert spec.placement.edges == {"SNS-01": "north", "OUT-01": "east"}


def test_spec_placement_absent_is_none(tmp_path):
    spec = load_spec(_write_spec(tmp_path, "profile: prototype\n"))
    assert spec.placement is None


def test_spec_placement_rejects_bad_edge(tmp_path):
    with pytest.raises(SpecError, match="must be one of"):
        load_spec(_write_spec(tmp_path, "placement:\n  edges: {SNS-01: up}\n"))


def test_spec_placement_rejects_bad_board(tmp_path):
    with pytest.raises(SpecError, match="must be > 0"):
        load_spec(_write_spec(tmp_path, "placement:\n  board: {width_mm: 0, height_mm: 60}\n"))


def test_spec_placement_rejects_unknown_key(tmp_path):
    with pytest.raises(SpecError, match="unknown key"):
        load_spec(_write_spec(tmp_path, "placement:\n  rows: 3\n"))


def test_bridge_spec_placement_is_none_by_default():
    # the committed BridgeSense spec has no placement: block (edge hints optional).
    spec = load_spec(BRIDGE_SPEC)
    assert spec.placement is None


# --- board e2e (needs kicad-cli + a pcbnew python) ---------------------------
@pytest.mark.kicad
@pytest.mark.slow
@pytest.mark.skipif(
    _KICAD_CLI is None or not _PCBNEW_PY.exists(),
    reason="needs kicad-cli and a pcbnew python",
)
def test_board_emit_e2e(tmp_path):
    from infersynth.catalog import Catalog
    from infersynth.floorplan import emit_board
    from infersynth.synthesize import synthesize

    design = tmp_path / "design"
    synthesize(
        REPO / "examples" / "frds" / "07_bridgesense_1.md",
        CATALOG, design, profile="prototype", verify=True,
    )
    catalog = Catalog.load(CATALOG)
    out = tmp_path / "board.kicad_pcb"
    result = emit_board(design, out, catalog, load_spec(BRIDGE_SPEC).placement)

    assert out.is_file()
    assert result.footprint_count == 39
    assert result.group_count == 13
    assert result.overlaps == []
    text = out.read_text()
    assert text.count("(group ") == 13
    assert text.count("(footprint ") == 39

    # kicad-cli must parse the emitted board (svg export = a parse proof).
    svg = tmp_path / "board.svg"
    proc = subprocess.run(
        ["kicad-cli", "pcb", "export", "svg", "--output", str(svg),
         "--layers", "F.Cu,Edge.Cuts", str(out)],
        capture_output=True, text=True,
    )
    assert svg.is_file(), proc.stderr
