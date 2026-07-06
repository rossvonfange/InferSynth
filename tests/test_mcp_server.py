"""infersynth-mcp tool-handler tests (BUILD_PLAN WP7).

These call :mod:`infersynth.mcp_server.tools` handlers directly (plain
Python functions, JSON-shaped in/out) rather than driving a real stdio MCP
transport — the transport (:mod:`infersynth.mcp_server.server`) is a thin
``mcp`` SDK wrapper around the same dispatch (see
``test_build_server_registers_every_tool`` below for a lightweight check of
that wiring); exercising it end-to-end would mean spinning up a subprocess
and a client session for no additional coverage of the logic under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.mcp_server import tools

CATALOG = Path(__file__).parent.parent / "catalog"
CELL1 = CATALOG / "core" / "opamp-gain-noninverting"
CELL2 = CATALOG / "core" / "opamp-gain-x4-noninverting"
FRD = Path(__file__).parent.parent / "examples" / "frds" / "01_hobbyist_garden_monitor.md"


class TestLintFrd:
    def test_path_returns_expected_code_counts(self):
        result = tools.lint_frd(path=str(FRD))
        assert "diagnostics" in result and "counts" in result
        assert result["counts"]
        # cross-check against the library call directly.
        from infersynth.lint import lint_path

        _, diags = lint_path(FRD)
        expected = {}
        for d in diags:
            expected[d.code] = expected.get(d.code, 0) + 1
        assert result["counts"] == expected
        assert sum(result["counts"].values()) == len(result["diagnostics"])

    def test_text_matches_path(self, tmp_path):
        text = FRD.read_text()
        by_text = tools.lint_frd(text=text)
        by_path = tools.lint_frd(path=str(FRD))
        assert by_text["counts"] == by_path["counts"]

    def test_catalog_dir_adds_vocab_lint(self):
        grammar_only = tools.lint_frd(path=str(FRD))
        with_catalog = tools.lint_frd(path=str(FRD), catalog_dir=str(CATALOG))
        # catalog-aware lint can only add findings (frd.no-primitive etc.).
        assert sum(with_catalog["counts"].values()) >= sum(grammar_only["counts"].values())

    def test_requires_exactly_one_of_path_or_text(self):
        with pytest.raises(ValueError):
            tools.lint_frd()
        with pytest.raises(ValueError):
            tools.lint_frd(path=str(FRD), text="whatever")

    def test_diagnostics_are_lsp_shaped(self):
        result = tools.lint_frd(path=str(FRD))
        d = result["diagnostics"][0]
        assert set(d) >= {"range", "severity", "code", "source", "message"}


class TestCatalogSearch:
    def test_amplifier_finds_all_four_cells(self):
        # opamp-gain-inverting joined the catalog with keywords "inverting
        # amplifier" / "amplifier gain stage" — both contain "amplifier", so
        # it now surfaces alongside the non-inverting single/quad cells.
        # summing-offset-stage's "summing amplifier" keyword also contains
        # "amplifier", so it surfaces too.
        result = tools.catalog_search("amplifier", catalog_dir=str(CATALOG))
        keys = {r["cell"] for r in result["results"]}
        assert keys == {
            "core/opamp-gain-noninverting@0.1.0",
            "core/opamp-gain-x4-noninverting@0.1.0",
            "core/opamp-gain-inverting@0.1.0",
            "core/summing-offset-stage@0.1.0",
        }
        assert all(r["library"] == "core" for r in result["results"])

    def test_no_match_returns_empty(self):
        result = tools.catalog_search("nonexistent-widget-xyz", catalog_dir=str(CATALOG))
        assert result["results"] == []

    def test_empty_query_returns_everything(self):
        result = tools.catalog_search("", catalog_dir=str(CATALOG))
        assert len(result["results"]) == 14


class TestCatalogValidate:
    def test_ok(self):
        result = tools.catalog_validate(str(CATALOG))
        assert result["ok"] is True
        assert "core/opamp-gain-noninverting@0.1.0" in result["cells"]

    def test_fail_reports_diagnostics(self, tmp_path):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "cell.yaml").write_text("manifest: {}\n")
        result = tools.catalog_validate(str(tmp_path))
        assert result["ok"] is False
        assert result["diagnostics"]


class TestBindCell:
    def test_gain_100_yields_r1_99000(self):
        result = tools.bind_cell(str(CELL1), {"gain": 100})
        assert result["cell"] == "opamp-gain-noninverting@0.1.0"
        assert result["bindings"]["R1"] == pytest.approx(99000.0)
        assert result["bindings"]["R2"] == pytest.approx(1000.0)  # rg_ohms default

    def test_out_of_range_raises(self):
        from infersynth.bind import BindingError

        with pytest.raises(BindingError):
            tools.bind_cell(str(CELL1), {"gain": 1_000_000})


class TestInstantiateCell:
    def test_writes_files_into_new_design(self, tmp_path):
        design_dir = tmp_path / "my_design"
        result = tools.instantiate_cell(
            cell_dir=str(CELL1),
            params={"gain": 10},
            instname="gain_a",
            design_dir=str(design_dir),
        )
        assert Path(result["parent_sch"]).is_file()
        assert Path(result["child_sch"]).is_file()
        assert Path(result["parent_sch"]).name == "my_design.kicad_sch"
        assert Path(result["child_sch"]).name == "gain_a.kicad_sch"

    def test_appends_to_existing_parent(self, tmp_path):
        design_dir = tmp_path / "design"
        first = tools.instantiate_cell(
            cell_dir=str(CELL1),
            params={"gain": 10},
            instname="stage1",
            design_dir=str(design_dir),
        )
        second = tools.instantiate_cell(
            cell_dir=str(CELL2),
            params={"gain1": 10, "gain2": 10, "gain3": 10, "gain4": 10, "channels": 4},
            instname="stage2",
            design_dir=str(design_dir),
            parent_sch=first["parent_sch"],
        )
        assert second["parent_sch"] == first["parent_sch"]
        parent_text = Path(second["parent_sch"]).read_text()
        assert "stage1" in parent_text
        assert "stage2" in parent_text


class TestRunGates:
    def test_golden_netlist_pass(self):
        result = tools.run_gates(
            cell_dir=str(CELL1),
            ir_partition={
                "/FB": [["R1", "2"], ["R2", "1"], ["U1", "4"]],
                "/GND": [["R2", "2"]],
                "/IN": [["U1", "3"]],
                "/OUT": [["R1", "1"], ["U1", "1"]],
                "/VCC": [["U1", "5"]],
                "/VEE": [["U1", "2"]],
            },
        )
        by_gate = {r["gate"]: r for r in result["results"]}
        assert by_gate["netlist-partition-equivalence"]["status"] == "pass"
        assert by_gate["erc"]["status"] == "skipped"
        assert by_gate["simulation"]["status"] == "skipped"
        assert "GATE SKIPPED" in result["summary"]

    def test_no_context_all_skipped(self):
        result = tools.run_gates()
        assert result["ok"] is True
        assert all(r["status"] == "skipped" for r in result["results"])

    def test_mismatched_partition_fails(self):
        result = tools.run_gates(
            cell_dir=str(CELL1),
            ir_partition={"/FB": [["R1", "2"]]},
        )
        by_gate = {r["gate"]: r for r in result["results"]}
        assert by_gate["netlist-partition-equivalence"]["status"] == "fail"
        assert result["ok"] is False


class TestNotImplemented:
    @pytest.mark.parametrize(
        "name",
        ["elaborate_spec", "match_catalog", "catalog_submit_check"],
    )
    def test_raises_not_implemented_stage_error(self, name):
        handler = getattr(tools, name)
        with pytest.raises(tools.NotImplementedStageError) as excinfo:
            handler()
        assert name in str(excinfo.value)
        assert excinfo.value.tool == name
        assert excinfo.value.stage


def test_build_server_registers_every_tool():
    pytest.importorskip("mcp")
    from infersynth.mcp_server.server import _TOOL_SCHEMAS, build_server

    build_server(catalog_dir=str(CATALOG))  # constructs without error
    assert set(_TOOL_SCHEMAS) == set(tools.TOOL_NAMES)
