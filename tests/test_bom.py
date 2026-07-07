"""BOM emission from a synthesized design's stamped child sheets
(SEED_PLAN acceptance criterion 4)."""

from __future__ import annotations

from pathlib import Path

from infersynth.bind.bom import bom_to_csv, build_bom
from infersynth.catalog.loader import load_cell
from infersynth.cli import main
from infersynth.compile_kicad import emit

CORE = Path(__file__).resolve().parent.parent / "catalog" / "core"


def _bridge_design(tmp_path: Path) -> Path:
    """A tiny two-cell design: an opamp gain stage + a decoupling cap, both
    stamped by the emitter."""
    root = emit.new_design(tmp_path, "d")
    amp = load_cell(CORE / "opamp-gain-noninverting")
    emit.instantiate(amp, {"gain": 4}, "amp", tmp_path, root)
    emit.instantiate(load_cell(CORE / "decoupling"), {}, "dec", tmp_path, root)
    return tmp_path


def test_build_bom_groups_and_counts(tmp_path: Path) -> None:
    bom = build_bom(_bridge_design(tmp_path))
    # opamp cell: U1 (OPA340NA) + R1/R2 (Yageo, values 3k & 1k) = 3; decoupling: C1 = 1.
    assert bom.total_parts == 4
    assert bom.bound_parts == 4
    assert not bom.unbound

    by_mpn = {line.mpn: line for line in bom.lines}
    assert "OPA340NA/250" in by_mpn
    # R1=3k and R2=1k share one MPN but differ by value -> two grouped lines.
    yageo = [line for line in bom.lines if line.manufacturer == "Yageo"]
    assert {line.value for line in yageo} == {"3k", "1k"}
    for line in yageo:
        assert line.qty == 1
        assert line.footprint == "Resistor_SMD:R_0603_1608Metric"


def test_bom_csv_has_header_summary_and_no_unbound_section(tmp_path: Path) -> None:
    csv_text = bom_to_csv(build_bom(_bridge_design(tmp_path)))
    lines = csv_text.splitlines()
    assert lines[0].startswith("# BOM summary:")
    assert lines[1] == "Refs,Qty,Value,MPN,Manufacturer,Footprint"
    assert "OPA340NA/250" in csv_text
    assert "UNBOUND" not in csv_text  # fully bound design -> no loud tail


def test_bom_unbound_section_is_loud(tmp_path: Path) -> None:
    # A hand-built child with an MPN-less symbol lands in the UNBOUND tail.
    emit.new_design(tmp_path, "d")
    child = tmp_path / "orphan.kicad_sch"
    child.write_text(
        '(kicad_sch\n\t(symbol\n\t\t(lib_id "Device:R")\n'
        '\t\t(property "Reference" "R1"\n\t\t)\n'
        '\t\t(property "Value" "10k"\n\t\t)\n'
        '\t\t(property "Footprint" ""\n\t\t)\n'
        "\t)\n)\n"
    )
    bom = build_bom(tmp_path)
    assert bom.unbound == ("R1",)
    csv_text = bom_to_csv(bom)
    assert "# UNBOUND" in csv_text
    assert "UNBOUND,R1" in csv_text


def test_cli_bom_command(tmp_path: Path, capsys) -> None:
    design = _bridge_design(tmp_path)
    out = tmp_path / "bom.csv"
    rc = main(["bom", "--design", str(design), "--out", str(out)])
    assert rc == 0
    text = out.read_text()
    assert "OPA340NA/250" in text
    assert text.startswith("# BOM summary:")
