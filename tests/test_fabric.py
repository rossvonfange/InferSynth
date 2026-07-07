"""Fabric stuffing engine tests (docs/FABRIC.md, deliverables 1-5).

Covers: loader validation, fit determinism + utilization diagnostics +
params_fixed mismatch, DNP text surgery (attrs set, rest byte-identical),
value stuffing, and the end-to-end (FRD -> lint -> match -> decide -> fit ->
stuff, with a kicad-cli parse proof).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.decide import decide
from infersynth.fabric import (
    FabricError,
    extracted_params_for_winners,
    fit,
    load_fabric,
    stuff,
)
from infersynth.fabric.loader import Site, derive_ref_map
from infersynth.fabric.stuff import apply_pcb_surgery
from infersynth.lint import lint_path
from infersynth.match import match

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"
FABRIC_DIR = REPO / "examples" / "fabric-demo"
FABRIC_YAML = FABRIC_DIR / "fabric.yaml"

_KICAD_CLI = shutil.which("kicad-cli")

GAIN4_FRD = (
    "# Demo\n"
    "- The board shall include a non-inverting amplifier gain stage with gain of 4.\n"
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG)


@pytest.fixture(scope="module")
def fabric(catalog: Catalog):
    return load_fabric(FABRIC_YAML, catalog)


def _decide_winners(frd_text: str, tmp_path: Path, catalog: Catalog):
    frd = tmp_path / "frd.md"
    frd.write_text(frd_text)
    reqset, _ = lint_path(frd, catalog_dir=CATALOG)
    mres = match(reqset, catalog)
    decision = decide(mres, catalog, "prototype")
    winners = {
        rid: fin.chain.cells[0]
        for rid, fin in decision.winners().items()
        if len(fin.chain.cells) == 1
    }
    resolved = extracted_params_for_winners(mres, decision, catalog)
    return winners, resolved


# --------------------------------------------------------------------------
# deliverable 1: loader validation
# --------------------------------------------------------------------------
class TestLoader:
    def test_loads_demo_fabric(self, fabric) -> None:
        assert fabric.name == "demo-fabric"
        assert [s.id for s in fabric.sites] == ["amp0", "amp1", "dec0"]
        assert fabric.board_path.is_file()
        # version-less cell refs resolve to canonical keys
        assert fabric.site_by_id["amp0"].cells == ("core/opamp-gain-noninverting@0.1.0",)
        assert fabric.tie_off["amp0"].policy == "dnp-all"

    def test_refs_unique_across_sites(self, catalog, tmp_path) -> None:
        board = tmp_path / "b.kicad_pcb"
        board.write_text("(kicad_pcb)\n")
        (tmp_path / "fabric.yaml").write_text(
            "fabric:\n"
            "  name: dup\n"
            "  board: b.kicad_pcb\n"
            "  sites:\n"
            "    - {id: a, cell: core/decoupling, refs: [C1]}\n"
            "    - {id: b, cell: core/decoupling, refs: [C1]}\n"
        )
        with pytest.raises(FabricError) as exc:
            load_fabric(tmp_path / "fabric.yaml", catalog)
        assert any("unique across sites" in d for d in exc.value.diagnostics)

    def test_unknown_cell_rejected(self, catalog, tmp_path) -> None:
        board = tmp_path / "b.kicad_pcb"
        board.write_text("(kicad_pcb)\n")
        (tmp_path / "fabric.yaml").write_text(
            "fabric:\n"
            "  name: bad\n"
            "  board: b.kicad_pcb\n"
            "  sites:\n"
            "    - {id: a, cell: core/does-not-exist, refs: [U1]}\n"
        )
        with pytest.raises(FabricError) as exc:
            load_fabric(tmp_path / "fabric.yaml", catalog)
        assert any("does-not-exist" in d for d in exc.value.diagnostics)

    def test_tie_off_policy_enum(self, catalog, tmp_path) -> None:
        board = tmp_path / "b.kicad_pcb"
        board.write_text("(kicad_pcb)\n")
        (tmp_path / "fabric.yaml").write_text(
            "fabric:\n"
            "  name: bad\n"
            "  board: b.kicad_pcb\n"
            "  sites:\n"
            "    - {id: a, cell: core/decoupling, refs: [C1]}\n"
            "  tie_off:\n"
            "    a: {policy: burn-it}\n"
        )
        with pytest.raises(FabricError) as exc:
            load_fabric(tmp_path / "fabric.yaml", catalog)
        assert any("dnp-all" in d for d in exc.value.diagnostics)

    def test_missing_board_file(self, catalog, tmp_path) -> None:
        (tmp_path / "fabric.yaml").write_text(
            "fabric:\n"
            "  name: nb\n"
            "  board: nope.kicad_pcb\n"
            "  sites:\n"
            "    - {id: a, cell: core/decoupling, refs: [C1]}\n"
        )
        with pytest.raises(FabricError) as exc:
            load_fabric(tmp_path / "fabric.yaml", catalog)
        assert any("does not exist" in d for d in exc.value.diagnostics)

    def test_derive_ref_map(self) -> None:
        assert derive_ref_map(["R1", "R2", "U1"], ["U101", "R101", "R102"]) == {
            "R1": "R101",
            "R2": "R102",
            "U1": "U101",
        }
        with pytest.raises(ValueError):
            derive_ref_map(["R1", "R2"], ["R101"])


# --------------------------------------------------------------------------
# deliverable 2: fit engine
# --------------------------------------------------------------------------
class TestFit:
    def test_assign_and_utilization(self, catalog, fabric, tmp_path) -> None:
        winners, resolved = _decide_winners(GAIN4_FRD, tmp_path, catalog)
        fr = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        assert fr.assignments == {"R-2": "amp0"}
        assert set(fr.dnp_sites) == {"amp1", "dec0"}
        assert set(fr.dnp_refs) == {"U201", "R201", "R202", "C101"}
        assert fr.utilization == pytest.approx(1 / 3)
        assert fr.fits

    def test_determinism(self, catalog, fabric, tmp_path) -> None:
        winners, resolved = _decide_winners(GAIN4_FRD, tmp_path, catalog)
        a = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        b = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        assert a.assignments == b.assignments
        assert a.dnp_refs == b.dnp_refs
        assert a.value_stuffing == b.value_stuffing

    def test_value_stuffing_from_gain(self, catalog, fabric, tmp_path) -> None:
        winners, resolved = _decide_winners(GAIN4_FRD, tmp_path, catalog)
        fr = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        # gain=4, rg_ohms baked at 1000 -> R1 = (4-1)*1000 = 3000 on board ref R101
        vs = {sv.board_ref: sv.value for sv in fr.value_stuffing}
        assert vs == {"R101": 3000.0}

    def test_unfittable_diagnostic(self, catalog, fabric) -> None:
        # three amp winners but only two amp sites -> the third is unfittable
        key = "core/opamp-gain-noninverting@0.1.0"
        winners = {"R-1": key, "R-2": key, "R-3": key}
        fr = fit(winners, fabric, catalog=catalog, resolved_params={})
        assert len(fr.stuffed_sites) == 2
        assert fr.unfittable == (("R-3", key),)
        assert not fr.fits
        assert any("needs 1 more site" in d for d in fr.diagnostics)

    def test_params_fixed_mismatch(self, catalog, fabric) -> None:
        key = "core/opamp-gain-noninverting@0.1.0"
        # the FRD pins rg_ohms=2200 but the routed topology bakes 1000
        fr = fit(
            {"R-2": key},
            fabric,
            catalog=catalog,
            resolved_params={"R-2": {"gain": 4.0, "rg_ohms": 2200.0}},
        )
        assert any(
            "params_fixed mismatch" in d and "2200" in d and "1000" in d
            for d in fr.diagnostics
        )

    def test_assignment_only_without_catalog(self, fabric) -> None:
        key = "core/decoupling@0.1.0"
        fr = fit({"R-9": key}, fabric)  # no catalog/resolved_params
        assert fr.assignments == {"R-9": "dec0"}
        assert fr.value_stuffing == ()


# --------------------------------------------------------------------------
# deliverable 3: DNP text surgery + value stuffing on the .kicad_pcb
# --------------------------------------------------------------------------
class TestSurgery:
    def test_dnp_sets_attrs_rest_byte_identical(self) -> None:
        original = (FABRIC_DIR / "fabric.kicad_pcb").read_text()
        surgical = apply_pcb_surgery(original, {"U201", "R201", "R202", "C101"}, {})
        assert surgical != original
        # line-level diff: only (attr ...) lines for the four refs changed
        changed = [
            (o, n)
            for o, n in zip(
                original.splitlines(), surgical.splitlines(), strict=True
            )
            if o != n
        ]
        assert len(changed) == 4
        for _old, new in changed:
            assert "exclude_from_bom dnp" in new
        # non-attr bytes are identical: reconstruct original attr lines
        for old, new in changed:
            assert old.strip().startswith("(attr smd)")
            assert new.strip() == "(attr smd exclude_from_bom dnp)"

    def test_value_surgery_changes_only_value(self) -> None:
        original = (FABRIC_DIR / "fabric.kicad_pcb").read_text()
        surgical = apply_pcb_surgery(original, set(), {"R101": 3000.0})
        changed = [
            (o, n)
            for o, n in zip(
                original.splitlines(), surgical.splitlines(), strict=True
            )
            if o != n
        ]
        assert len(changed) == 1
        old, new = changed[0]
        assert '(property "Value" "10k"' in old
        assert '(property "Value" "3000"' in new

    def test_idempotent_dnp(self) -> None:
        original = (FABRIC_DIR / "fabric.kicad_pcb").read_text()
        once = apply_pcb_surgery(original, {"C101"}, {})
        twice = apply_pcb_surgery(once, {"C101"}, {})
        assert once == twice  # re-DNP'ing an already-DNP ref is a no-op


# --------------------------------------------------------------------------
# deliverables 3+4: stuffing emission + end-to-end
# --------------------------------------------------------------------------
class TestEndToEnd:
    def test_e2e_stuff(self, catalog, fabric, tmp_path) -> None:
        winners, resolved = _decide_winners(GAIN4_FRD, tmp_path, catalog)
        fr = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        out = tmp_path / "out"
        result = stuff(fabric, fr, out, catalog)

        board = result.board_path.read_text()
        # amp1 + dec0 DNP'd, amp0 NOT
        import re

        attrs = {
            m.group(1): m.group(2)
            for m in re.finditer(
                r'\(property "Reference" "([^"]*)".*?(\(attr [^)]*\))', board, re.S
            )
        }
        assert "dnp" in attrs["U201"] and "exclude_from_bom" in attrs["U201"]
        assert "dnp" in attrs["C101"]
        assert "dnp" not in attrs["U101"]  # stuffed site untouched
        assert "dnp" not in attrs["R101"]
        # value stuffed on the board
        assert '(property "Value" "3000"' in board

        # BOM: stuffed refs only, DNP'd absent
        bom_csv = result.bom_path.read_text()
        assert "R101" in bom_csv and "3000" in bom_csv
        assert "U101" in bom_csv
        assert "C101" not in bom_csv and "U201" not in bom_csv
        assert result.bom.summary.endswith("3 line item(s)")

        # report exists with the utilization line
        report = result.report_path.read_text()
        assert "utilization: **33%**" in report

    @pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli not on PATH")
    def test_stuffed_board_parses_with_kicad_cli(self, catalog, fabric, tmp_path) -> None:
        winners, resolved = _decide_winners(GAIN4_FRD, tmp_path, catalog)
        fr = fit(winners, fabric, catalog=catalog, resolved_params=resolved)
        out = tmp_path / "out"
        result = stuff(fabric, fr, out, catalog)
        # any pcb export is a parse proof that the mutated s-expression is valid
        proc = subprocess.run(
            [
                _KICAD_CLI,
                "pcb",
                "export",
                "svg",
                "--layers",
                "F.Cu",
                "--output",
                str(tmp_path / "proof.svg"),
                str(result.board_path),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert (tmp_path / "proof.svg").is_file()


class TestCli:
    def test_stuff_command(self, tmp_path, capsys) -> None:
        from infersynth.cli import main

        frd = tmp_path / "demo.md"
        frd.write_text(GAIN4_FRD)
        out = tmp_path / "out"
        rc = main(
            [
                "stuff",
                "--frd",
                str(frd),
                "--catalog",
                str(CATALOG),
                "--fabric",
                str(FABRIC_DIR),
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        printed = capsys.readouterr().out
        assert "utilization: 33%" in printed
        assert (out / "fabric.kicad_pcb").is_file()
        assert (out / "STUFFING.md").is_file()
        assert (out / "stuffing_bom.csv").is_file()


def test_site_dataclass_defaults() -> None:
    s = Site(id="x", cells=("a",), refs=("R1",))
    assert s.params_fixed == {}
    assert s.params_stuffable == ()
    assert s.ref_map is None
