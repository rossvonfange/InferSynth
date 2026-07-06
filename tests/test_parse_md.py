"""Per-FRD snapshot tests: extracted (id, text) pairs for all six examples."""

from pathlib import Path

import pytest

from infersynth.lint.parse_md import parse_frd_markdown

FRDS = Path(__file__).parent.parent / "examples" / "frds"
ALL_FRDS = sorted(FRDS.glob("*.md"))


def ids(rs):
    return [r.id for r in rs]


@pytest.mark.parametrize("path", ALL_FRDS, ids=lambda p: p.stem)
def test_all_frds_parse(path):
    rs = parse_frd_markdown(path)
    assert len(rs) > 0
    for r in rs:
        assert r.id and isinstance(r.text, str)
        assert r.source is not None and r.source.file == str(path)
        assert r.level == (0 if r.parent is None else r.parent.level + 1)


def test_frd1_hobbyist_prose_and_bullets():
    rs = parse_frd_markdown(FRDS / "01_hobbyist_garden_monitor.md")
    assert ids(rs) == [f"R-{n}" for n in range(1, 14)]
    assert rs["R-1"].is_group  # the title heading
    assert rs["R-2"].rationale  # "(posted to the forum, more or less)"
    assert rs["R-4"].text.startswith("read 3 of those cheap capacitive soil moisture")
    assert rs["R-10"].text == "cheap!! ideally under $20 in parts"
    assert all(r.parent is rs["R-1"] for r in rs if r is not rs["R-1"])


def test_frd2_manager_memo_numbered_items():
    rs = parse_frd_markdown(FRDS / "02_manager_fleet_tracker.md")
    assert ids(rs) == [f"R-{n}" for n in range(1, 13)]
    # numbered item 2 with its wrapped continuation joined
    assert rs["R-5"].text == (
        "The unit must report position at least hourly, more often when moving."
    )
    assert rs["R-6"].text.endswith("Non-negotiable.")
    assert not any(r.is_group or r.rationale for r in rs)


def test_frd3_engineer_shall_table():
    rs = parse_frd_markdown(FRDS / "03_engineer_motor_controller.md")
    explicit = [r.id for r in rs if r.explicit_id]
    assert explicit == [
        "PWR-01", "PWR-02", "PWR-03", "DRV-01", "DRV-02", "DRV-03",
        "SNS-01", "SNS-02", "COM-01", "PRT-01", "PRT-02", "ENV-01", "ENV-02",
    ]
    assert rs["PWR-03"].text == (
        "SHALL provide logic rails — 12 V ±5 % @ 1 A, 3V3 ±3 % @ 500 mA"
    )
    assert rs["ENV-02"].text == "Conformal-coat compatible layout"
    # table rows hang off the "2. Requirements" heading
    assert rs["PWR-01"].parent is not None and rs["PWR-01"].parent.is_group
    assert rs["PWR-01"].parent.text == "2. Requirements"


def test_frd4_pcb_designer_redlines():
    rs = parse_frd_markdown(FRDS / "04_pcb_designer_daughtercard.md")
    assert ids(rs) == [f"R-{n}" for n in range(1, 13)]
    assert rs["R-2"].rationale  # "# (author: layout, not systems ...)"
    assert rs["R-5"].text.startswith("The LoRa radio section from the app note")
    assert "verbatim" in rs["R-5"].text  # wrapped bullet joined
    assert rs["R-8"].text.startswith("TCXO not crystal")


def test_frd5_scientist_email_paragraphs():
    rs = parse_frd_markdown(FRDS / "05_scientist_photometer.md")
    assert ids(rs) == [f"R-{n}" for n in range(1, 8)]
    assert all("prose" in r.tags for r in rs)
    assert rs["R-4"].text.startswith("Each channel must be sampled")
    assert rs["R-7"].text.startswith("If a channel saturates")


def test_frd6_hierarchical_tree():
    rs = parse_frd_markdown(FRDS / "06_hierarchical_esp32_aircell.md")
    explicit = [r.id for r in rs if r.explicit_id]
    assert explicit == [
        "SYS",
        "SYS.1", "SYS.1.1", "SYS.1.1.1", "SYS.1.1.2", "SYS.1.1.3", "SYS.1.1.3.1",
        "SYS.1.1.4", "SYS.1.2", "SYS.1.2.1", "SYS.1.2.2", "SYS.1.2.3", "SYS.1.2.4",
        "SYS.1.3", "SYS.1.3.1", "SYS.1.3.2",
        "SYS.2", "SYS.2.1", "SYS.2.1.1", "SYS.2.1.2", "SYS.2.2",
        "SYS.3", "SYS.3.1", "SYS.3.2", "SYS.3.3",
        "SYS.4", "SYS.4.1", "SYS.4.2", "SYS.4.3",
    ]
    # dotted IDs attach to their prefix parent
    assert rs["SYS.1.1.3.1"].parent is rs["SYS.1.1.3"]
    assert rs["SYS.1.1.1"].parent is rs["SYS.1.1"]
    assert rs["SYS.4"].parent is rs["SYS"]
    # wrapped tree lines are joined; inline [D: ...] notes are split out
    assert rs["SYS.1.1.1"].text == (
        "CO2 SHALL be measured 400–5000 ppm, ±(50 ppm + 5 %), NDIR type."
    )
    rationale = [r for r in rs if r.rationale]
    assert len(rationale) == 3
    assert rationale[1].text == "photoacoustic drifted in 2023 pilot"
    assert rationale[1].parent is rs["SYS.1.1.1"]
    assert rationale[2].text == "bid ceiling from district RFP"
    assert rationale[2].parent is rs["SYS.4.3"]
    assert rs["SYS.4.3"].text == "Target BOM ≤ $38 @ 1k."
