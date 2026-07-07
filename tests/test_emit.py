"""Direct-emitter tests (BUILD_PLAN WP3).

Covers the ``format_value`` helper, the byte-preserving instantiation surgery,
provenance stamping, and — under ``@pytest.mark.kicad`` — the golden end-to-end
assertion that ``kicad-cli sch erc`` *parses* the emitted hierarchy (ERC
violations are allowed; a parse failure is not).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.catalog.loader import load_cell
from infersynth.compile_kicad import emit

CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "core"
CELL1 = CATALOG / "opamp-gain-noninverting"
CELL2 = CATALOG / "opamp-gain-x4-noninverting"

_SLOT_RE = re.compile(r"\$\{IS\.[^}]+\}")


def test_format_value_examples() -> None:
    # The spec's worked examples.
    assert emit.format_value(1000.0) == "1k"
    assert emit.format_value(99000) == "99k"
    assert emit.format_value(470.0) == "470"
    # k with a decimal mantissa, and the M decade.
    assert emit.format_value(4700) == "4.7k"
    assert emit.format_value(4.7e3) == "4.7k"
    assert emit.format_value(1_000_000) == "1M"
    assert emit.format_value(2.2e6) == "2.2M"
    # sub-kilohm stays plain; trailing zeros trimmed.
    assert emit.format_value(100.0) == "100"
    assert emit.format_value(0) == "0"


def test_new_design_creates_root_and_project(tmp_path: Path) -> None:
    root = emit.new_design(tmp_path, "demo")
    assert root == tmp_path / "demo.kicad_sch"
    assert root.is_file()
    assert (tmp_path / "demo.kicad_pro").is_file()
    text = root.read_text()
    assert text.startswith("(kicad_sch")
    assert "(sheet_instances" in text
    assert '(page "1")' in text


def _instantiate_both(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = emit.new_design(tmp_path, "demo")
    c1 = load_cell(CELL1)
    c2 = load_cell(CELL2)
    p1 = emit.instantiate(c1, {"gain": 100}, "gain_a", tmp_path, root)
    p2 = emit.instantiate(
        c2,
        {"gain1": 10, "gain2": 10, "gain3": 10, "gain4": 10, "channels": 4},
        "quad_a",
        tmp_path,
        root,
    )
    return root, p1, p2


def test_instantiate_file_set(tmp_path: Path) -> None:
    root, p1, p2 = _instantiate_both(tmp_path)
    assert p1 == tmp_path / "gain_a.kicad_sch"
    assert p2 == tmp_path / "quad_a.kicad_sch"
    names = {f.name for f in tmp_path.iterdir()}
    assert names == {
        "demo.kicad_sch",
        "demo.kicad_pro",
        "gain_a.kicad_sch",
        "quad_a.kicad_sch",
    }


def test_value_slots_fully_substituted(tmp_path: Path) -> None:
    _, p1, p2 = _instantiate_both(tmp_path)
    # No ${IS.*} slot survives in any child sheet.
    assert not _SLOT_RE.search(p1.read_text())
    assert not _SLOT_RE.search(p2.read_text())
    # gain=100, rg=1000 -> Rf=99k, Rg=1k.
    t1 = p1.read_text()
    assert '"99k"' in t1
    assert '"1k"' in t1
    # x4 gain=10, rg=1000 -> Rf=9k, Rg=1k on every channel.
    t2 = p2.read_text()
    assert '"9k"' in t2
    assert '"1k"' in t2


def test_provenance_stamped_in_parent(tmp_path: Path) -> None:
    root, _, _ = _instantiate_both(tmp_path)
    text = root.read_text()
    assert '"IS.Cell" "opamp-gain-noninverting@0.1.0"' in text
    assert '"IS.Cell" "opamp-gain-x4-noninverting@0.1.0"' in text
    assert '"IS.Param.gain" "100"' in text
    assert '"IS.Param.rg_ohms" "1000"' in text  # default surfaced
    for n in (1, 2, 3, 4):
        assert f'"IS.Param.gain{n}" "10"' in text


def test_fresh_uuids_no_collision_with_fragment(tmp_path: Path) -> None:
    _, p1, _ = _instantiate_both(tmp_path)
    frag_uuids = set(emit._UUID_RE.findall((CELL1 / "fragment.kicad_sch").read_text()))
    child_uuids = set(emit._UUID_RE.findall(p1.read_text()))
    # Not one uuid is carried over verbatim from the fragment.
    assert frag_uuids.isdisjoint(child_uuids)


def test_sheet_instances_pages_are_sequential(tmp_path: Path) -> None:
    root, _, _ = _instantiate_both(tmp_path)
    text = root.read_text()
    si = text[text.index("(sheet_instances") :]
    pages = re.findall(r'\(page "(\d+)"\)', si)
    assert pages == ["1", "2", "3"]  # root + two children


def test_byte_preserving_structure(tmp_path: Path) -> None:
    # The child must be the fragment text with only slots, uuids, the instance
    # project/path, and MPN-level part stamping changed — lib_symbols untouched.
    from infersynth.bind.parts import bind_parts

    _, p1, _ = _instantiate_both(tmp_path)
    frag = (CELL1 / "fragment.kicad_sch").read_text()
    child = p1.read_text()
    # Stamping injects two hidden single-line properties (MPN + Manufacturer)
    # per bound ref; the Footprint value is set in place (no line change).
    nbound = len(bind_parts(load_cell(CELL1)).bindings)
    assert child.count("\n") == frag.count("\n") + nbound * 2
    # lib_symbols section is copied verbatim (byte-for-byte up to lib defs).
    frag_lib = frag[frag.index("(lib_symbols") : frag.index("\t(symbol\n\t\t(lib_id")]
    assert frag_lib in child


# --------------------------------------------------------------------------
# Golden end-to-end: kicad-cli must PARSE the emitted hierarchy.
# --------------------------------------------------------------------------

_KICAD_CLI = shutil.which("kicad-cli")


# --------------------------------------------------------------------------
# Grid layout (round-2 cosmetics): many-sheet designs wrap instead of running
# off the A4 page in one endless row.
# --------------------------------------------------------------------------


def test_grid_layout_wraps_and_stays_in_frame(tmp_path: Path) -> None:
    root = emit.new_design(tmp_path, "demo")
    cell = load_cell(CELL1)
    for i in range(12):
        emit.instantiate(cell, {"gain": 100}, f"inst_{i}", tmp_path, root)

    boxes = emit._existing_sheet_boxes(root.read_text())
    assert len(boxes) == 12

    # wraps: not every sheet lands on the same row (the old one-row layout).
    ys = {y for _, y, _, _ in boxes}
    assert len(ys) > 1

    # in-frame: no sheet's right edge crosses the A4 usable-width budget.
    page_right = emit._SHEET_ORIGIN_X + emit._PAGE_USABLE_W
    for x, _, w, _ in boxes:
        assert x + w <= page_right + 1e-6

    # no two boxes overlap.
    def _overlap(a, b) -> bool:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not _overlap(a, b), f"{a} overlaps {b}"


def test_sheet_size_grows_with_instname_and_port_count(tmp_path: Path) -> None:
    root = emit.new_design(tmp_path, "demo")
    cell1 = load_cell(CELL1)
    cell2 = load_cell(CELL2)
    emit.instantiate(cell1, {"gain": 100}, "a_very_long_instance_name_indeed", tmp_path, root)
    emit.instantiate(
        cell2,
        {"gain1": 10, "gain2": 10, "gain3": 10, "gain4": 10, "channels": 4},
        "q",
        tmp_path,
        root,
    )
    boxes = emit._existing_sheet_boxes(root.read_text())
    long_name_box, many_ports_box = boxes
    # the long-instname sheet is wider than the floor size.
    assert long_name_box[2] > emit._SHEET_W
    # the many-port cell's sheet is taller than the floor size.
    assert many_ports_box[3] > emit._SHEET_H
    # positions and sizes stay on the 1.27 mm grid.
    for x, y, w, h in boxes:
        for v in (x, y, w, h):
            assert abs(v / emit._GRID - round(v / emit._GRID)) < 1e-6


# --------------------------------------------------------------------------
# Refdes re-annotation (round-2 cosmetics): each instance's copied fragment
# gets a per-instance refdes namespace so a multi-sheet design never stacks
# the same U1/R1/C1 across every child.
# --------------------------------------------------------------------------


def test_renumber_refs_offsets_prefixed_digits() -> None:
    assert emit._bump_ref("U1", 100) == "U101"
    assert emit._bump_ref("R2", 200) == "R202"
    assert emit._bump_ref("C10", 300) == "C310"


def test_renumber_refs_excludes_virtual_refs() -> None:
    assert emit._bump_ref("#FLG01", 100) == "#FLG01"
    assert emit._bump_ref("#PWR01", 500) == "#PWR01"


def test_instantiate_renumbers_refs_per_instance(tmp_path: Path) -> None:
    root, p1, p2 = _instantiate_both(tmp_path)
    t1, t2 = p1.read_text(), p2.read_text()
    # instance 1 (offset +100): U1 -> U101, R1 -> R101, R2 -> R102.
    assert '"U101"' in t1
    assert '"R101"' in t1
    assert '"R102"' in t1
    ref_props_t1 = re.findall(r'\(property "Reference" "([^"]+)"', t1)
    assert "U1" not in ref_props_t1
    assert "R1" not in ref_props_t1
    assert "R2" not in ref_props_t1
    # instance 2 (offset +200): its own U1 -> U201.
    assert '"U201"' in t2
    # (reference "...") inside (instances ...) is rewritten identically.
    assert '(reference "U101")' in t1
    assert '(reference "U201")' in t2


def test_renumber_refs_leaves_lib_symbols_untouched(tmp_path: Path) -> None:
    _, p1, _ = _instantiate_both(tmp_path)
    child = p1.read_text()
    frag = (CELL1 / "fragment.kicad_sch").read_text()
    frag_lib = frag[frag.index("(lib_symbols") : frag.index("\t(symbol\n\t\t(lib_id")]
    # the lib_symbols block (bare "R"/"U" default-reference properties) is
    # copied verbatim — renumbering never touches it.
    assert frag_lib in child


def test_instantiate_renumber_refs_false_preserves_bare_refs(tmp_path: Path) -> None:
    # The WP4 cell-CI harness path: refs must stay exactly as the fragment
    # wrote them (golden_netlist.txt is keyed on those bare refs).
    root = emit.new_design(tmp_path, "demo")
    cell = load_cell(CELL1)
    p1 = emit.instantiate(cell, {"gain": 100}, "dut", tmp_path, root, renumber_refs=False)
    t1 = p1.read_text()
    assert '"U1"' in t1
    assert '"R1"' in t1
    assert '"R2"' in t1
    assert '(reference "U1")' in t1


# --------------------------------------------------------------------------
# MPN-level part stamping (SEED_PLAN crit 4): each bound instance symbol gets
# its Footprint set and hidden MPN/Manufacturer added, post-renumber.
# --------------------------------------------------------------------------


def test_stamping_sets_footprint_and_mpn_post_renumber(tmp_path: Path) -> None:
    _, p1, _ = _instantiate_both(tmp_path)
    child = p1.read_text()
    # opamp cell (instance 1, +100): U101 is OPA340NA in SOT-23-5; R101/R102 Yageo 0603.
    assert '"Footprint" "Package_TO_SOT_SMD:SOT-23-5"' in child
    assert '"Footprint" "Resistor_SMD:R_0603_1608Metric"' in child
    assert '"MPN" "OPA340NA/250"' in child
    assert '"Manufacturer" "Texas Instruments"' in child
    assert '"MPN" "RC0603FR-0710KL"' in child
    # every bound ref carries an MPN (3 refs -> 3 MPN props).
    assert child.count('(property "MPN"') == 3
    assert child.count('(property "Manufacturer"') == 3


def test_stamping_leaves_lib_symbols_untouched(tmp_path: Path) -> None:
    _, p1, _ = _instantiate_both(tmp_path)
    child = p1.read_text()
    frag = (CELL1 / "fragment.kicad_sch").read_text()
    frag_lib = frag[frag.index("(lib_symbols") : frag.index("\t(symbol\n\t\t(lib_id")]
    # the lib_symbols block is copied verbatim — stamping only touches instances.
    assert frag_lib in child
    # no MPN/Manufacturer property leaked into the (lib_symbols ...) head.
    head = child[: child.index("\t(symbol\n\t\t(lib_id")]
    assert '"MPN"' not in head
    assert '"Manufacturer"' not in head


def test_stamping_maps_bare_ref_when_renumber_false(tmp_path: Path) -> None:
    # WP4 cell-CI path (offset 0): bare refs still stamp correctly.
    root = emit.new_design(tmp_path, "demo")
    cell = load_cell(CELL1)
    p1 = emit.instantiate(cell, {"gain": 100}, "dut", tmp_path, root, renumber_refs=False)
    child = p1.read_text()
    assert '"MPN" "OPA340NA/250"' in child
    assert '"Footprint" "Resistor_SMD:R_0603_1608Metric"' in child


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_netlist_export_has_no_annotation_warning(tmp_path: Path) -> None:
    # Round-2 acceptance: with per-instance refdes renumbering, a multi-sheet
    # design no longer stacks the same U1/R1 on every child, so kicad-cli's
    # netlist export must not warn "schematic has annotation errors".
    root = emit.new_design(tmp_path, "demo")
    cell = load_cell(CELL1)
    for i in range(3):
        emit.instantiate(cell, {"gain": 100}, f"inst_{i}", tmp_path, root)
    proc = subprocess.run(
        [
            _KICAD_CLI,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "-o",
            str(tmp_path / "netlist.xml"),
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "annotation" not in combined.lower(), combined


@pytest.mark.kicad
@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
def test_kicad_cli_parses_emitted_hierarchy(tmp_path: Path) -> None:
    root, _, _ = _instantiate_both(tmp_path)
    proc = subprocess.run(
        [_KICAD_CLI, "sch", "erc", str(root)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    combined = proc.stdout + proc.stderr
    # A parse failure is the only real failure. ERC violations (any exit code)
    # are acceptable — the schematic loaded, which is what we assert.
    assert "Failed to load" not in combined, (
        f"kicad-cli could not parse the emitted schematic:\n{combined}"
    )
    # Positive evidence the whole hierarchy elaborated and ERC actually ran.
    assert "violation" in combined.lower(), combined
