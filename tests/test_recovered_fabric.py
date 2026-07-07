"""Tests for the recovered-fabric verification gate (Loom Pillar 2 honesty).

The gate independently re-verifies a RecognitionResult against its source
netlist: each recognized site is re-matched structurally and its recovered
params are pushed forward parametrically; glue (residual) is declared-not-
verified and always listed. Coverage:

* closed-loop PASS on a clean recovered fabric (hand fixture, no kicad-cli;
  plus a kicad-gated round-trip against a real InferSynth forward weave);
* NEGATIVE parametric: a perturbed recovered param fails its site loudly;
* NEGATIVE structural: a mutated source netlist (missing part) fails the
  site's subgraph re-match;
* GLUE: an unrecognized component passes the verdict but is listed
  declared-not-verified;
* report shape (schema/dict/markdown/GateResult) + determinism.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from infersynth.catalog import Catalog
from infersynth.compile_kicad.emit import instantiate, new_design
from infersynth.gates.recovered_fabric import (
    RecoveredFabricReport,
    recovered_fabric_gate,
    verify_recovered_fabric,
)
from infersynth.gates.runner import GateStatus
from infersynth.recognize import load_design_netlist, recognize
from infersynth.recognize.netlist import Component, DesignNetlist

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"
NONINV = "core/opamp-gain-noninverting@0.1.0"
OPA_MPN = "OPA340NA/250"
R_MPN = "RC0603FR-0710KL"

_KICAD = shutil.which("kicad-cli")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(CATALOG_DIR)


# --------------------------------------------------------------------------- #
# fixtures: a clean gain-100 non-inverting amp (shape mirrors test_recognize)  #
# --------------------------------------------------------------------------- #
_NONINV_NETS = {
    "/FB": [("R1", "2"), ("R2", "1"), ("U1", "4")],
    "/GND": [("R2", "2")],
    "/IN": [("U1", "3")],
    "/OUT": [("R1", "1"), ("U1", "1")],
    "/VCC": [("U1", "5")],
    "/VEE": [("U1", "2")],
}
_NONINV_COMPS = {
    "R1": ("99k", R_MPN),
    "R2": ("1k", R_MPN),
    "U1": ("OPAMP_SINGLE", OPA_MPN),
}


def _design(
    comps: dict[str, tuple[str, str]], nets: dict[str, list[tuple[str, str]]]
) -> DesignNetlist:
    components = {
        ref: Component.build(ref, val, "fp", mpn) for ref, (val, mpn) in comps.items()
    }
    pin_net = {(r, p): name for name, pins in nets.items() for r, p in pins}
    return DesignNetlist(components=components, nets=dict(nets), pin_net=pin_net)


# --------------------------------------------------------------------------- #
# closed loop: clean recovered fabric verifies (hand fixture, no kicad-cli)    #
# --------------------------------------------------------------------------- #
def test_clean_fabric_all_sites_verify(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    recognition = recognize(design, catalog)
    report = verify_recovered_fabric(recognition, design, catalog)

    assert report.verified is True
    assert len(report.sites) == 1
    site = report.sites[0]
    assert site.cell_key == NONINV
    assert site.structural_ok and site.parametric_ok and site.verified
    assert site.design_refs == ("R1", "R2", "U1")
    assert site.residual < 1e-9
    assert report.glue_count == 0
    assert report.failed_sites == ()


def test_clean_fabric_gate_result_passes(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    recognition = recognize(design, catalog)
    result = recovered_fabric_gate(recognition, design, catalog)
    assert result.status == GateStatus.PASS
    # glue-count is reported even on PASS (honesty: never hidden)
    assert any("no glue" in d for d in result.diagnostics)


# --------------------------------------------------------------------------- #
# NEGATIVE — parametric: a perturbed recovered param fails its site            #
# --------------------------------------------------------------------------- #
def test_perturbed_param_fails_parametric(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    recognition = recognize(design, catalog)
    inst = recognition.instances[0]
    # push the recovered gain off by 2x — the values no longer reproduce
    bad_params = {**inst.inversion.params, "gain": inst.inversion.params["gain"] * 2.0}
    bad_inst = replace(inst, inversion=replace(inst.inversion, params=bad_params))
    bad_recognition = replace(recognition, instances=(bad_inst,))

    report = verify_recovered_fabric(bad_recognition, design, catalog)
    assert report.verified is False
    site = report.sites[0]
    assert site.structural_ok is True  # topology unchanged
    assert site.parametric_ok is False
    assert site.residual > 0.1
    assert any("PARAMETRIC" in d and "residual" in d for d in site.detail)

    result = recovered_fabric_gate(bad_recognition, design, catalog)
    assert result.status == GateStatus.FAIL
    assert any("FAILED" in d for d in result.diagnostics)


# --------------------------------------------------------------------------- #
# NEGATIVE — structural: a mutated source netlist fails the subgraph re-match  #
# --------------------------------------------------------------------------- #
def test_swapped_topology_fails_structural(catalog: Catalog) -> None:
    """Recognize the clean fabric, then verify it against a MUTATED source
    (feedback resistor R2 removed): the anchored re-match no longer holds."""
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    recognition = recognize(design, catalog)

    mutated_nets = {k: [pp for pp in v if pp[0] != "R2"] for k, v in _NONINV_NETS.items()}
    mutated_comps = {r: v for r, v in _NONINV_COMPS.items() if r != "R2"}
    mutated = _design(mutated_comps, mutated_nets)

    report = verify_recovered_fabric(recognition, mutated, catalog)
    assert report.verified is False
    site = report.sites[0]
    assert site.structural_ok is False
    assert any("STRUCTURAL" in d for d in site.detail)


# --------------------------------------------------------------------------- #
# GLUE — an unrecognized component: verdict PASS but declared-not-verified     #
# --------------------------------------------------------------------------- #
def test_glue_is_listed_but_passes(catalog: Catalog) -> None:
    comps = {**_NONINV_COMPS, "R9": ("330", "SOME-UNKNOWN-MPN")}
    nets = {
        **{k: list(v) for k, v in _NONINV_NETS.items()},
        "/AUX": [("R9", "1"), ("R9", "2")],
    }
    design = _design(comps, nets)
    recognition = recognize(design, catalog)
    assert "R9" in recognition.residual

    report = verify_recovered_fabric(recognition, design, catalog)
    # glue presence does NOT fail the verdict
    assert report.verified is True
    assert report.glue_count == 1
    assert [g.ref for g in report.glue] == ["R9"]
    assert report.glue[0].value == "330"

    md = report.to_markdown()
    assert "DECLARED-NOT-VERIFIED" in md
    assert "`R9`" in md

    # even on the PASS GateResult the glue is loudly reported
    result = report.to_gate_result()
    assert result.status == GateStatus.PASS
    assert any("DECLARED-NOT-VERIFIED" in d and "R9" in d for d in result.diagnostics)


# --------------------------------------------------------------------------- #
# report shape + determinism                                                   #
# --------------------------------------------------------------------------- #
def test_report_dict_shape(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    report = verify_recovered_fabric(recognize(design, catalog), design, catalog)
    d = report.to_dict()
    assert d["schema"] == "infersynth.gates.recovered-fabric/v0"
    assert d["verdict"] == "pass"
    assert d["summary"]["sites_total"] == 1
    assert d["summary"]["sites_verified"] == 1
    assert d["summary"]["glue_declared_not_verified"] == 0
    assert d["sites"][0]["cell_key"] == NONINV
    assert d["sites"][0]["verified"] is True


def test_determinism(catalog: Catalog) -> None:
    design = _design(_NONINV_COMPS, _NONINV_NETS)
    a = verify_recovered_fabric(recognize(design, catalog), design, catalog).to_dict()
    b = verify_recovered_fabric(recognize(design, catalog), design, catalog).to_dict()
    assert a == b


def test_accepts_netlist_path(catalog: Catalog, tmp_path: Path) -> None:
    """The gate also accepts a kicadxml path for the source netlist (loads it)."""
    comp_xml = "".join(
        f'<comp ref="{ref}"><value>{val}</value><footprint>fp</footprint>'
        f'<property name="MPN" value="{mpn}"/></comp>'
        for ref, (val, mpn) in _NONINV_COMPS.items()
    )
    net_xml = "".join(
        f'<net code="{i}" name="{name}">'
        + "".join(f'<node ref="{r}" pin="{p}"/>' for r, p in pins)
        + "</net>"
        for i, (name, pins) in enumerate(_NONINV_NETS.items(), 1)
    )
    xml = tmp_path / "hand.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><export version="E">'
        f"<components>{comp_xml}</components><nets>{net_xml}</nets></export>"
    )
    recognition = recognize(load_design_netlist(xml), catalog)
    report = verify_recovered_fabric(recognition, xml, catalog)
    assert isinstance(report, RecoveredFabricReport)
    assert report.verified is True


# --------------------------------------------------------------------------- #
# the elegant proof: closed-loop round-trip against a real forward weave       #
# --------------------------------------------------------------------------- #
@pytest.mark.kicad
@pytest.mark.slow
@pytest.mark.skipif(_KICAD is None, reason="kicad-cli not on PATH")
def test_closed_loop_roundtrip_verifies(catalog: Catalog, tmp_path: Path) -> None:
    """Synthesize a gain-100 amp with InferSynth's forward path, export its
    netlist, recognize it, and run the recovered-fabric gate: every recovered
    site verifies (structural + parametric, ~zero residual) with no glue."""
    root = new_design(tmp_path, "rt")
    instantiate(catalog.get(NONINV), {"gain": 100.0, "rg_ohms": 1000.0}, "c1", tmp_path, root)
    nl = tmp_path / "rt.net.xml"
    subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
         "-o", str(nl), str(root)],
        check=True, capture_output=True,
    )
    design = load_design_netlist(nl)
    recognition = recognize(design, catalog)
    report = verify_recovered_fabric(recognition, design, catalog)

    assert report.verified is True
    assert report.glue_count == 0
    assert len(report.sites) == 1
    site = report.sites[0]
    assert site.cell_key == NONINV
    assert site.structural_ok and site.parametric_ok
    assert site.residual < 1e-3
