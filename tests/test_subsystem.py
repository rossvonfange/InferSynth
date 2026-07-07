"""Tests for the subsystem-cell tier (docs/HIERARCHICAL_RECOGNITION.md
"Subsystem-cell tier"): interface+composition cells, the ``match_subsystem``
matcher, param recovery, determinism + tie-break, catalog discovery, and the
``hierarchical_recognize`` recognized-subsystem path.

Toolchain-free. A subsystem cell recognizes a segment by INTERFACE + COMPOSITION
(not exact subgraph): interface kind + boundary-width range + member class-count
ranges. This closes the foundry loop — a segment promoted on one board becomes a
subsystem cell recognized on the next.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.recognize import (
    SubsystemCell,
    SubsystemCellError,
    hierarchical_recognize,
    load_subsystem_cell,
    load_subsystem_cells,
    match_subsystem,
    match_subsystems,
)
from infersynth.recognize.netlist import Component, DesignNetlist
from infersynth.recognize.segment import BoundaryPin, Segment
from infersynth.recognize.subsystem import CompositionRule, InterfaceSig

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG_DIR)


# --------------------------------------------------------------------------- #
# Helpers: build a design + a Segment directly (no segmenter run needed).      #
# --------------------------------------------------------------------------- #
def _design(comps: dict[str, tuple[str, str]]) -> DesignNetlist:
    """*comps*: ref -> (value_str, mpn). Connectivity is irrelevant to the
    matcher (it reads the segment's boundary + member classes), so nets empty."""
    components = {r: Component.build(r, v, "fp", m) for r, (v, m) in comps.items()}
    return DesignNetlist(components=components, nets={}, pin_net={})


def _i2c_segment(
    refs: tuple[str, ...], *, kind: str = "i2c", nets: tuple[str, ...] = ("SDA", "SCL")
) -> Segment:
    boundary = tuple(
        BoundaryPin(ref=refs[0], pin=str(i), net=n, interface_kind=kind)
        for i, n in enumerate(nets)
    )
    return Segment(
        id="seg-x",
        component_refs=refs,
        internal_nets=(),
        boundary=boundary,
        interface_kind=kind,
    )


# --------------------------------------------------------------------------- #
# Catalog discovery + load/validation.                                        #
# --------------------------------------------------------------------------- #
def test_catalog_discovers_subsystem_cells(catalog: Catalog) -> None:
    names = set(catalog.subsystem_cells)
    assert {"i2c-bus", "led-bank", "diff-pair-link"} <= names
    i2c = catalog.subsystem_cells["i2c-bus"]
    assert i2c.interface.kind == "i2c"
    assert i2c.golden_ref_cell == "i2c-pullups@0.1.0"
    # composition parsed into rules with ranges
    classes = {r.cls: (r.lo, r.hi) for r in i2c.composition}
    assert classes["R"] == (1, 4) and classes["U"] == (1, 8)


def test_load_seeded_cell_directly() -> None:
    cell = load_subsystem_cell(CATALOG_DIR / "subsystems" / "led-bank")
    assert isinstance(cell, SubsystemCell)
    assert cell.name == "led-bank"
    assert cell.interface.kind == "led"


def test_malformed_cell_raises(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "subsystem_cell.yaml").write_text(
        "kind: widget\nname: ''\ninterface: {kind: i2c, min_width: 5, max_width: 2}\n"
        "composition: [{class: R, count: [3, 1]}]\n"
    )
    with pytest.raises(SubsystemCellError) as exc:
        load_subsystem_cell(d)
    diags = "\n".join(exc.value.diagnostics)
    assert "kind must be 'subsystem'" in diags
    assert "min_width" in diags or "lo <= hi" in diags


def test_load_subsystem_cells_sorted(catalog: Catalog) -> None:
    cells = load_subsystem_cells(CATALOG_DIR / "subsystems")
    assert list(cells) == sorted(cells)  # name-sorted, deterministic


# --------------------------------------------------------------------------- #
# match_subsystem — positive.                                                 #
# --------------------------------------------------------------------------- #
def test_match_positive_i2c_bus(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]
    seg = _i2c_segment(("R1", "R2", "U1"))
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("SENSOR", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "i2c-bus"
    assert m.interface_kind == "i2c"
    assert m.width == 2
    assert m.member_counts == {"R": 2, "U": 1}
    assert m.confidence > 0.0


# --------------------------------------------------------------------------- #
# match_subsystem — negatives.                                                #
# --------------------------------------------------------------------------- #
def test_match_negative_wrong_interface_kind(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]
    seg = _i2c_segment(("R1", "R2", "U1"), kind="spi")  # not i2c
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("X", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


def test_match_negative_width_out_of_range(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]  # width range [2, 8]
    seg = _i2c_segment(("R1", "R2", "U1"), nets=("SDA",))  # width 1 < min 2
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("X", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


def test_match_negative_composition_counts(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]  # needs U in [1, 8]
    seg = _i2c_segment(("R1", "R2"))  # zero U devices
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


# --------------------------------------------------------------------------- #
# Param recovery.                                                             #
# --------------------------------------------------------------------------- #
def test_param_recovery_width_and_pullup(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]
    seg = _i2c_segment(("R1", "R2", "U1"), nets=("SDA", "SCL", "ALERT"))  # width 3
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("SENSOR", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.params["interface_width"] == 3.0
    # pull-up value inverted through the i2c-pullups golden cell (R = 4700)
    assert m.params["r_pullup_ohms"] == pytest.approx(4700.0)


def test_param_recovery_skips_pullup_without_catalog(catalog: Catalog) -> None:
    """With no catalog the golden-cell inversion is skipped; width still recovered."""
    cell = catalog.subsystem_cells["i2c-bus"]
    seg = _i2c_segment(("R1", "R2", "U1"))
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("X", "")})
    m = match_subsystem(seg, cell, design, catalog=None)
    assert m is not None
    assert m.params == {"interface_width": 2.0}


# --------------------------------------------------------------------------- #
# Determinism + tie-break.                                                    #
# --------------------------------------------------------------------------- #
def test_match_is_deterministic(catalog: Catalog) -> None:
    cell = catalog.subsystem_cells["i2c-bus"]
    seg = _i2c_segment(("R1", "R2", "U1"))
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("X", "")})
    a = match_subsystem(seg, cell, design, catalog=catalog)
    b = match_subsystem(seg, cell, design, catalog=catalog)
    assert a is not None and b is not None
    assert a.to_dict() == b.to_dict()


def test_tie_break_tightest_then_lexical(catalog: Catalog) -> None:
    """Two subsystem cells both match one segment -> the most-specific (tightest
    total range slack) wins; ties broken by lexical name."""
    seg = _i2c_segment(("R1", "R2", "U1"))
    design = _design({"R1": ("4.7k", ""), "R2": ("4.7k", ""), "U1": ("X", "")})
    tight = SubsystemCell(
        name="i2c-tight",
        interface=InterfaceSig("i2c", 2, 2),
        composition=(CompositionRule("R", "pullup", 2, 2), CompositionRule("U", "dev", 1, 1)),
    )
    loose = SubsystemCell(
        name="i2c-loose",
        interface=InterfaceSig("i2c", 2, 8),
        composition=(CompositionRule("R", "pullup", 1, 4), CompositionRule("U", "dev", 1, 8)),
    )
    m = match_subsystems(seg, [loose, tight], design)
    assert m is not None and m.cell_name == "i2c-tight"  # tightest wins regardless of order

    # equal specificity -> lexical name wins
    a = SubsystemCell(name="aaa", interface=InterfaceSig("i2c", 2, 2), composition=())
    b = SubsystemCell(name="bbb", interface=InterfaceSig("i2c", 2, 2), composition=())
    m2 = match_subsystems(seg, [b, a], design)
    assert m2 is not None and m2.cell_name == "aaa"


# --------------------------------------------------------------------------- #
# hierarchical_recognize — the recognized-subsystem path.                     #
# --------------------------------------------------------------------------- #
def test_hierarchical_recognizes_subsystem(catalog: Catalog) -> None:
    """A synthetic board whose only cluster is an I2C bus (2 pull-ups + a device
    joined to a connector by an i2c bundle) recognizes as the i2c-bus subsystem
    cell — not promoted."""
    comps = {r: ("4.7k", "") for r in ("R1", "R2")}
    comps["U1"] = ("SENSOR", "")
    comps["J1"] = ("HDR", "")
    components = {r: Component.build(r, v, "fp", m) for r, (v, m) in comps.items()}
    nets = {
        # a tight local cluster around U1 (dedicated 2-pin nets bind it)
        "N_A": [("U1", "1"), ("R1", "1")],
        "N_B": [("U1", "2"), ("R2", "1")],
        "N_C": [("U1", "3"), ("R1", "2"), ("R2", "2")],
        # the i2c bundle crossing to the connector — the interface boundary
        "BUS_SDA": [("U1", "4"), ("J1", "1")],
        "BUS_SCL": [("U1", "5"), ("J1", "2")],
    }
    pin_net = {(r, p): n for n, pins in nets.items() for r, p in pins}
    design = DesignNetlist(components=components, nets=dict(nets), pin_net=pin_net)

    result = hierarchical_recognize(design, catalog)
    assert len(result.recognized_subsystem) >= 1
    match = result.recognized_subsystem[0].subsystem_match
    assert match is not None and match.cell_name == "i2c-bus"
    assert match.interface_kind == "i2c"
    # and it is reported separately from small-cell recognitions + promotions
    d = result.to_dict()
    assert d["summary"]["recognized_subsystem"] >= 1
    assert any(r["subsystem_cell"] == "i2c-bus" for r in d["recognized_subsystem"])
