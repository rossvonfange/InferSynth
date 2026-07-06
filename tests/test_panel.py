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
from infersynth.synthesize import synthesize  # noqa: E402

GOLDEN_CATALOG = Path(__file__).parent.parent / "catalog"

# Same demo text as tests/test_synthesize.py's DEMO_FRD (duplicated here
# rather than imported so this test module doesn't reach into another
# test module's internals — see test_synthesize.DEMO_FRD for the source of
# truth on wording).
DEMO_FRD = """# Demo sensor board

- The board shall include a non-inverting amplifier gain stage with gain of 4.
- The board shall include a voltage reference.
- The board shall include decoupling.
- The board shall include an adc driver.
- The board shall include a 4-wire sensor input connector.
"""

# Deliberately omits a gain value: rg_ohms still has a cell.yaml default (so
# it resolves to "default"), but gain has no default — only a [1.0, 1000.0]
# range — so it falls all the way to the harness's deterministic "assumed
# midpoint" rule (infersynth.synthesize._params_for).
NO_GAIN_FRD = """# Demo sensor board (no gain given)

- The board shall include a non-inverting amplifier gain stage.
- The board shall include a voltage reference.
- The board shall include decoupling.
- The board shall include an adc driver.
- The board shall include a 4-wire sensor input connector.
"""


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


def _write_demo_design(tmp_path: Path, frd_text: str, design_name: str) -> Path:
    frd = tmp_path / f"{design_name}.md"
    frd.write_text(frd_text, encoding="utf-8")
    out = tmp_path / "designs" / design_name
    synthesize(frd, GOLDEN_CATALOG, out, profile="prototype")
    return out


def test_designs_index_lists_synthesized_design(tmp_path: Path):
    _write_demo_design(tmp_path, DEMO_FRD, "demo")
    app = create_app(catalog_dir=GOLDEN_CATALOG, designs_dir=tmp_path / "designs")
    client = TestClient(app)

    resp = client.get("/designs")
    assert resp.status_code == 200
    body = resp.text
    assert "demo" in body
    # DEMO_FRD's 5 requirements all reach a decided winner (test_synthesize's
    # own test_full_pipeline asserts result.all_decided for this FRD).
    assert "5/5" in body
    assert f'/design?path={tmp_path / "designs" / "demo"}' in body


def test_designs_index_dir_query_param_overrides_startup_dir(tmp_path: Path):
    _write_demo_design(tmp_path, DEMO_FRD, "demo")
    # No --designs at startup; ?dir= alone should still find it.
    app = create_app(catalog_dir=GOLDEN_CATALOG)
    client = TestClient(app)

    resp = client.get("/designs", params={"dir": str(tmp_path / "designs")})
    assert resp.status_code == 200
    assert "demo" in resp.text


def test_designs_index_empty_without_designs_dir(client: TestClient):
    resp = client.get("/designs")
    assert resp.status_code == 200
    assert "No designs directory configured" in resp.text


def test_design_page_shows_winner_and_assumed_marker(tmp_path: Path):
    out = _write_demo_design(tmp_path, NO_GAIN_FRD, "no_gain")
    app = create_app(catalog_dir=GOLDEN_CATALOG, designs_dir=tmp_path / "designs")
    client = TestClient(app)

    resp = client.get("/design", params={"path": str(out)})
    assert resp.status_code == 200
    body = resp.text

    # SYNTHESIS.md rendered: the winner cell key appears (as a table cell in
    # the "Instantiated cells" section) and its assumed gain is flagged.
    assert "core/opamp-gain-noninverting@0.1.0" in body
    assert "⚠ASSUMED" in body
    assert 'class="assumed"' in body

    # selection trace rendered structurally: winner badge + justification.
    assert "WINNER" in body
    assert "Justification" in body
    assert "Candidates considered" in body


def test_design_page_404_when_no_synthesis_md(tmp_path: Path):
    app = create_app(catalog_dir=GOLDEN_CATALOG)
    client = TestClient(app)
    resp = client.get("/design", params={"path": str(tmp_path)})
    assert resp.status_code == 404


def test_design_page_trace_shape_mismatch_shows_error(tmp_path: Path):
    out = tmp_path / "broken"
    out.mkdir()
    (out / "SYNTHESIS.md").write_text("# Synthesis report\n", encoding="utf-8")
    (out / "selection_trace.json").write_text('{"schema": "not-the-right-schema"}')

    app = create_app(catalog_dir=GOLDEN_CATALOG)
    client = TestClient(app)
    resp = client.get("/design", params={"path": str(out)})
    assert resp.status_code == 200
    assert "Selection trace error" in resp.text
    assert "schema" in resp.text.lower()


def test_nav_present_on_catalog_gates_and_designs_pages(client: TestClient):
    for url in ("/", "/gates", "/designs"):
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.text
        assert 'href="/"' in body
        assert 'href="/gates"' in body
        assert 'href="/designs"' in body
