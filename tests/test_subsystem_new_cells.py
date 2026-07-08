"""Tests for the 4 canonical subsystem cells harvested from the real
PolarFire SoC Discovery Kit board (docs/HIERARCHICAL_RECOGNITION.md
"Subsystem-cell tier"): ``ddr-interface``, ``sdio-slot``,
``connector-header``, ``display-header``.

Mirrors tests/test_subsystem.py's pattern for the 3 seed exemplars: load/
validate via ``Catalog.load`` (no loader edits needed — the loader already
globs ``catalog/subsystems/*``), a positive synthetic match per cell, a
negative match per cell (wrong interface kind, and width out of range), a
tie-break test exercising the EXISTING most-specific-wins logic in
``subsystem.py`` (not something new), and determinism. Toolchain-free — no
kicad-cli needed; the real-board payoff lives in tests/test_segment.py's
kicad/slow-marked ``test_polarfire_board_segments_sanely``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.recognize import (
    SubsystemCell,
    load_subsystem_cell,
    match_subsystem,
    match_subsystems,
)
from infersynth.recognize.netlist import Component, DesignNetlist
from infersynth.recognize.segment import BoundaryPin, Segment
from infersynth.recognize.subsystem import CompositionRule, InterfaceSig

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"

NEW_CELL_NAMES = {"ddr-interface", "sdio-slot", "connector-header", "display-header"}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG_DIR)


def _design(comps: dict[str, tuple[str, str]]) -> DesignNetlist:
    """*comps*: ref -> (value_str, mpn). Connectivity is irrelevant to the
    matcher (it reads the segment's boundary + member classes), so nets empty."""
    components = {r: Component.build(r, v, "fp", m) for r, (v, m) in comps.items()}
    return DesignNetlist(components=components, nets={}, pin_net={})


def _segment(
    refs: tuple[str, ...],
    *,
    kind: str,
    n_iface_nets: int,
    seg_id: str = "seg-x",
) -> Segment:
    """Build a synthetic segment whose boundary carries *n_iface_nets*
    distinct nets of interface_kind *kind* (all pinned on the first ref, which
    is enough for ``_boundary_width`` — it only counts distinct nets)."""
    boundary = tuple(
        BoundaryPin(ref=refs[0], pin=str(i), net=f"{kind.upper()}{i}", interface_kind=kind)
        for i in range(n_iface_nets)
    )
    return Segment(
        id=seg_id,
        component_refs=refs,
        internal_nets=(),
        boundary=boundary,
        interface_kind=kind,
    )


# --------------------------------------------------------------------------- #
# Catalog discovery — no loader edits: the existing subsystems/ glob picks     #
# up the 4 new directories automatically.                                     #
# --------------------------------------------------------------------------- #
def test_catalog_discovers_new_cells(catalog: Catalog) -> None:
    assert NEW_CELL_NAMES <= set(catalog.subsystem_cells)


@pytest.mark.parametrize("name", sorted(NEW_CELL_NAMES))
def test_load_new_cell_directly(name: str) -> None:
    cell = load_subsystem_cell(CATALOG_DIR / "subsystems" / name)
    assert isinstance(cell, SubsystemCell)
    assert cell.name == name
    assert cell.golden_ref_cell is None  # all 4 are structural v0, no electrical core
    assert cell.composition  # every cell declares >=1 composition rule


# --------------------------------------------------------------------------- #
# Positive matches — synthetic segments shaped like the real PolarFire        #
# segments each cell targets (docs of the .yaml files record the exact refs). #
# --------------------------------------------------------------------------- #
def test_match_positive_ddr_interface_small(catalog: Catalog) -> None:
    """Shaped like the real seg-024 (R26/R34/R35/U2): 1 U + 3 R, width 28."""
    cell = catalog.subsystem_cells["ddr-interface"]
    seg = _segment(("U1", "R1", "R2", "R3"), kind="ddr", n_iface_nets=28)
    design = _design({"U1": ("DDR_PHY", ""), "R1": ("", ""), "R2": ("", ""), "R3": ("", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "ddr-interface"
    assert m.width == 28
    assert m.member_counts == {"U": 1, "R": 3}


def test_match_positive_ddr_interface_large(catalog: Catalog) -> None:
    """Shaped like the real seg-003: 1 U + 68 R + 9 C (+ noise classes),
    width 28 — the biggest promoted segment on the board."""
    cell = catalog.subsystem_cells["ddr-interface"]
    refs = ["U1"] + [f"R{i}" for i in range(68)] + [f"C{i}" for i in range(9)] + ["X1", "J1"]
    seg = _segment(tuple(refs), kind="ddr", n_iface_nets=28)
    comps = {"U1": ("FPGA", ""), "X1": ("", ""), "J1": ("", "")}
    comps.update({f"R{i}": ("", "") for i in range(68)})
    comps.update({f"C{i}": ("", "") for i in range(9)})
    design = _design(comps)
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "ddr-interface"
    assert m.member_counts["U"] == 1
    assert m.member_counts["R"] == 68
    assert m.member_counts["C"] == 9


def test_match_positive_sdio_slot(catalog: Catalog) -> None:
    """Shaped like the real seg-015 (J17/U85): 1 J + 1 U, width 8."""
    cell = catalog.subsystem_cells["sdio-slot"]
    seg = _segment(("J1", "U1"), kind="sdio", n_iface_nets=8)
    design = _design({"J1": ("MICROSD", ""), "U1": ("LEVEL_SHIFT", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "sdio-slot"
    assert m.width == 8
    assert m.member_counts == {"J": 1, "U": 1}


def test_match_positive_connector_header(catalog: Catalog) -> None:
    """Shaped like the real seg-013 (J10/R169/R170/R172): 1 J + 3 R, width 10."""
    cell = catalog.subsystem_cells["connector-header"]
    seg = _segment(("J1", "R1", "R2", "R3"), kind="gpio", n_iface_nets=10)
    design = _design({"J1": ("HDR", ""), "R1": ("", ""), "R2": ("", ""), "R3": ("", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "connector-header"
    assert m.width == 10
    assert m.member_counts == {"J": 1, "R": 3}


def test_match_positive_connector_header_bare_header(catalog: Catalog) -> None:
    """A bare 2-pin header with no passives at all still matches (R count 0 is
    in-range) — pin count varies hugely across boards, which is the point."""
    cell = catalog.subsystem_cells["connector-header"]
    seg = _segment(("J1",), kind="gpio", n_iface_nets=2)
    design = _design({"J1": ("HDR", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.member_counts == {"J": 1}


def test_match_positive_display_header(catalog: Catalog) -> None:
    """Shaped like the real seg-027 (R354/U6): 1 U + 1 R, width 16."""
    cell = catalog.subsystem_cells["display-header"]
    seg = _segment(("U1", "R1"), kind="display", n_iface_nets=16)
    design = _design({"U1": ("SEG_DRIVER", ""), "R1": ("", "")})
    m = match_subsystem(seg, cell, design, catalog=catalog)
    assert m is not None
    assert m.cell_name == "display-header"
    assert m.width == 16
    assert m.member_counts == {"U": 1, "R": 1}


# --------------------------------------------------------------------------- #
# Negative matches — wrong interface kind, and width outside the band.        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cell_name", "wrong_kind", "n_iface_nets"),
    [
        ("ddr-interface", "sdio", 28),   # right width, wrong kind
        ("sdio-slot", "gpio", 8),
        ("connector-header", "ddr", 10),
        ("display-header", "led", 16),
    ],
)
def test_match_negative_wrong_interface_kind(
    catalog: Catalog, cell_name: str, wrong_kind: str, n_iface_nets: int
) -> None:
    cell = catalog.subsystem_cells[cell_name]
    seg = _segment(("U1", "R1"), kind=wrong_kind, n_iface_nets=n_iface_nets)
    design = _design({"U1": ("X", ""), "R1": ("", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


@pytest.mark.parametrize(
    ("cell_name", "kind", "bad_width"),
    [
        ("ddr-interface", "ddr", 4),      # below min_width 8
        ("ddr-interface", "ddr", 128),    # above max_width 96
        ("sdio-slot", "sdio", 2),         # below min_width 4
        ("sdio-slot", "sdio", 32),        # above max_width 16
        ("connector-header", "gpio", 1),  # below min_width 2
        ("connector-header", "gpio", 128),  # above max_width 64
        ("display-header", "display", 2),   # below min_width 4
        ("display-header", "display", 64),  # above max_width 32
    ],
)
def test_match_negative_width_out_of_range(
    catalog: Catalog, cell_name: str, kind: str, bad_width: int
) -> None:
    cell = catalog.subsystem_cells[cell_name]
    seg = _segment(("U1", "R1"), kind=kind, n_iface_nets=bad_width)
    design = _design({"U1": ("X", ""), "R1": ("", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


def test_match_negative_ddr_composition_no_controller(catalog: Catalog) -> None:
    """A ddr-kind segment with zero U (no controller/PHY) does not match — a
    pure termination-only cluster isn't a DDR *interface* on its own."""
    cell = catalog.subsystem_cells["ddr-interface"]
    seg = _segment(("R1", "R2"), kind="ddr", n_iface_nets=28)
    design = _design({"R1": ("", ""), "R2": ("", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


def test_match_negative_connector_header_no_connector(catalog: Catalog) -> None:
    """A gpio-kind segment with zero J (no connector) does not match."""
    cell = catalog.subsystem_cells["connector-header"]
    seg = _segment(("R1", "R2"), kind="gpio", n_iface_nets=10)
    design = _design({"R1": ("", ""), "R2": ("", "")})
    assert match_subsystem(seg, cell, design, catalog=catalog) is None


# --------------------------------------------------------------------------- #
# New cells must never mis-match an i2c or led segment (structurally          #
# guaranteed: match_subsystem's first check is interface_kind equality), and  #
# vice versa — the existing 7 recognitions must not shift onto a new cell.    #
# --------------------------------------------------------------------------- #
def test_new_cells_do_not_claim_i2c_or_led_segments(catalog: Catalog) -> None:
    i2c_seg = _segment(("R1", "R2", "U1"), kind="i2c", n_iface_nets=2)
    led_seg = _segment(("LED1", "LED2", "R1", "R2"), kind="led", n_iface_nets=2)
    design = _design(
        {
            "R1": ("4.7k", ""),
            "R2": ("4.7k", ""),
            "U1": ("X", ""),
            "LED1": ("", ""),
            "LED2": ("", ""),
        }
    )
    new_cells = {n: catalog.subsystem_cells[n] for n in NEW_CELL_NAMES}
    assert match_subsystems(i2c_seg, new_cells, design, catalog=catalog) is None
    assert match_subsystems(led_seg, new_cells, design, catalog=catalog) is None
    # and the seeded i2c-bus / led-bank cells still claim them against the FULL catalog
    i2c_match = match_subsystems(i2c_seg, catalog.subsystem_cells, design, catalog=catalog)
    led_match = match_subsystems(led_seg, catalog.subsystem_cells, design, catalog=catalog)
    assert i2c_match is not None and i2c_match.cell_name == "i2c-bus"
    assert led_match is not None and led_match.cell_name == "led-bank"


# --------------------------------------------------------------------------- #
# Tie-break — exercises the EXISTING most-specific-wins logic in              #
# subsystem.py (not something new here): two `ddr`-kind cells both match one  #
# segment, the tighter total range slack wins.                                #
# --------------------------------------------------------------------------- #
def test_tie_break_ddr_tightest_wins(catalog: Catalog) -> None:
    ddr_interface = catalog.subsystem_cells["ddr-interface"]  # width [8,96], R [1,80], C [0,16]
    seg = _segment(("U1", "R1", "R2", "R3"), kind="ddr", n_iface_nets=28)
    design = _design({"U1": ("X", ""), "R1": ("", ""), "R2": ("", ""), "R3": ("", "")})

    ddr_tight = SubsystemCell(
        name="ddr-tight-test-only",
        interface=InterfaceSig("ddr", 28, 28),
        composition=(
            CompositionRule("U", "controller_phy", 1, 1),
            CompositionRule("R", "termination", 3, 3),
        ),
    )
    m = match_subsystems(seg, [ddr_interface, ddr_tight], design, catalog=catalog)
    assert m is not None and m.cell_name == "ddr-tight-test-only"
    # order-independence
    m2 = match_subsystems(seg, [ddr_tight, ddr_interface], design, catalog=catalog)
    assert m2 is not None and m2.cell_name == "ddr-tight-test-only"


def test_tie_break_against_seeded_cell_lexical(catalog: Catalog) -> None:
    """Equal-specificity tie between one new cell and a same-shape competitor
    -> lexical name wins (the existing rule, unchanged)."""
    seg = _segment(("J1",), kind="gpio", n_iface_nets=10)
    design = _design({"J1": ("HDR", "")})
    connector_header = catalog.subsystem_cells["connector-header"]
    same_shape_earlier_name = SubsystemCell(
        name="aaa-header-test-only",
        interface=InterfaceSig("gpio", 2, 64),
        composition=(
            CompositionRule("J", "connector", 1, 1),
            CompositionRule("R", "series_pullup", 0, 16),
        ),
    )
    m = match_subsystems(seg, [connector_header, same_shape_earlier_name], design, catalog=catalog)
    assert m is not None and m.cell_name == "aaa-header-test-only"  # lexically first


# --------------------------------------------------------------------------- #
# Determinism.                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cell_name", sorted(NEW_CELL_NAMES))
def test_match_is_deterministic(catalog: Catalog, cell_name: str) -> None:
    cell = catalog.subsystem_cells[cell_name]
    kind = cell.interface.kind
    width = (cell.interface.min_width + cell.interface.max_width) // 2
    seg = _segment(("U1", "R1"), kind=kind, n_iface_nets=width)
    design = _design({"U1": ("X", ""), "R1": ("", "")})
    a = match_subsystem(seg, cell, design, catalog=catalog)
    b = match_subsystem(seg, cell, design, catalog=catalog)
    assert (a is None) == (b is None)
    if a is not None and b is not None:
        assert a.to_dict() == b.to_dict()


def test_full_catalog_match_is_deterministic_across_all_new_cells(catalog: Catalog) -> None:
    segs = [
        _segment(("U1", "R1", "R2", "R3"), kind="ddr", n_iface_nets=28, seg_id="s-ddr"),
        _segment(("J1", "U1"), kind="sdio", n_iface_nets=8, seg_id="s-sdio"),
        _segment(("J1", "R1", "R2", "R3"), kind="gpio", n_iface_nets=10, seg_id="s-gpio"),
        _segment(("U1", "R1"), kind="display", n_iface_nets=16, seg_id="s-display"),
    ]
    design = _design(
        {"U1": ("X", ""), "J1": ("HDR", ""), "R1": ("", ""), "R2": ("", ""), "R3": ("", "")}
    )
    run1 = [match_subsystems(s, catalog.subsystem_cells, design, catalog=catalog) for s in segs]
    run2 = [match_subsystems(s, catalog.subsystem_cells, design, catalog=catalog) for s in segs]
    assert [m.to_dict() if m else None for m in run1] == [m.to_dict() if m else None for m in run2]
    assert [m.cell_name for m in run1] == [
        "ddr-interface",
        "sdio-slot",
        "connector-header",
        "display-header",
    ]
