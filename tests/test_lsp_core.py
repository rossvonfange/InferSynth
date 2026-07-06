"""Pure LSP feature-logic tests (BUILD_PLAN WP6) — no pygls required.

``infersynth.lsp.core`` is plain Python in/out (see its docstring), so these
tests exercise it directly without any LSP transport machinery.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infersynth.catalog import Catalog
from infersynth.lint.vocab import Vocabulary
from infersynth.lsp import core

REPO_CATALOG = Path(__file__).parent.parent / "catalog"


def _vocab() -> Vocabulary:
    return Vocabulary.from_catalog(Catalog.load(REPO_CATALOG))


def write_cell(root: Path, name: str, keywords, params=None, disambiguation=None) -> None:
    """Minimal valid cell package (mirrors tests/test_lint.py's helper)."""
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


# --- diagnostics --------------------------------------------------------------


def test_diagnostics_for_text_runs_over_in_memory_buffer():
    text = (
        "- The board shall use a non-inverting amplifier with gain of 2000\n"
        "- The unit shall report position at least hourly\n"
    )
    _, diags = core.diagnostics_for_text(text, "file:///buf.md", catalog_dir=REPO_CATALOG)
    codes = {d.code for d in diags}
    assert "frd.param-out-of-range" in codes
    assert "frd.no-primitive" in codes
    assert "frd.ears" in codes


def test_diagnostics_for_text_grammar_only_without_catalog():
    _, diags = core.diagnostics_for_text("- widget shall widget\n", "file:///buf.md")
    assert diags and all(d.code != "frd.no-primitive" for d in diags)


# --- word at position -----------------------------------------------------------


def test_word_at_position_finds_hyphenated_and_underscored_words():
    text = "gain of 100 for rg_ohms and non-inverting\n"
    assert core.word_at_position(text, 0, 0) == "gain"
    idx = text.index("rg_ohms")
    assert core.word_at_position(text, 0, idx + 2) == "rg_ohms"
    idx2 = text.index("non-inverting")
    assert core.word_at_position(text, 0, idx2 + 2) == "non-inverting"


def test_word_at_position_out_of_range_is_empty():
    assert core.word_at_position("short\n", 5, 0) == ""


# --- completions ----------------------------------------------------------------


def test_completion_items_contain_keyword_and_param():
    items = core.completion_items(_vocab())
    labels = {i.label for i in items}
    assert "non-inverting amplifier" in labels
    assert "gain" in labels
    gain_item = next(i for i in items if i.label == "gain" and i.is_param)
    assert gain_item.detail.startswith("core/opamp-gain-noninverting@")
    assert "1.0..1000.0" in gain_item.documentation


# --- hover ------------------------------------------------------------------


def test_hover_for_param_includes_range():
    hover = core.hover_for_word("gain", _vocab())
    assert hover is not None
    assert "1.0..1000.0" in hover.contents
    assert "Depth:" in hover.contents


def test_hover_for_keyword_token_matches():
    hover = core.hover_for_word("amplifier", _vocab())
    assert hover is not None
    assert "opamp-gain" in hover.contents


def test_hover_for_unknown_word_is_none():
    assert core.hover_for_word("frobnicate", _vocab()) is None


# --- code actions -------------------------------------------------------------


def test_code_actions_for_ambiguous_diagnostic(tmp_path):
    write_cell(tmp_path, "can-transceiver", ["can transceiver"])
    write_cell(tmp_path, "isolated-can-transceiver", ["isolated can transceiver"])
    vocab = Vocabulary.from_catalog(Catalog.load(tmp_path))
    text = "- The board shall provide an isolated can transceiver\n"
    _, diags = core.diagnostics_for_text(text, "file:///buf.md")
    from infersynth.lint import lint_requirement_set, parse_frd_text

    reqset = parse_frd_text(text, file="file:///buf.md")
    diags = lint_requirement_set(reqset, vocab)
    actions = core.code_actions_for_diagnostics(diags, vocab)
    assert actions
    titles = {a.title for a in actions}
    assert "Specify can-transceiver" in titles
    assert "Specify isolated-can-transceiver" in titles
    for action in actions:
        assert action.edits


def test_code_action_for_no_primitive_inserts_stub():
    text = "- The unit shall report position at least hourly\n"
    _, diags = core.diagnostics_for_text(text, "file:///buf.md", catalog_dir=REPO_CATALOG)
    actions = core.code_actions_for_diagnostics(diags, _vocab())
    (action,) = [a for a in actions if a.title == "Insert catalog-gap stub"]
    (edit,) = action.edits
    assert "catalog-gap stub" in edit.new_text


def test_code_actions_empty_without_diagnostics():
    assert core.code_actions_for_diagnostics([], _vocab()) == []
