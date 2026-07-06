"""Sidecar panel tests (BUILD_PLAN WP8)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from infersynth.gates import GateReport, GateResult  # noqa: E402
from infersynth.panel import gate_io, render  # noqa: E402
from infersynth.panel.app import create_app  # noqa: E402

GOLDEN_CATALOG = Path(__file__).parent.parent / "catalog"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = create_app(catalog_dir=GOLDEN_CATALOG, cache_dir=tmp_path / "svg-cache")
    return TestClient(app)


def test_catalog_list_shows_both_cells(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "opamp-gain-noninverting" in body
    assert "opamp-gain-x4-noninverting" in body


def test_cell_page_contains_binding_expression(client: TestClient):
    resp = client.get("/cell/opamp-gain-noninverting/0.1.0")
    assert resp.status_code == 200
    body = resp.text
    assert "(gain - 1) * rg_ohms" in body
    assert "rg_ohms" in body
    # rendered sections present
    for heading in ("Manifest", "Ports", "Idioms", "Bindings", "Selection", "Depth"):
        assert f">{heading}<" in body


def test_cell_page_404_for_unknown_cell(client: TestClient):
    resp = client.get("/cell/does-not-exist/0.0.0")
    assert resp.status_code == 404


def test_gates_index_lists_reports(tmp_path):
    report = GateReport(
        results=[GateResult.passed("erc", "no errors")],
    )
    (tmp_path / "run1.json").write_text(
        __import__("json").dumps(gate_io.report_to_dict(report))
    )
    app = create_app(catalog_dir=GOLDEN_CATALOG, reports_dir=tmp_path)
    client = TestClient(app)
    resp = client.get("/gates")
    assert resp.status_code == 200
    assert "run1.json" in resp.text


def test_gates_page_renders_synthetic_report(client: TestClient, tmp_path):
    # Built from the real GateReport/GateResult classes so the JSON shape
    # displayed here cannot silently drift from what infersynth.gates emits.
    report = GateReport(
        results=[
            GateResult.passed("erc", "0 errors, 0 warnings"),
            GateResult.failed("netlist-partition-equivalence", "net VCC: ref mismatch"),
            GateResult.skipped("simulation", "SystemC-AMS toolchain not installed"),
        ]
    )
    report_path = tmp_path / "synthetic.json"
    report_path.write_text(__import__("json").dumps(gate_io.report_to_dict(report)))

    resp = client.get("/gates", params={"report": str(report_path)})
    assert resp.status_code == 200
    body = resp.text

    assert "erc" in body
    assert "PASS" in body
    assert "netlist-partition-equivalence" in body
    assert "net VCC: ref mismatch" in body
    assert "FAIL" in body
    assert "simulation" in body
    assert "SKIPPED" in body
    # loud not-fully-verified banner, mirroring gates/runner.py's wording
    assert "NOT fully verified" in body
    assert "RESULT: FAILED" in body


def test_gates_page_bad_report_path_shows_error(client: TestClient, tmp_path):
    resp = client.get("/gates", params={"report": str(tmp_path / "missing.json")})
    assert resp.status_code == 400
    assert "cannot read" in resp.text


@pytest.mark.kicad
@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not on PATH")
def test_fragment_svg_renders_when_kicad_cli_present(client: TestClient):
    resp = client.get("/cell/opamp-gain-noninverting/0.1.0/fragment.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in resp.text


def test_fragment_svg_placeholder_when_kicad_cli_absent(client: TestClient, monkeypatch):
    monkeypatch.setattr(render, "kicad_cli_available", lambda: False)
    resp = client.get("/cell/opamp-gain-noninverting/0.1.0/fragment.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "fragment preview unavailable" in resp.text


def test_fragment_svg_404_for_unknown_cell(client: TestClient):
    resp = client.get("/cell/nope/0.0.0/fragment.svg")
    assert resp.status_code == 404
