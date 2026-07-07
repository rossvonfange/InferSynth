"""Tests for the netlist segmenter + hierarchical recognition
(docs/HIERARCHICAL_RECOGNITION.md, Contract 2 + the pipeline).

Toolchain-free coverage: interface-bundle cutting (i2c), determinism, rail-net
non-merging, label-hint biasing, and hierarchical recognize-vs-promote on a
design carrying one catalog cell + one novel cluster. Kicad-gated: the real
PolarFire ``.brd`` payoff test — import via kicad-cli, parse to a DesignNetlist
with a small self-contained parser (mirroring Loom's allegro adapter, which we
cannot import), segment it, and assert a SANE cluster count (not 1 blob, not
562 singletons) with no rail-driven mega-merge.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.recognize import (
    LabelClaim,
    classify_nets,
    hierarchical_recognize,
    restrict_netlist,
    segment,
)
from infersynth.recognize.netlist import Component, DesignNetlist

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"
OPA_MPN = "OPA340NA/250"
R_MPN = "RC0603FR-0710KL"
NONINV = "core/opamp-gain-noninverting@0.1.0"

_KICAD = shutil.which("kicad-cli")
_BRD = Path(
    "/home/cycix/Desktop/fai-tuner/golden-box/pfdsc_carrier/references/"
    "PolarFire_SoC_Discovery_Kit_Rev2_23_0954_PCB_091923.brd"
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG_DIR)


def _design(
    comps: dict[str, tuple[str, str]], nets: dict[str, list[tuple[str, str]]]
) -> DesignNetlist:
    """*comps*: ref -> (value_str, mpn)."""
    components = {
        ref: Component.build(ref, val, "fp", mpn) for ref, (val, mpn) in comps.items()
    }
    pin_net = {(r, p): name for name, pins in nets.items() for r, p in pins}
    return DesignNetlist(components=components, nets=dict(nets), pin_net=pin_net)


# --------------------------------------------------------------------------- #
# Two IC clusters joined by an i2c bundle -> cut at the i2c boundary.          #
# --------------------------------------------------------------------------- #
def _two_cluster_design() -> DesignNetlist:
    comps = {r: ("", "") for r in ["U1", "R1", "R2", "C1", "U2", "R3", "R4", "C2"]}
    nets = {
        # cluster A around U1 (dedicated 2-pin nets bind tightly)
        "U1_A": [("U1", "1"), ("R1", "1")],
        "U1_B": [("U1", "2"), ("R2", "1")],
        "U1_C": [("U1", "3"), ("C1", "1")],
        "U1_D": [("R1", "2"), ("R2", "2")],
        # cluster B around U2
        "U2_A": [("U2", "1"), ("R3", "1")],
        "U2_B": [("U2", "2"), ("R4", "1")],
        "U2_C": [("U2", "3"), ("C2", "1")],
        "U2_D": [("R3", "2"), ("R4", "2")],
        # the i2c bundle crossing between them — the CUT
        "IIC_SDA": [("U1", "4"), ("U2", "4")],
        "IIC_SCL": [("U1", "5"), ("U2", "5")],
        # a shared ground rail touching everything
        "GND": [("C1", "2"), ("C2", "2"), ("U1", "6"), ("U2", "6"), ("R1", "3")],
    }
    return _design(comps, nets)


def test_i2c_bundle_is_cut(catalog: Catalog) -> None:
    d = _two_cluster_design()
    nc = classify_nets(d, catalog)
    assert nc["IIC_SDA"].kind == "interface" and nc["IIC_SDA"].interface_kind == "i2c"
    assert nc["IIC_SCL"].interface_kind == "i2c"
    assert nc["GND"].kind == "rail"
    res = segment(d, catalog)
    # exactly two clusters, split on the i2c bundle
    assert len(res.segments) == 2
    members = sorted(tuple(s.component_refs) for s in res.segments)
    assert members == [("C1", "R1", "R2", "U1"), ("C2", "R3", "R4", "U2")]
    # each segment reports the i2c interface on its boundary
    assert all(s.interface_kind == "i2c" for s in res.segments)
    # the i2c nets appear as boundary pins, not internal nets
    for s in res.segments:
        assert "IIC_SDA" not in s.internal_nets
        assert any(b.net == "IIC_SDA" and b.interface_kind == "i2c" for b in s.boundary)


def test_determinism_byte_identical(catalog: Catalog) -> None:
    d = _two_cluster_design()
    assert segment(d, catalog).to_json() == segment(d, catalog).to_json()


def test_rail_net_does_not_merge_clusters(catalog: Catalog) -> None:
    """A GND net touching every component must NOT collapse the two clusters."""
    d = _two_cluster_design()
    res = segment(d, catalog)
    # GND touches all 8 comps; if it were a clustering edge we'd get 1 segment.
    assert len(res.segments) == 2
    # GND is never an internal net of any segment
    for s in res.segments:
        assert "GND" not in s.internal_nets


def test_high_degree_unnamed_net_is_rail(catalog: Catalog) -> None:
    """A net with no power name but very high degree is still treated as a rail
    (broadcast), so it doesn't merge the board."""
    comps = {f"C{i}": ("", "") for i in range(10)}
    nets = {"BROADCAST": [(f"C{i}", "1") for i in range(10)]}  # degree 10 >= RAIL_DEGREE
    d = _design(comps, nets)
    nc = classify_nets(d, catalog)
    assert nc["BROADCAST"].kind == "rail"
    res = segment(d, catalog)
    assert len(res.segments) == 0
    assert len(res.residual) == 10


