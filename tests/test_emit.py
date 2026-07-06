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

CATALOG = Path(__file__).resolve().parents[1] / "catalog"
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
    # The child must be the fragment text with only slots, uuids, and the
    # instance project/path changed — line count and lib_symbols untouched.
    _, p1, _ = _instantiate_both(tmp_path)
    frag = (CELL1 / "fragment.kicad_sch").read_text()
    child = p1.read_text()
    assert child.count("\n") == frag.count("\n")
    # lib_symbols section is copied verbatim (byte-for-byte up to lib defs).
    frag_lib = frag[frag.index("(lib_symbols") : frag.index("\t(symbol\n\t\t(lib_id")]
    assert frag_lib in child


# --------------------------------------------------------------------------
# Golden end-to-end: kicad-cli must PARSE the emitted hierarchy.
# --------------------------------------------------------------------------

_KICAD_CLI = shutil.which("kicad-cli")


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
