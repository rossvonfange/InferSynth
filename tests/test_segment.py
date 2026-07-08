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
    load_subsystem_cells,
    promote_result,
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


# --------------------------------------------------------------------------- #
# Rail-attached-passive absorption: decoupling caps fold into their IC's       #
# segment instead of falling out as residual singletons.                      #
# --------------------------------------------------------------------------- #
def _decoupled_ic_design() -> DesignNetlist:
    """One IC cluster (U1, R1, R2, C1) plus three decoupling caps C2/C3/C4 (one
    rail pin + one pin shared onto the cluster's already-claimed ``U1_D`` net,
    so their edge weight to R1/R2 is 1/4 < MERGE_THRESHOLD and they fall out of
    clustering as singletons) and one bulk cap C5 strung purely between VCC and
    GND (no signal net at all). Refdes prefixes are real classes (``C``) —
    ``ref_class`` matches the whole leading alpha run, so multi-letter
    "CDEC"-style refs would misclassify."""
    comps = {r: ("", "") for r in ["U1", "R1", "R2", "C1", "C2", "C3", "C4", "C5"]}
    nets = {
        "U1_A": [("U1", "1"), ("R1", "1")],
        "U1_B": [("U1", "2"), ("R2", "1")],
        "U1_C": [("U1", "3"), ("C1", "1")],
        # already degree-2 (weight 1.0 for R1-R2); 3 caps push it to degree 5
        # (weight 0.25) so none of the cap-R/cap-cap pairs reach MERGE_THRESHOLD.
        "U1_D": [("R1", "2"), ("R2", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2")],
        "VCC": [("C2", "1"), ("C3", "1"), ("C4", "1"), ("C5", "1")],
        "GND": [("C1", "2"), ("U1", "6"), ("C5", "2")],
    }
    return _design(comps, nets)


def test_decoupling_caps_absorbed_into_ic_segment(catalog: Catalog) -> None:
    d = _decoupled_ic_design()
    pre = segment(d, catalog, absorb_passives=False)
    # pre-absorption: the caps are residual singletons, as the bug report says.
    assert set(pre.residual) >= {"C2", "C3", "C4", "C5"}
    assert len(pre.segments) == 1
    assert pre.segments[0].component_refs == ("C1", "R1", "R2", "U1")

    res = segment(d, catalog, absorb_passives=True)
    assert len(res.segments) == 1
    seg = res.segments[0]
    # all 3 decoupling caps folded into the IC's segment ...
    assert seg.component_refs == ("C1", "C2", "C3", "C4", "R1", "R2", "U1")
    assert seg.provenance["absorbed"] == ["C2", "C3", "C4"]
    # ... and the bulk cap (both pins on rails) is left residual, tagged.
    assert "C5" in res.residual
    assert res.residual_tags["C5"] == "rail-only"
    assert res.rail_only_residual == ("C5",)
    assert res.absorbed_count == 3
    assert "C2" not in res.residual
    assert "C3" not in res.residual
    assert "C4" not in res.residual


def test_absorption_does_not_move_anchor_or_merge_segments(catalog: Catalog) -> None:
    """Absorption only grows component_refs — same segment count, same id,
    same anchor set (empty here), nothing merged or moved."""
    d = _decoupled_ic_design()
    pre = segment(d, catalog, absorb_passives=False)
    post = segment(d, catalog, absorb_passives=True)
    assert len(pre.segments) == len(post.segments) == 1
    assert pre.segments[0].id == post.segments[0].id
    assert pre.segments[0].provenance["anchors"] == post.segments[0].provenance["anchors"]
    assert pre.segments[0].internal_nets == post.segments[0].internal_nets
    assert pre.segments[0].boundary == post.segments[0].boundary
    # only component_refs grew
    assert set(pre.segments[0].component_refs) < set(post.segments[0].component_refs)


def test_absorb_passives_false_restores_old_behavior(catalog: Catalog) -> None:
    d = _decoupled_ic_design()
    res = segment(d, catalog, absorb_passives=False)
    assert {"C2", "C3", "C4", "C5"} <= set(res.residual)
    assert res.residual_tags == {}
    assert res.absorbed_count == 0


def test_absorption_is_deterministic(catalog: Catalog) -> None:
    d = _decoupled_ic_design()
    a = segment(d, catalog, absorb_passives=True)
    b = segment(d, catalog, absorb_passives=True)
    assert a.to_json() == b.to_json()


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

    res = segment(design, catalog)  # absorb_passives=True by default
    n_comp = len(design.components)
    sizes = sorted((len(s.component_refs) for s in res.segments), reverse=True)
    largest = sizes[0] if sizes else 0

    # (1) SANE cluster count — tens of clusters, not 1 blob, not 562 singletons.
    assert 8 <= len(res.segments) <= 120, sizes
    # (2) no rail-driven mega-merge: the largest cluster is well under half the board.
    assert largest < n_comp * 0.4, (largest, n_comp)
    # (3) determinism on the real board
    assert segment(design, catalog).to_json() == res.to_json()

    # honest report of the recognize/promote/residual split. Flat small-cell
    # recognition still finds nothing (no MPN in the .brd) — but the SUBSYSTEM
    # tier now recognizes the interface-bounded segments (i2c / led / diff_pair
    # clusters): the payoff, recognize > 0 on a real vendor board.
    hres = hierarchical_recognize(design, catalog)
    assert len(hres.per_segment) == len(res.segments)
    assert len(hres.recognized_small) == 0  # no MPN => flat recognizer fires on nothing
    assert len(hres.recognized_subsystem) > 0, "subsystem tier should recognize >0 segments"
    recognized_cells = {s.subsystem_match.cell_name for s in hres.recognized_subsystem}
    # the LED banks and the I2C bus are the reliably-present wins on this board.
    assert "led-bank" in recognized_cells
    # many segments (DDR, the FPGA core) still promote — expected, no cell yet.
    assert len(hres.promoted_candidates) > 0
    # a large residual is expected (decoupling caps on rails, FPGA/DDR glue).
    assert len(hres.residual) > 0

    # ------------------------------------------------------------------- #
    # Rail-attached-passive absorption payoff: before/after on the real   #
    # board. Pre-absorption baseline is ~345/562 residual singletons      #
    # (overwhelmingly decoupling caps); absorption should fold most of    #
    # those into their IC's segment without exploding segment count or    #
    # moving any anchor.                                                  #
    # ------------------------------------------------------------------- #
    pre = segment(design, catalog, absorb_passives=False)
    n_pre_residual = len(pre.residual)
    n_pre_segments = len(pre.segments)
    pre_anchors = {s.id: s.provenance["anchors"] for s in pre.segments}
    pre_largest = max((len(s.component_refs) for s in pre.segments), default=0)

    n_post_residual = len(res.residual)
    n_post_segments = len(res.segments)
    post_anchors = {s.id: s.provenance["anchors"] for s in res.segments}
    post_largest = max((len(s.component_refs) for s in res.segments), default=0)

    print(
        f"\n[polarfire absorption] residual {n_pre_residual} -> {n_post_residual} "
        f"(absorbed={res.absorbed_count}, rail_only={len(res.rail_only_residual)}); "
        f"segments {n_pre_segments} -> {n_post_segments}; "
        f"largest {pre_largest} -> {post_largest}"
    )

    # confirms the ~345 baseline this task set out to fix, and that absorption
    # actually moves the needle. On this board most decoupling is a direct
    # rail-to-rail bypass cap (VDD net to GND, no unique local/signal net at
    # all) — topologically indistinguishable from any other cap on that same
    # rail pair, so it has no honest single owner and correctly stays
    # "rail-only" residual rather than being force-assigned. The absorbable
    # subset (one rail pin + one genuinely local pin) is smaller but real:
    # observed on this board, 345 -> 258 residual (87 absorbed, 236 of the
    # remaining 258 rail-only-tagged, the rest genuine unexplained glue).
    assert n_pre_residual > 300, n_pre_residual
    assert res.absorbed_count >= 50, res.absorbed_count
    assert n_post_residual < n_pre_residual - 50, (n_pre_residual, n_post_residual)
    assert n_post_residual < 300, (n_pre_residual, n_post_residual, res.absorbed_count)
    # every non-absorbed residual ref is now honestly accounted for: either
    # rail-only tagged, or genuine (not a rail-attached-passive at all).
    assert res.absorbed_count == n_pre_residual - n_post_residual

    # absorption is pure enrichment: same segment ids/anchors, no fragmentation.
    assert n_post_segments == n_pre_segments
    assert post_anchors == pre_anchors
    # the largest segment only grows by absorbed refs, never a new merge.
    assert post_largest >= pre_largest
    assert post_largest < n_comp * 0.4, (post_largest, n_comp)

    # determinism holds for the absorption pass on the real board too.
    assert segment(design, catalog, absorb_passives=True).to_json() == res.to_json()


@pytest.mark.kicad
@pytest.mark.slow
@pytest.mark.skipif(_KICAD is None, reason="kicad-cli not on PATH")
@pytest.mark.skipif(not _BRD.is_file(), reason="PolarFire .brd reference absent")
def test_polarfire_promote_closes_the_foundry_loop(tmp_path: Path, catalog: Catalog) -> None:
    """THE FOUNDRY LOOP: promote the board's unrecognized segments into generated
    subsystem_cell.yaml stubs, drop them into the catalog, and re-recognize the
    SAME board — the previously-promoted segments now RECOGNIZE. promote -> cell
    -> recognize, closed on a real vendor board."""
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

    # BEFORE: recognize the board against the seeded catalog.
    before = hierarchical_recognize(design, catalog)
    n_recog_before = len(before.recognized_subsystem)
    n_promoted_before = len(before.promoted_candidates)
    assert n_promoted_before > 0, "expected promotable segments on the PolarFire board"

    # PROMOTE: every unrecognized segment -> a reviewable subsystem_cell.yaml.
    promoted_dir = tmp_path / "promoted"
    written = promote_result(before, design, promoted_dir, board_hint="polarfire-discovery")
    n_cells = len(written)
    assert n_cells > 0, "promotion should generate >=1 subsystem cell"
    # distinct shapes generated (DDR interface / gpio bank / display / sdio ...)
    gen_cells = load_subsystem_cells(promoted_dir)
    gen_kinds = sorted({c.interface.kind for c in gen_cells.values()})

    # every generated stub is marked provisional / needs-review.
    import yaml

    for path in written:
        data = yaml.safe_load(path.read_text())
        assert data["provenance"]["needs_review"] is True
        assert data["provenance"]["generated_by"] == "promote/v0"
    assert (promoted_dir / "PROMOTED.md").is_file()

    # DROP the generated cells into the catalog (union with the seeded ones).
    catalog2 = Catalog.load(CATALOG_DIR)
    catalog2.subsystem_cells = {**catalog2.subsystem_cells, **gen_cells}

    # RE-RECOGNIZE the SAME board: promoted segments now recognize.
    after = hierarchical_recognize(design, catalog2)
    n_recog_after = len(after.recognized_subsystem)
    n_promoted_after = len(after.promoted_candidates)

    print(
        f"\n[polarfire foundry loop] generated {n_cells} distinct subsystem "
        f"cell(s) {gen_kinds}; recognized_subsystem {n_recog_before} -> "
        f"{n_recog_after}; promoted {n_promoted_before} -> {n_promoted_after}"
    )

    # THE LOOP CLOSING: recognition jumps, promotion drops, by construction.
    assert n_recog_after > n_recog_before, (n_recog_before, n_recog_after)
    assert n_promoted_after < n_promoted_before, (n_promoted_before, n_promoted_after)
    # segmentation is unchanged, so every previously-promoted interface segment
    # that yielded a cell is now recognized: recognized gains >= distinct cells.
    assert n_recog_after - n_recog_before >= n_cells, (
        n_recog_before, n_recog_after, n_cells
    )
    # determinism of promotion on the real board.
    written2 = promote_result(
        before, design, tmp_path / "promoted2", board_hint="polarfire-discovery"
    )
    assert [p.read_text() for p in written] == [p.read_text() for p in written2]