# --------------------------------------------------------------------------- #
# Label-hint biasing: a block label refines a borderline assignment.          #
# --------------------------------------------------------------------------- #
def test_label_hint_biases_cut(catalog: Catalog) -> None:
    """A borderline passive weakly bound to two anchors goes to whichever the
    block label names — connectivity ties, the label breaks the tie."""
    comps = {r: ("", "") for r in ["U1", "U2", "Rx"]}
    nets = {
        # Rx shares one 3-way node with U1 and one with U2 (symmetric, weight 0.5 each)
        "NA": [("U1", "1"), ("Rx", "1"), ("U1", "9")],
        "NB": [("U2", "1"), ("Rx", "1"), ("U2", "9")],
        # make U1, U2 anchors (local degree >= 4)
        **{f"U1_p{i}": [("U1", f"1{i}")] for i in range(4)},
        **{f"U2_p{i}": [("U2", f"2{i}")] for i in range(4)},
    }
    d = _design(comps, nets)
    # without a label, Rx attaches deterministically (to the lexicographically
    # first winner). With a block label naming U2+Rx, it must go to U2's segment.
    res = segment(d, catalog, labels=[LabelClaim(kind="block", value="blkU2", refs=("U2", "Rx"))])
    seg_of = {r: s for s in res.segments for r in s.component_refs}
    assert seg_of["Rx"].id == seg_of["U2"].id
    assert seg_of["Rx"].label == "blkU2"


def test_labels_none_is_baseline(catalog: Catalog) -> None:
    """labels=None must work (connectivity-only v0 baseline)."""
    d = _two_cluster_design()
    a = segment(d, catalog, labels=None)
    b = segment(d, catalog)
    assert a.to_json() == b.to_json()


# --------------------------------------------------------------------------- #
# Hierarchical recognize: one segment recognizes a catalog cell, one promotes. #
# --------------------------------------------------------------------------- #
def test_hierarchical_recognizes_and_promotes(catalog: Catalog) -> None:
    # cluster A: the exact known-good non-inverting opamp gain topology
    # (MPN-anchored on U1) — must recognize when scoped to its segment.
    comps = {
        "R1": ("99k", R_MPN),
        "R2": ("1k", R_MPN),
        "U1": ("OPAMP_SINGLE", OPA_MPN),
        # cluster B: a novel 2-IC cluster with no catalog match, joined by i2c
        "U2": ("MYSTERY", ""),
        "U3": ("MYSTERY", ""),
        "R5": ("10k", ""),
    }
    nets = {
        "/FB": [("R1", "2"), ("R2", "1"), ("U1", "4")],
        "/GND": [("R2", "2")],
        "/IN": [("U1", "3")],
        "/OUT": [("R1", "1"), ("U1", "1")],
        "/VCC": [("U1", "5")],
        "/VEE": [("U1", "2")],
        "B_LOCA": [("U2", "1"), ("R5", "1")],
        "B_LOCB": [("U2", "2"), ("U3", "1")],
        "B_LOCC": [("U3", "2"), ("R5", "2")],
        "B_LOCD": [("U2", "3"), ("U3", "3")],
        "B_LOCE": [("U2", "7"), ("U3", "7")],
        # i2c bundle from cluster A's opamp region to cluster B — the CUT
        "LINK_SDA": [("U1", "8"), ("U2", "8")],
        "LINK_SCL": [("U1", "9"), ("U2", "9")],
    }
    d = _design(comps, nets)
    result = hierarchical_recognize(d, catalog)
    # the opamp cluster recognizes the non-inverting gain cell
    assert NONINV in {
        k for s in result.recognized_segments for k in s.recognition.recognized_cell_keys
    }
    # the novel cluster is promoted to a provisional candidate cell
    assert len(result.promoted_candidates) >= 1
    cand = result.promoted_candidates[0]
    assert cand.suggested_name.startswith("cand-")
    assert set(cand.component_refs) & {"U2", "U3", "R5"}


