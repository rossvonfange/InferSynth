"""Lint engine unit tests: model, diagnostics, EARS, vocabulary resolution."""

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog
from infersynth.lint import (
    Severity,
    classify,
    lint_requirement_set,
    parse_frd_text,
)
from infersynth.lint.diagnostics import Diagnostic, Position, Range
from infersynth.lint.model import Requirement, RequirementModelError, RequirementSet
from infersynth.lint.vocab import (
    Vocabulary,
    extract_quantities,
    pick_winner,
    resolve,
)

REPO_CATALOG = Path(__file__).parent.parent / "catalog"


def write_cell(root: Path, name: str, keywords, params=None, disambiguation=None) -> None:
    """Minimal valid cell package (mirrors tests/test_catalog.py helper)."""
    cell_dir = root / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    idioms: dict = {"keywords": list(keywords)}
    if params is not None:
        idioms["params"] = params
    if disambiguation is not None:
        idioms["disambiguation"] = disambiguation
    (cell_dir / "cell.yaml").write_text(
        yaml.safe_dump(
            {
                "manifest": {
                    "name": name,
                    "version": "0.1.0",
                    "description": f"test cell {name}",
                    "provenance": "synthetic test fixture",
                    "license": "GPL-3.0-or-later",
                },
                "idioms": idioms,
            }
        )
    )


# --- model ------------------------------------------------------------------


def test_requirement_set_document_order_and_index():
    root = Requirement(id="SYS", text="root")
    a = Requirement(id="SYS.1", text="a")
    b = Requirement(id="SYS.2", text="b")
    a1 = Requirement(id="SYS.1.1", text="a1")
    root.add_child(a)
    root.add_child(b)
    a.add_child(a1)
    rs = RequirementSet([root])
    assert [r.id for r in rs] == ["SYS", "SYS.1", "SYS.1.1", "SYS.2"]
    assert rs["SYS.1.1"].parent is a
    assert len(rs) == 4


def test_requirement_set_rejects_duplicate_ids():
    root = Requirement(id="A", text="x")
    root.add_child(Requirement(id="A", text="y"))
    with pytest.raises(RequirementModelError):
        RequirementSet([root])


def test_lintable_requirements_exclude_groups_and_rationale():
    rs = parse_frd_text("# Heading\n\n- the widget shall widget\n- (a note)\n")
    kinds = {(r.is_group, r.rationale) for r in rs}
    assert (True, False) in kinds and (False, True) in kinds
    assert [r.text for r in rs.requirements()] == ["the widget shall widget"]


# --- diagnostics (LSP shape) --------------------------------------------------


def test_diagnostic_to_lsp_shape():
    d = Diagnostic(
        file="frd.md",
        range=Range(Position(3, 2), Position(3, 40)),
        severity=Severity.WARNING,
        code="frd.no-primitive",
        message="nope",
        quickfix="stub",
    )
    assert d.to_lsp() == {
        "range": {
            "start": {"line": 3, "character": 2},
            "end": {"line": 3, "character": 40},
        },
        "severity": 2,
        "code": "frd.no-primitive",
        "source": "infersynth-lint",
        "message": "nope",
        "data": {"quickfix": "stub"},
    }
    # CLI form is one-based
    assert d.format_cli() == "frd.md:4:3 warning frd.no-primitive nope"


# --- EARS ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("The system shall report position hourly.", "ubiquitous"),
        ("SHALL provide logic rails — 12 V ±5 % @ 1 A, 3V3 ±3 % @ 500 mA", "ubiquitous"),
        ("When the input exceeds 5 V, the system shall clamp.", "event-driven"),
        ("While charging, the LED must blink.", "state-driven"),
        ("If the sensor fails, then the system shall alarm.", "unwanted"),
        ("Where PoE is present, the node shall negotiate class 2.", "optional"),
        ("While armed, when motion is detected, the unit shall alert.", "complex"),
        ("cheap!! ideally under $20 in parts", "unclassifiable"),
        # a modal statement spanning several sentences is not one EARS sentence
        ("It must survive being outside. Trailers get pressure-washed.", "unclassifiable"),
    ],
)
def test_classify(text, pattern):
    assert classify(text) == pattern


def test_unclassifiable_with_modal_warns_but_prose_does_not():
    rs = parse_frd_text(
        "- It must survive outside. It gets washed.\n- cheap!! ideally under $20\n"
    )
    diags = lint_requirement_set(rs)
    warns = [d for d in diags if d.code == "frd.unclassifiable"]
    assert len(warns) == 1
    assert warns[0].severity == Severity.WARNING
    assert "R-1" in warns[0].message
    infos = [d for d in diags if d.code == "frd.ears"]
    assert len(infos) == 2  # every lintable requirement gets its pattern INFO


