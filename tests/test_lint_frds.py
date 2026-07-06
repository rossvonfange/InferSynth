"""End-to-end lint snapshots: all six example FRDs against the 2-cell catalog.

Snapshots stay small (BUILD_PLAN WP2 item 8): diagnostic-code counts per file
plus the first few codes of one representative file.
"""

from collections import Counter
from pathlib import Path

import pytest

from infersynth.cli import main
from infersynth.lint import Severity, classify, lint_path, parse_frd_markdown

REPO = Path(__file__).parent.parent
FRDS = REPO / "examples" / "frds"
CATALOG = REPO / "catalog"

# code counts per example FRD, linted against the committed 2-cell catalog
EXPECTED_CODE_COUNTS = {
    "01_hobbyist_garden_monitor.md": {
        "frd.ears": 11,
        "frd.no-primitive": 11,
        "frd.rationale-ignored": 1,
    },
    "02_manager_fleet_tracker.md": {
        "frd.ears": 12,
        "frd.no-primitive": 12,
        "frd.unclassifiable": 2,
    },
    "03_engineer_motor_controller.md": {
        "frd.ears": 17,
        "frd.no-primitive": 17,
        "frd.unclassifiable": 1,
    },
    "04_pcb_designer_daughtercard.md": {
        "frd.ears": 10,
        "frd.no-primitive": 10,
        "frd.rationale-ignored": 1,
    },
    "05_scientist_photometer.md": {
        "frd.ears": 7,
        "frd.no-primitive": 7,
        "frd.unclassifiable": 1,
    },
    "06_hierarchical_esp32_aircell.md": {
        "frd.ears": 29,
        "frd.no-primitive": 29,
        "frd.rationale-ignored": 3,
        "frd.unclassifiable": 5,
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED_CODE_COUNTS), ids=lambda n: n[:12])
def test_frd_diagnostic_code_counts(name):
    _, diags = lint_path(FRDS / name, catalog_dir=CATALOG)
    assert dict(Counter(d.code for d in diags)) == EXPECTED_CODE_COUNTS[name]
    # nothing in the corpus is an intake-rejecting ERROR with this catalog
    assert not any(d.severity == Severity.ERROR for d in diags)


def test_frd1_hobbyist_is_mostly_no_primitive():
    _, diags = lint_path(FRDS / "01_hobbyist_garden_monitor.md", catalog_dir=CATALOG)
    counts = Counter(d.code for d in diags)
    assert counts["frd.no-primitive"] >= 10  # the catalog-gap signal, en masse
    first_codes = [d.code for d in diags[:6]]
    assert first_codes == [
        "frd.rationale-ignored",
        "frd.ears", "frd.no-primitive",
        "frd.ears", "frd.no-primitive",
        "frd.ears",
    ]


def test_frd3_pwr03_matches_nothing_but_classifies_cleanly():
    rs = parse_frd_markdown(FRDS / "03_engineer_motor_controller.md")
    assert classify(rs["PWR-03"].text) == "ubiquitous"
    _, diags = lint_path(FRDS / "03_engineer_motor_controller.md", catalog_dir=CATALOG)
    pwr03 = [d for d in diags if "PWR-03" in d.message]
    assert sorted(d.code for d in pwr03) == ["frd.ears", "frd.no-primitive"]


# --- CLI ---------------------------------------------------------------------


def test_cli_lint_frd_ok(capsys):
    rc = main(["lint", str(FRDS / "01_hobbyist_garden_monitor.md"), "--catalog", str(CATALOG)])
    assert rc == 0  # warnings/info only -> success
    out = capsys.readouterr().out
    assert "warning frd.no-primitive" in out
    assert "info frd.ears" in out
    # file:line:col prefix, one-based
    first = out.splitlines()[0]
    assert first.startswith(str(FRDS / "01_hobbyist_garden_monitor.md") + ":")


def test_cli_lint_exit_1_on_error_severity(tmp_path, capsys):
    frd = tmp_path / "bad.md"
    frd.write_text("- a non-inverting amplifier with gain of 2000 shall buffer it\n")
    rc = main(["lint", str(frd), "--catalog", str(CATALOG)])
    assert rc == 1
    assert "error frd.param-out-of-range" in capsys.readouterr().out


def test_cli_lint_grammar_only_without_catalog(tmp_path, capsys):
    frd = tmp_path / "g.md"
    frd.write_text("- When armed, the unit shall alert.\n")
    rc = main(["lint", str(frd)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "frd.ears" in out and "event-driven" in out
    assert "frd.no-primitive" not in out


def test_cli_lint_missing_file(capsys):
    rc = main(["lint", "does/not/exist.md"])
    assert rc == 1
    assert "infersynth lint:" in capsys.readouterr().err