def test_restrict_netlist_scopes_to_segment() -> None:
    d = _two_cluster_design()
    sub = restrict_netlist(d, ("U1", "R1", "R2", "C1"))
    assert set(sub.components) == {"U1", "R1", "R2", "C1"}
    # the i2c boundary net appears filtered to only the in-segment pin
    assert sub.nets.get("IIC_SDA") == [("U1", "4")]
    # a net entirely outside the segment is dropped
    assert "U2_A" not in sub.nets


# --------------------------------------------------------------------------- #
# THE PAYOFF TEST — the real PolarFire board (kicad-gated, slow).             #
# --------------------------------------------------------------------------- #
def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            yield (c, "")
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if cj == '"':
                    break
                buf.append(cj)
                j += 1
            yield ("str", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            yield ("atom", text[i:j])
            i = j


def _read_node(tk):
    node: list = []
    for kind, val in tk:
        if kind == "(":
            node.append(_read_node(tk))
        elif kind == ")":
            return node
        else:
            node.append(val)
    return node


def _head(node) -> str:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def _design_netlist_from_pcb(pcb_path: Path) -> DesignNetlist:
    """Minimal .kicad_pcb -> DesignNetlist (mirrors Loom's allegro adapter,
    which we cannot import here). Footprints -> components; pads -> nets."""
    tk = _tokens(pcb_path.read_text(errors="replace"))
    for kind, _ in tk:
        if kind == "(":
            next(tk, None)  # consume the kicad_pcb head
            break
    comps: dict[str, Component] = {}
    nets: dict[str, list[tuple[str, str]]] = {}
    for kind, _ in tk:
        if kind == ")":
            break
        if kind != "(":
            continue
        child = _read_node(tk)
        if _head(child) != "footprint":
            continue
        ref = value = ""
        pads: list[tuple[str, str]] = []
        for c in child:
            if not isinstance(c, list):
                continue
            h = _head(c)
            if h == "property" and len(c) >= 3:
                if c[1] == "Reference" and not ref and c[2] not in ("${REFERENCE}", "~", "") \
                        and not c[2].startswith("$"):
                    ref = c[2]
                elif c[1] == "Value" and not value:
                    value = c[2]
            elif h == "pad" and len(c) >= 2 and isinstance(c[1], str):
                pn, net = c[1], ""
                for sub in c:
                    if isinstance(sub, list) and _head(sub) == "net":
                        strs = [x for x in sub[1:] if isinstance(x, str)]
                        if strs:
                            net = strs[-1]
                        break
                if pn and net:
                    pads.append((pn, net))
        if not ref or ref.startswith("#") or ref in comps:
            continue
        comps[ref] = Component.build(ref, value, "", "")
        for pn, net in pads:
            nets.setdefault(net, []).append((ref, pn))
    comps = dict(sorted(comps.items()))
    nets = {name: sorted(set(pins)) for name, pins in sorted(nets.items())}
    pin_net = {(r, p): name for name, pins in nets.items() for r, p in pins}
    return DesignNetlist(components=comps, nets=nets, pin_net=pin_net)


@pytest.mark.kicad
@pytest.mark.slow
@pytest.mark.skipif(_KICAD is None, reason="kicad-cli not on PATH")
@pytest.mark.skipif(not _BRD.is_file(), reason="PolarFire .brd reference absent")
def test_polarfire_board_segments_sanely(tmp_path: Path, catalog: Catalog) -> None:
    out_pcb = tmp_path / "pf.kicad_pcb"
    proc = subprocess.run(
        ["kicad-cli", "pcb", "import", "--format", "auto", "-o", str(out_pcb), str(_BRD)],
        capture_output=True,
        text=True,
        timeout=420,
    )
    if not out_pcb.is_file():
        pytest.skip(f"kicad-cli import failed: {(proc.stderr or proc.stdout).strip()}")

    design = _design_netlist_from_pcb(out_pcb)
    # sanity on the parse itself (documented board shape)
    assert len(design.components) > 400
    assert len(design.nets) > 300

    res = segment(design, catalog)
    n_comp = len(design.components)
    sizes = sorted((len(s.component_refs) for s in res.segments), reverse=True)
    largest = sizes[0] if sizes else 0

    # (1) SANE cluster count — tens of clusters, not 1 blob, not 562 singletons.
    assert 8 <= len(res.segments) <= 120, sizes
    # (2) no rail-driven mega-merge: the largest cluster is well under half the board.
    assert largest < n_comp * 0.4, (largest, n_comp)
    # (3) determinism on the real board
    assert segment(design, catalog).to_json() == res.to_json()

    # honest report of the recognize/promote/residual split (recognition finds
    # nothing — no MPN in the .brd — so every segment promotes; that's expected).
    hres = hierarchical_recognize(design, catalog)
    assert len(hres.per_segment) == len(res.segments)
    # a large residual is expected (decoupling caps on rails, FPGA/DDR glue).
    assert len(hres.residual) > 0
