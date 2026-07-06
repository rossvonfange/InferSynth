"""End-to-end tests for infersynth.synthesize (lint → match → decide → emit)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.cli import main
from infersynth.match import MatchKnobs, match
from infersynth.synthesize import synthesize

CORE = Path(__file__).resolve().parent.parent / "catalog"

DEMO_FRD = """# Demo sensor board

- The board shall include a non-inverting amplifier gain stage with gain of 4.
- The board shall include a voltage reference.
- The board shall include decoupling.
- The board shall include an adc driver.
- The board shall include a 4-wire sensor input connector.
"""

GAP_FRD = """# Gap board

- The board shall include a flux capacitor.
"""


@pytest.fixture()
def demo_frd(tmp_path: Path) -> Path:
    p = tmp_path / "demo_frd.md"
    p.write_text(DEMO_FRD, encoding="utf-8")
    return p


class TestSynthesizeEndToEnd:
    def test_full_pipeline(self, demo_frd: Path, tmp_path: Path) -> None:
        out = tmp_path / "build"
        result = synthesize(demo_frd, CORE, out, profile="prototype")

        assert result.all_decided
        assert not result.skipped
        cells = {i.cell_key for i in result.instantiated}
        # disambiguation-primacy regression: a "non-inverting amplifier"
        # requirement must never instantiate the inverting (rule-carrying)
        # or x4 cells via downstream tie-break.
        assert "core/opamp-gain-noninverting@0.1.0" in cells
        assert "core/opamp-gain-inverting@0.1.0" not in cells

        amp = next(i for i in result.instantiated if "noninverting" in i.cell_key)
        assert amp.params["gain"] == 4.0
        assert amp.param_sources["gain"] == "extracted"
        assert amp.param_sources["rg_ohms"] == "default"

        # files exist; ${IS.*} slots fully substituted; provenance stamped
        assert result.root.exists()
        child = Path(amp.sheet_path)
        text = child.read_text(encoding="utf-8")
        assert "${IS." not in text
        assert '"3k"' in text  # (gain-1)*rg_ohms = 3000 -> "3k"
        root_text = result.root.read_text(encoding="utf-8")
        assert root_text.count("IS.Cell") == len(result.instantiated)
        assert result.trace_path is not None and result.trace_path.exists()
        report = result.report_path.read_text(encoding="utf-8")
        # NETFLOW stage 1: rails are wired (single-cell winners -> no signal
        # nets), and the report carries the wired-nets table + residual worklist.
        assert "Wired nets" in report
        assert "Residual wiring worklist" in report
        assert result.wiring_plan is not None and result.wiring_plan.rails

    def test_deterministic(self, demo_frd: Path, tmp_path: Path) -> None:
        r1 = synthesize(demo_frd, CORE, tmp_path / "a", profile="prototype")
        r2 = synthesize(demo_frd, CORE, tmp_path / "b", profile="prototype")
        assert [i.cell_key for i in r1.instantiated] == [i.cell_key for i in r2.instantiated]
        assert [i.instname for i in r1.instantiated] == [i.instname for i in r2.instantiated]
        assert r1.report_path.read_text(encoding="utf-8") == r2.report_path.read_text(
            encoding="utf-8"
        )

    def test_undecided_reported(self, tmp_path: Path) -> None:
        frd = tmp_path / "gap.md"
        frd.write_text(GAP_FRD, encoding="utf-8")
        result = synthesize(frd, CORE, tmp_path / "build")
        assert not result.all_decided
        assert not result.instantiated
        assert "Undecided requirements" in result.report_path.read_text(encoding="utf-8")

    @pytest.mark.kicad
    def test_emitted_hierarchy_parses(self, demo_frd: Path, tmp_path: Path) -> None:
        if shutil.which("kicad-cli") is None:
            pytest.skip("kicad-cli not on PATH")
        out = tmp_path / "build"
        result = synthesize(demo_frd, CORE, out, profile="prototype")
        proc = subprocess.run(
            [
                "kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                "-o", str(out / "net.xml"), str(result.root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert (out / "net.xml").exists(), proc.stderr
        assert "Failed to load" not in proc.stderr


class TestDisambiguationPrimacy:
    def test_rule_carrying_cells_not_chained_when_rule_free_exists(self, demo_frd) -> None:
        from infersynth.catalog import Catalog
        from infersynth.lint import lint_path

        catalog = Catalog.load(CORE)
        reqset, _ = lint_path(demo_frd, catalog_dir=CORE)
        mres = match(reqset, catalog, knobs=MatchKnobs())
        amp_rid = next(r.id for r in reqset if "non-inverting" in r.text)
        chained = {c for ch in mres.chains[amp_rid] for c in ch.cells}
        assert chained == {"core/opamp-gain-noninverting@0.1.0"}
        # but the excluded candidates remain visible as considered evidence
        considered = {c.cell_key for c in mres.candidates[amp_rid]}
        assert "core/opamp-gain-inverting@0.1.0" in considered


class TestNetflowThreading:
    def test_pins_win_synthesis(self, tmp_path: Path) -> None:
        frd = tmp_path / "pinned.md"
        frd.write_text(
            "- The board shall include an amplifier gain stage with gain of 4.\n",
            encoding="utf-8",
        )
        # without the pin this unqualified amp resolves to non-inverting; the pin
        # forces the inverting cell to be the synthesized winner (primacy defeat).
        result = synthesize(
            frd, CORE, tmp_path / "build", profile="prototype",
            pins={"R-1": "core/opamp-gain-inverting"},
        )
        cells = {i.cell_key for i in result.instantiated}
        assert "core/opamp-gain-inverting@0.1.0" in cells
        assert "core/opamp-gain-noninverting@0.1.0" not in cells

    def test_feeds_rendered_in_report(self, demo_frd: Path, tmp_path: Path) -> None:
        from infersynth.spec import FeedEdge

        out = tmp_path / "build"
        result = synthesize(
            demo_frd, CORE, out, profile="prototype",
            feeds=(FeedEdge("R-2", "R-5", "IN2"),),
        )
        report = result.report_path.read_text(encoding="utf-8")
        assert "Declared feeds (not yet wired)" in report
        assert "`R-2` → `R-5.IN2`" in report
        assert result.feeds == (FeedEdge("R-2", "R-5", "IN2"),)


class TestSynthesizeCli:
    def test_cli_exit_codes(self, demo_frd: Path, tmp_path: Path) -> None:
        rc = main(
            [
                "synthesize", "--frd", str(demo_frd), "--catalog", str(CORE),
                "--out", str(tmp_path / "ok"), "--profile", "prototype",
            ]
        )
        assert rc == 0
        gap = tmp_path / "gap.md"
        gap.write_text(GAP_FRD, encoding="utf-8")
        rc = main(
            ["synthesize", "--frd", str(gap), "--catalog", str(CORE),
             "--out", str(tmp_path / "gapbuild")]
        )
        assert rc == 3
