"""Tests for promote-to-subsystem-cell (:mod:`infersynth.recognize.promote`) —
the cell foundry loop: a segment promoted on one board becomes a subsystem cell
recognized on the next.

Toolchain-free coverage: candidate->cell inference (interface/composition/name),
the ROUND-TRIP guarantee (a generated cell, loaded + matched against its origin
segment, recognizes it), width/count band widening, dedup of identical shapes,
provisional/needs-review marking, determinism, and multi-candidate
``promote_result``. The kicad/slow PolarFire re-recognition (loop-closing) lives
in ``test_segment.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.recognize import (
    candidate_to_subsystem_cell,
    load_subsystem_cell,
    load_subsystem_cells,
    match_subsystem,
    promote_result,
    shape_key,
)
from infersynth.recognize.hierarchical import CandidateCell
from infersynth.recognize.netlist import Component, DesignNetlist
from infersynth.recognize.promote import _band
from infersynth.recognize.segment import BoundaryPin, Segment


# --------------------------------------------------------------------------- #
# Builders                                                                     #
# --------------------------------------------------------------------------- #
def _design(comps: dict[str, tuple[str, str]]) -> DesignNetlist:
    """*comps*: ref -> (value_str, mpn)."""
    components = {r: Component.build(r, v, "fp", m) for r, (v, m) in comps.items()}
    return DesignNetlist(components=components, nets={}, pin_net={})


def _segment(
    refs: tuple[str, ...],
    *,
    seg_id: str = "seg-000",
    kind: str | None = "ddr",
    nets: tuple[str, ...] = ("DDR_DQ0", "DDR_DQ1", "DDR_DQ2", "DDR_DQ3"),
    label: str | None = None,
) -> Segment:
    boundary = tuple(
        BoundaryPin(ref=refs[0], pin=str(i), net=n, interface_kind=kind)
        for i, n in enumerate(nets)
    )
    return Segment(
        id=seg_id,
        component_refs=refs,
        internal_nets=(),
        boundary=boundary,
        interface_kind=kind,
        label=label,
    )


def _candidate(seg: Segment) -> CandidateCell:
    return CandidateCell(
        suggested_name=f"cand-{seg.id}",
        segment_id=seg.id,
        component_refs=seg.component_refs,
        interface_kind=seg.interface_kind,
        label=seg.label,
        boundary_kinds=(seg.interface_kind,) if seg.interface_kind else (),
    )


class _FakeSegmentation:
    def __init__(self, segments: tuple[Segment, ...]) -> None:
        self.segments = segments


class _FakeHResult:
    """Minimal stand-in exposing the two attributes promote_result reads:
    ``promoted_candidates`` and ``segmentation.segments``."""

    def __init__(self, pairs: list[tuple[Segment, CandidateCell]]) -> None:
        self.segmentation = _FakeSegmentation(tuple(s for s, _ in pairs))
        self.promoted_candidates = tuple(c for _, c in pairs)


# --------------------------------------------------------------------------- #
# Band widening                                                                #
# --------------------------------------------------------------------------- #
def test_band_straddles_and_widens() -> None:
    # always includes the observed value (for observed >= floor)
    for obs in (1, 2, 8, 32):
        lo, hi = _band(obs, frac=0.25, min_tol=1, floor=1)
        assert lo <= obs <= hi
    # a wide bus widens proportionally (32 -> +/-8), a narrow one by the floor
    assert _band(32, frac=0.25, min_tol=1, floor=1) == (24, 40)
    assert _band(2, frac=0.25, min_tol=1, floor=1) == (1, 3)
    # count band is wider (50%) and floored at 0 so a class can go optional
    assert _band(1, frac=0.5, min_tol=1, floor=0) == (0, 2)
    assert _band(8, frac=0.5, min_tol=1, floor=0) == (4, 12)


# --------------------------------------------------------------------------- #
# Interface / composition / name inference                                    #
# --------------------------------------------------------------------------- #
def test_interface_inference_width_band() -> None:
    seg = _segment(("U1", "R1", "R2"))  # 4 ddr nets on the boundary
    cell = candidate_to_subsystem_cell(_candidate(seg), seg, _design(
        {"U1": ("MEM", ""), "R1": ("100", ""), "R2": ("100", "")}
    ))
    assert cell["kind"] == "subsystem"
    assert cell["interface"]["kind"] == "ddr"
    # observed width 4 -> band [3, 5] straddling observed
    assert cell["interface"]["min_width"] <= 4 <= cell["interface"]["max_width"]
    assert cell["interface"] == {"kind": "ddr", "min_width": 3, "max_width": 5}


def test_composition_inference_one_rule_per_class() -> None:
    seg = _segment(("U1", "R1", "R2", "C1"))
    cell = candidate_to_subsystem_cell(_candidate(seg), seg, _design(
        {"U1": ("MEM", ""), "R1": ("100", ""), "R2": ("100", ""), "C1": ("100n", "")}
    ))
    rules = {r["class"]: r for r in cell["composition"]}
    assert set(rules) == {"U", "R", "C"}
    # counts: U=1, R=2, C=1 -> each band straddles observed
    assert rules["U"]["count"][0] <= 1 <= rules["U"]["count"][1]
    assert rules["R"]["count"][0] <= 2 <= rules["R"]["count"][1]
    # roles assigned from the ddr interface kind
    assert rules["U"]["role"] == "memory"
    assert rules["R"]["role"] == "termination"
    # composition is class-sorted (deterministic)
    assert [r["class"] for r in cell["composition"]] == ["C", "R", "U"]


def test_name_inference_from_kind_and_label() -> None:
    seg_k = _segment(("U1",), kind="gpio")
    cell_k = candidate_to_subsystem_cell(_candidate(seg_k), seg_k, _design({"U1": ("IO", "")}))
    assert cell_k["name"] == "gpio-bank"

    seg_sdio = _segment(("U1",), kind="sdio")
    cell_sdio = candidate_to_subsystem_cell(
        _candidate(seg_sdio), seg_sdio, _design({"U1": ("SD", "")})
    )
    assert cell_sdio["name"] == "sdio-slot"

    seg_l = _segment(("U1",), kind="display", label="LCD Header")
    cell_l = candidate_to_subsystem_cell(
        _candidate(seg_l), seg_l, _design({"U1": ("LCD", "")})
    )
    assert cell_l["name"] == "lcd-header"  # label wins, slugged


def test_anchor_from_member_mpns_else_null() -> None:
    seg = _segment(("U1", "R1"))
    with_mpn = candidate_to_subsystem_cell(
        _candidate(seg), seg, _design({"U1": ("MEM", "MT41K256"), "R1": ("100", "")})
    )
    assert with_mpn["anchor"] == {"mpn_patterns": ["MT41K256"]}
    no_mpn = candidate_to_subsystem_cell(
        _candidate(seg), seg, _design({"U1": ("MEM", ""), "R1": ("100", "")})
    )
    assert no_mpn["anchor"] is None  # a .brd with no MPN -> anchor: null


def test_provisional_marking() -> None:
    seg = _segment(("U1",))
    cell = candidate_to_subsystem_cell(
        _candidate(seg), seg, _design({"U1": ("MEM", "")}), board_hint="board-1"
    )
    prov = cell["provenance"]
    assert prov["generated_by"] == "promote/v0"
    assert prov["needs_review"] is True
    assert prov["from_board"] == "board-1"
    assert prov["observed"]["segment_id"] == "seg-000"
    assert prov["observed"]["width"] == 4
    assert "PROVISIONAL" in cell["description"]


def test_interfaceless_segment_rejected() -> None:
    seg = _segment(("U1",), kind=None, nets=())
    with pytest.raises(ValueError, match="no interface_kind"):
        candidate_to_subsystem_cell(_candidate(seg), seg, _design({"U1": ("X", "")}))


def test_determinism() -> None:
    seg = _segment(("U1", "R1", "R2"))
    design = _design({"U1": ("MEM", "MT41K"), "R1": ("100", ""), "R2": ("100", "")})
    a = candidate_to_subsystem_cell(_candidate(seg), seg, design)
    b = candidate_to_subsystem_cell(_candidate(seg), seg, design)
    assert a == b


# --------------------------------------------------------------------------- #
# THE round-trip guarantee: a generated cell recognizes its own origin.        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kind,nets,refs,mpn",
    [
        ("ddr", tuple(f"DDR_DQ{i}" for i in range(8)), ("U1", "R1", "R2", "R3"), ""),
        ("i2c", ("SDA", "SCL"), ("U1", "R1", "R2"), ""),
        ("led", ("LED0", "LED1", "LED2", "LED3"), ("D1", "D2", "R1", "R2"), ""),
        ("gpio", tuple(f"GPIO{i}" for i in range(6)), ("U1", "R1"), "STM32"),
    ],
)
def test_roundtrip_generated_cell_matches_origin(
    tmp_path: Path, kind: str, nets: tuple[str, ...], refs: tuple[str, ...], mpn: str
) -> None:
    seg = _segment(refs, kind=kind, nets=nets)
    design = _design({r: ("100", mpn if r.startswith("U") else "") for r in refs})
    cell = candidate_to_subsystem_cell(_candidate(seg), seg, design)

    # write it as a real package and load it back through the validator
    d = tmp_path / cell["name"]
    d.mkdir()
    import yaml

    (d / "subsystem_cell.yaml").write_text(yaml.safe_dump(cell, sort_keys=False))
    loaded = load_subsystem_cell(d)

    # the loaded cell recognizes the ORIGINAL segment
    match = match_subsystem(seg, loaded, design)
    assert match is not None, f"{kind} cell failed to recognize its origin segment"
    assert match.cell_name == cell["name"]
    assert match.interface_kind == kind


# --------------------------------------------------------------------------- #
# promote_result: multi-candidate, dedup, files, PROMOTED.md                   #
# --------------------------------------------------------------------------- #
def test_promote_result_multi_candidate_and_dedup(tmp_path: Path) -> None:
    # two IDENTICAL-shape ddr segments (same width, same composition) + one gpio
    ddr_nets = tuple(f"DDR_DQ{i}" for i in range(4))
    seg_a = _segment(("U1", "R1"), seg_id="seg-000", kind="ddr", nets=ddr_nets)
    seg_b = _segment(("U2", "R2"), seg_id="seg-001", kind="ddr", nets=ddr_nets)
    gpio_nets = tuple(f"GPIO{i}" for i in range(4))
    seg_c = _segment(("U3", "R3"), seg_id="seg-002", kind="gpio", nets=gpio_nets)
    # one interface-less glue cluster -> skipped
    seg_glue = _segment(("U4",), seg_id="seg-003", kind=None, nets=())

    pairs = [(s, _candidate(s)) for s in (seg_a, seg_b, seg_c, seg_glue)]
    design = _design({f"U{i}": ("X", "") for i in range(1, 5)}
                     | {f"R{i}": ("100", "") for i in range(1, 4)})
    hres = _FakeHResult(pairs)

    written = promote_result(hres, design, tmp_path, board_hint="board-1")
    # two distinct shapes (ddr, gpio); the two ddr segments dedup to one cell
    assert len(written) == 2
    names = {p.parent.name for p in written}
    assert names == {"ddr-interface", "gpio-bank"}

    # the deduped ddr cell notes BOTH source segments
    import yaml

    ddr_yaml = yaml.safe_load((tmp_path / "ddr-interface" / "subsystem_cell.yaml").read_text())
    assert ddr_yaml["provenance"]["from_segments"] == ["seg-000", "seg-001"]

    md = (tmp_path / "PROMOTED.md").read_text()
    assert "distinct subsystem cells generated: **2**" in md
    assert "candidates skipped (no interface_kind): **1**" in md
    assert "seg-003" in md  # the skipped glue cluster is reported

    # the whole output dir loads as a subsystem catalog (round-trip via loader)
    cells = load_subsystem_cells(tmp_path)
    assert set(cells) == {"ddr-interface", "gpio-bank"}


def test_promote_result_name_collision_distinct_shapes(tmp_path: Path) -> None:
    # two ddr segments of DIFFERENT width -> distinct shapes, same base name
    seg_a = _segment(("U1", "R1"), seg_id="seg-000", kind="ddr",
                     nets=tuple(f"A{i}" for i in range(4)))
    seg_b = _segment(("U2", "R2"), seg_id="seg-001", kind="ddr",
                     nets=tuple(f"B{i}" for i in range(16)))
    design = _design({"U1": ("X", ""), "U2": ("X", ""), "R1": ("100", ""), "R2": ("100", "")})
    hres = _FakeHResult([(seg_a, _candidate(seg_a)), (seg_b, _candidate(seg_b))])
    written = promote_result(hres, design, tmp_path, board_hint="b")
    assert len(written) == 2
    names = sorted(p.parent.name for p in written)
    assert names == ["ddr-interface", "ddr-interface-2"]  # collision disambiguated
    # both still load
    assert set(load_subsystem_cells(tmp_path)) == {"ddr-interface", "ddr-interface-2"}


def test_promote_result_deterministic(tmp_path: Path) -> None:
    seg = _segment(("U1", "R1"), kind="sdio", nets=tuple(f"SD{i}" for i in range(4)))
    design = _design({"U1": ("SD", ""), "R1": ("100", "")})
    hres = _FakeHResult([(seg, _candidate(seg))])
    p1 = promote_result(hres, design, tmp_path / "a", board_hint="b")
    p2 = promote_result(hres, design, tmp_path / "b", board_hint="b")
    assert [p.read_text() for p in p1] == [p.read_text() for p in p2]


def test_shape_key_ignores_name_and_provenance() -> None:
    seg = _segment(("U1", "R1"), kind="ddr")
    design = _design({"U1": ("X", ""), "R1": ("100", "")})
    c1 = candidate_to_subsystem_cell(_candidate(seg), seg, design, board_hint="b1")
    c2 = candidate_to_subsystem_cell(_candidate(seg), seg, design, board_hint="b2")
    c2 = {**c2, "name": "renamed"}
    assert shape_key(c1) == shape_key(c2)  # same shape despite name/board diff