# --- vocabulary ---------------------------------------------------------------


def test_vocabulary_is_generated_from_catalog():
    vocab = Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))
    assert vocab.keywords() == [
        "4-wire sensor input",
        "active low-pass filter",
        "adc driver",
        "amplifier gain stage",
        "anti-alias filter",
        "anti-alias rc",
        "bypass capacitor",
        "constant current",
        "current source",
        "decoupling",
        "fixed voltage regulator",
        "input conditioning",
        "inverting amplifier",
        "linear regulator",
        "mfb low-pass",
        "non-inverting amplifier",
        "offset stage",
        "output clamp",
        "output connector",
        "output header",
        "overvoltage clamp",
        "power connector",
        "power input connector",
        "power input protection",
        "precision reference",
        "rc snubber",
        "reverse polarity protection",
        "sallen-key",
        "sensor connector",
        "summing amplifier",
        "unity buffer",
        "voltage follower",
        "voltage reference",
    ]
    keys = [e.cell_key for e in vocab.entries]
    assert keys == sorted(keys)
    x4 = next(e for e in vocab.entries if "x4" in e.cell_name)
    assert x4.disambiguation is not None
    assert "gain1" in x4.params


def test_extract_quantities_units():
    got = {
        (q.dimension, q.value)
        for q in extract_quantities("12 V, 500 mA, 5 MHz, 100k, 50 ppm, 60 °C, 5 %, 2 kHz")
    }
    assert got == {
        ("V", 12.0),
        ("A", 0.5),
        ("Hz", 5e6),
        ("ohm", 100000.0),
        ("ppm", 50.0),
        ("degC", 60.0),
        ("%", 5.0),
        ("Hz", 2000.0),
    }
    # 'kbps' must not read as a bare-k resistance
    assert extract_quantities("500 kbps") == []


def test_resolve_disambiguation_winner_and_param_binding():
    vocab = Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))
    rs = parse_frd_text("- The board shall use a non-inverting amplifier with gain of 100\n")
    (req,) = rs.requirements()
    matches = resolve(req, vocab)
    # Three claimants: opamp-gain-noninverting (exact "non-inverting
    # amplifier" keyword), opamp-gain-x4-noninverting (disambiguation rule,
    # never a primary winner), and opamp-gain-inverting (its "inverting
    # amplifier" keyword substring-matches inside "non-inverting amplifier";
    # it also declares a disambiguation rule so it never wins directly).
    assert len(matches) == 3
    winner = pick_winner(matches)
    # both the x4 cell and the inverting cell declare a disambiguation rule
    # -> neither is ever inferred directly
    assert winner is not None and winner.entry.cell_name == "opamp-gain-noninverting"
    gain = next(b for b in winner.params if b.name == "gain")
    assert gain.value == 100.0 and gain.problem is None


def test_param_out_of_range_is_error():
    vocab = Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))
    rs = parse_frd_text("- a non-inverting amplifier with gain of 2000 shall buffer it\n")
    diags = lint_requirement_set(rs, vocab)
    (err,) = [d for d in diags if d.code == "frd.param-out-of-range"]
    assert err.severity == Severity.ERROR
    assert "gain" in err.message and "above maximum" in err.message


def test_ambiguous_when_two_primary_cells_match(tmp_path):
    write_cell(tmp_path, "can-transceiver", ["can transceiver"])
    write_cell(tmp_path, "isolated-can-transceiver", ["isolated can transceiver"])
    vocab = Vocabulary.from_catalog(Catalog.load(tmp_path))
    rs = parse_frd_text("- The board shall provide an isolated can transceiver\n")
    diags = lint_requirement_set(rs, vocab)
    (amb,) = [d for d in diags if d.code == "frd.ambiguous"]
    assert amb.severity == Severity.ERROR
    assert "can-transceiver@0.1.0" in amb.message


def test_no_primitive_carries_catalog_gap_quickfix():
    vocab = Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))
    rs = parse_frd_text("- The unit shall report position at least hourly\n")
    diags = lint_requirement_set(rs, vocab)
    (gap,) = [d for d in diags if d.code == "frd.no-primitive"]
    assert gap.severity == Severity.WARNING
    assert gap.quickfix is not None and "catalog-gap stub" in gap.quickfix
    assert gap.to_lsp()["data"]["quickfix"] == gap.quickfix


def test_rationale_lines_are_never_linted():
    vocab = Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))
    rs = parse_frd_text("- (we tried photoacoustic, it drifted)\n")
    diags = lint_requirement_set(rs, vocab)
    assert [d.code for d in diags] == ["frd.rationale-ignored"]
    assert diags[0].severity == Severity.INFO
