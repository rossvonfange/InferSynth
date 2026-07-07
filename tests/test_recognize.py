"""Tests for the cell recognizer (Loom Pillar 2 — reverse weaving).

Coverage: reverse-index construction; anchored subgraph matching (positive +
a near-miss that must NOT match); parameter inversion (exact-linear + numeric
bisection); residual reporting; determinism (two runs byte-identical); the
closed-loop round-trip against a real InferSynth-synthesized design (kicad-
gated) plus a lightweight hand-authored-fixture variant that needs no toolchain.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.bind import bind_cell
from infersynth.catalog import Catalog
from infersynth.compile_kicad.emit import instantiate, new_design
from infersynth.recognize import (
    build_reverse_index,
    load_design_netlist,
    recognize,
)
from infersynth.recognize.invert import invert_params
from infersynth.recognize.match import load_golden_graph, match_cell
from infersynth.recognize.netlist import (
    Component,
    DesignNetlist,
    parse_value_str,
)

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"
NONINV = "core/opamp-gain-noninverting@0.1.0"
INV = "core/opamp-gain-inverting@0.1.0"
SALLEN = "core/sallen-key-lowpass-2@0.1.0"
INSTR = "core/instrumentation-amp-3opamp@0.1.0"
OPA_MPN = "OPA340NA/250"
R_MPN = "RC0603FR-0710KL"

_KICAD = shutil.which("kicad-cli")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG_DIR)


# --------------------------------------------------------------------------- #
# in-memory + XML fixture helpers                                             #
# --------------------------------------------------------------------------- #
def _design(comps: dict[str, tuple[str, str]], nets: dict[str, list[tuple[str, str]]]
            ) -> DesignNetlist:
    """Build a DesignNetlist in memory. *comps*: ref -> (value_str, mpn)."""
    components = {
        ref: Component.build(ref, val, "fp", mpn) for ref, (val, mpn) in comps.items()
    }
    pin_net = {(r, p): name for name, pins in nets.items() for r, p in pins}
    return DesignNetlist(components=components, nets=dict(nets), pin_net=pin_net)


_NONINV_NETS = {
    "/FB": [("R1", "2"), ("R2", "1"), ("U1", "4")],
    "/GND": [("R2", "2")],
    "/IN": [("U1", "3")],
    "/OUT": [("R1", "1"), ("U1", "1")],
    "/VCC": [("U1", "5")],
    "/VEE": [("U1", "2")],
}
_NONINV_COMPS = {
    "R1": ("99k", R_MPN),
    "R2": ("1k", R_MPN),
    "U1": ("OPAMP_SINGLE", OPA_MPN),
}


def _kicadxml(comps: dict[str, tuple[str, str]], nets: dict[str, list[tuple[str, str]]]) -> str:
    comp_xml = "".join(
        f'<comp ref="{ref}"><value>{val}</value><footprint>fp</footprint>'
        f'<property name="MPN" value="{mpn}"/></comp>'
        for ref, (val, mpn) in comps.items()
    )
    net_xml = "".join(
        f'<net code="{i}" name="{name}">'
        + "".join(f'<node ref="{r}" pin="{p}"/>' for r, p in pins)
        + "</net>"
        for i, (name, pins) in enumerate(nets.items(), 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><export version="E">'
        f"<components>{comp_xml}</components><nets>{net_xml}</nets></export>"
    )


# --------------------------------------------------------------------------- #
# STEP 1 — reverse index                                                      #
# --------------------------------------------------------------------------- #
def test_reverse_index_construction(catalog: Catalog) -> None:
    index = build_reverse_index(catalog)
    cells = index.candidates(OPA_MPN)
    assert NONINV in cells and INV in cells and SALLEN in cells
    # the op-amp MPN anchors on the golden IC ref U1 for the non-inverting cell
    assert index.anchor_refs[(OPA_MPN, NONINV)] == ("U1",)
    # a generic resistor MPN is a *weaker* anchor than the op-amp MPN
    assert index.specificity(R_MPN) > index.specificity(OPA_MPN)
    assert index.candidates("NOT-A-REAL-MPN") == ()


def test_value_parsing() -> None:
    assert parse_value_str("99k") == 99000.0
    assert parse_value_str("1k") == 1000.0
    assert parse_value_str("2.2M") == 2200000.0
    assert parse_value_str("470") == 470.0
    assert parse_value_str("1e-08") == pytest.approx(1e-8)
    assert parse_value_str("4k7") == 4700.0
    assert parse_value_str("4R7") == pytest.approx(4.7)
    assert parse_value_str("OPAMP_SINGLE") is None
    assert parse_value_str("") is None


# --------------------------------------------------------------------------- #
# STEP 2 — anchored subgraph match: positive + near-miss negative            #
# --------------------------------------------------------------------------- #
def test_subgraph_match_positive(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    golden = load_golden_graph(catalog.get(NONINV))
    assert golden is not None
    m = match_cell(catalog.get(NONINV), golden, design, "U1", "U1")
    assert m is not None
    assert m.phi == {"U1": "U1", "R1": "R1", "R2": "R2"}
    assert m.design_refs == ("R1", "R2", "U1")


def test_subgraph_match_negative_extra_on_internal_net(catalog: Catalog) -> None:
    """A foreign component pin hung on the cell's INTERNAL feedback node must
    break the match — internal nets require exact membership."""
    nets = {k: list(v) for k, v in _NONINV_NETS.items()}
    nets["/FB"].append(("R3", "1"))  # intruder on the internal FB node
    nets["/X"] = [("R3", "2")]
    comps = {**_NONINV_COMPS, "R3": ("10k", R_MPN)}
    design = _design(comps, nets)
    golden = load_golden_graph(catalog.get(NONINV))
    assert match_cell(catalog.get(NONINV), golden, design, "U1", "U1") is None


def test_subgraph_match_negative_missing_part(catalog: Catalog) -> None:
    """Drop R2 entirely: the topology can no longer be an isomorphic image."""
    nets = {k: [pp for pp in v if pp[0] != "R2"] for k, v in _NONINV_NETS.items()}
    comps = {r: v for r, v in _NONINV_COMPS.items() if r != "R2"}
    design = _design(comps, nets)
    golden = load_golden_graph(catalog.get(NONINV))
    assert match_cell(catalog.get(NONINV), golden, design, "U1", "U1") is None


def test_subgraph_match_pin_swapped_passive(catalog: Catalog) -> None:
    """A physically flipped feedback resistor (pins 1<->2 swapped) still
    matches — two-pin passives are electrically symmetric."""
    nets = {k: list(v) for k, v in _NONINV_NETS.items()}
    # swap R1's pins: R1/1 was OUT, R1/2 was FB -> flip
    nets["/FB"] = [("R1", "1"), ("R2", "1"), ("U1", "4")]
    nets["/OUT"] = [("R1", "2"), ("U1", "1")]
    design = _design(_NONINV_COMPS, nets)
    golden = load_golden_graph(catalog.get(NONINV))
    m = match_cell(catalog.get(NONINV), golden, design, "U1", "U1")
    assert m is not None
    assert m.pin_map["R1"] == {"1": "2", "2": "1"}


# --------------------------------------------------------------------------- #
# STEP 3 — parameter inversion (exact-linear + numeric bisection)            #
# --------------------------------------------------------------------------- #
def test_param_inversion_linear(catalog: Catalog) -> None:
    cell = catalog.get(NONINV)
    observed = {"R1": 99000.0, "R2": 1000.0}  # gain=100, rg=1000
    inv = invert_params(cell, observed)
    assert inv.params["rg_ohms"] == pytest.approx(1000.0)
    assert inv.params["gain"] == pytest.approx(100.0)
    assert inv.method["gain"] == "linear"
    assert inv.method["rg_ohms"] == "identity"
    assert inv.residual < 1e-9
    assert inv.confidence > 0.999


def test_param_inversion_bisection(catalog: Catalog) -> None:
    """Recover a param that enters a binding non-linearly (Sallen-Key q sets
    C1 = 4*q^2*c_base) via the deterministic numeric bisection path."""
    cell = catalog.get(SALLEN)
    params = {"fc_hz": 3300.0, "q": 1.7, "c_base_farads": 1e-8}
    observed = bind_cell(cell, params)  # forward: golden ref -> value
    inv = invert_params(cell, observed)
    assert inv.params["q"] == pytest.approx(1.7, rel=1e-4)
    assert inv.params["fc_hz"] == pytest.approx(3300.0, rel=1e-4)
    assert inv.params["c_base_farads"] == pytest.approx(1e-8, rel=1e-6)
    assert inv.method["q"] == "bisection"
    assert inv.residual < 1e-4


def test_param_inversion_bisection_instr_gain(catalog: Catalog) -> None:
    cell = catalog.get(INSTR)
    params = {"gain": 47.0, "rf_ohms": 10000.0, "r_diff_ohms": 10000.0}
    observed = bind_cell(cell, params)
    inv = invert_params(cell, observed)
    assert inv.params["gain"] == pytest.approx(47.0, rel=1e-4)
    assert inv.method["gain"] == "bisection"
    assert inv.residual < 1e-4


# --------------------------------------------------------------------------- #
# STEP 4 — residual reporting                                                 #
# --------------------------------------------------------------------------- #
def test_residual_reporting(catalog: Catalog) -> None:
    """An unclaimed component (no candidate cell / not part of a matched
    neighborhood) lands in the residual."""
    comps = {**_NONINV_COMPS, "R9": ("330", "SOME-UNKNOWN-MPN")}
    nets = {**{k: list(v) for k, v in _NONINV_NETS.items()}, "/AUX": [("R9", "1"), ("R9", "2")]}
    design = _design(comps, nets)
    result = recognize(design, catalog)
    assert result.recognized_cell_keys == (NONINV,)
    assert "R9" in result.residual
    assert all(r not in result.residual for r in ("R1", "R2", "U1"))
    d = result.to_dict()
    assert d["schema"] == "infersynth.recognize/v0"
    assert d["summary"]["residual_components"] == 1


# --------------------------------------------------------------------------- #
# lightweight closed loop: hand-authored fixture, no kicad-cli                #
# --------------------------------------------------------------------------- #
def test_recognize_hand_fixture(catalog: Catalog, tmp_path: Path) -> None:
    xml = tmp_path / "hand.xml"
    xml.write_text(_kicadxml(_NONINV_COMPS, _NONINV_NETS))
    design = load_design_netlist(xml)
    result = recognize(design, catalog)
    assert result.recognized_cell_keys == (NONINV,)
    inst = result.instances[0]
    assert inst.inversion.params["gain"] == pytest.approx(100.0)
    assert not result.residual


def test_determinism(catalog: Catalog, tmp_path: Path) -> None:
    xml = tmp_path / "hand.xml"
    xml.write_text(_kicadxml(_NONINV_COMPS, _NONINV_NETS))
    a = recognize(load_design_netlist(xml), catalog).to_json()
    b = recognize(load_design_netlist(xml), catalog).to_json()
    assert a == b


# --------------------------------------------------------------------------- #
# the elegant proof: closed-loop round-trip against a real forward weave      #
# --------------------------------------------------------------------------- #
def _synth_netlist(catalog: Catalog, specs: list[tuple[str, dict]], out_dir: Path) -> Path:
    root = new_design(out_dir, "rt")
    for i, (key, params) in enumerate(specs, 1):
        instantiate(catalog.get(key), params, f"c{i}", out_dir, root)
    nl = out_dir / "rt.net.xml"
    subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
         "-o", str(nl), str(root)],
        check=True, capture_output=True,
    )
    return nl


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD is None, reason="kicad-cli not on PATH")
def test_closed_loop_roundtrip_gain_amp(catalog: Catalog, tmp_path: Path) -> None:
    """Synthesize a gain-100 non-inverting amp with InferSynth's own forward
    path, export its netlist, feed it back: the recognizer recovers the cell
    key and gain≈100."""
    nl = _synth_netlist(catalog, [(NONINV, {"gain": 100.0, "rg_ohms": 1000.0})], tmp_path)
    result = recognize(load_design_netlist(nl), catalog)
    assert result.recognized_cell_keys == (NONINV,)
    inst = result.instances[0]
    assert inst.inversion.params["gain"] == pytest.approx(100.0, rel=1e-3)
    assert inst.inversion.params["rg_ohms"] == pytest.approx(1000.0, rel=1e-3)
    assert not result.residual


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD is None, reason="kicad-cli not on PATH")
def test_closed_loop_roundtrip_multicell(catalog: Catalog, tmp_path: Path) -> None:
    """A three-cell design round-trips: every synthesized cell is recovered
    with its input params, zero residual."""
    specs = [
        (NONINV, {"gain": 22.0, "rg_ohms": 1000.0}),
        (SALLEN, {"fc_hz": 2000.0, "q": 0.9, "c_base_farads": 1e-8}),
        (INSTR, {"gain": 33.0, "rf_ohms": 10000.0, "r_diff_ohms": 10000.0}),
    ]
    nl = _synth_netlist(catalog, specs, tmp_path)
    result = recognize(load_design_netlist(nl), catalog)
    assert set(result.recognized_cell_keys) == {NONINV, SALLEN, INSTR}
    by_cell = {i.cell_key: i for i in result.instances}
    assert by_cell[NONINV].inversion.params["gain"] == pytest.approx(22.0, rel=1e-3)
    assert by_cell[SALLEN].inversion.params["fc_hz"] == pytest.approx(2000.0, rel=1e-3)
    assert by_cell[SALLEN].inversion.params["q"] == pytest.approx(0.9, rel=1e-3)
    assert by_cell[INSTR].inversion.params["gain"] == pytest.approx(33.0, rel=1e-3)
    assert not result.residual
