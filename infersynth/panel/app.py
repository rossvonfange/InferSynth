"""FastAPI app for the sidecar panel (BUILD_PLAN WP8; UX.md "sidecar web panel").

A thin, read-mostly adapter: it imports ``infersynth.catalog`` and
``infersynth.gates`` and renders what they already know. No synthesis, no
lint, no part binding lives here (UX.md anti-goals: "no owned GUI" beyond a
read-mostly viewer).

Routes:

* ``GET /`` — catalog browser (list of cells).
* ``GET /cell/{name}/{version}`` — one cell's full cell.yaml, rendered.
* ``GET /cell/{name}/{version}/fragment.svg`` — rendered fragment (or a
  graceful placeholder if kicad-cli is unavailable).
* ``GET /gates`` — list *.json reports under --reports, or render one via
  ``?report=<path>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, Response

from infersynth.catalog import Catalog, CatalogError, CellPackage
from infersynth.panel import gate_io, render, templates

__all__ = ["create_app"]


def _cell_summary(cell: CellPackage) -> dict[str, Any]:
    return {
        "name": cell.name,
        "version": cell.version,
        "description": cell.manifest.get("description", ""),
        "depth": cell.depth.get("level", "L0"),
        "keywords": list(cell.keywords),
    }


def _cell_detail(cell: CellPackage) -> dict[str, Any]:
    return {
        **_cell_summary(cell),
        "manifest": dict(cell.manifest),
        "ports": dict(cell.ports),
        "keywords": list(cell.keywords),
        "idiom_params": dict(cell.idiom_params),
        "disambiguation": cell.disambiguation,
        "bindings": dict(cell.bindings),
        "selection": dict(cell.selection),
        "depth": dict(cell.depth),
    }


def create_app(
    catalog_dir: str | Path = "catalog",
    reports_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    Catalog is loaded once at construction time (read-mostly: the panel does
    not watch for changes). A catalog that fails validation does not crash
    the panel — the load error is shown as a banner on the catalog page.
    """
    catalog_dir = Path(catalog_dir)
    reports_dir = Path(reports_dir) if reports_dir is not None else None
    svg_cache_dir = Path(cache_dir) if cache_dir is not None else render.default_cache_dir()

    cells: dict[str, CellPackage] = {}
    load_errors: list[str] = []
    try:
        catalog = Catalog.load(catalog_dir)
        cells = dict(catalog.cells)
    except CatalogError as exc:
        load_errors = list(exc.diagnostics)
    except FileNotFoundError as exc:
        load_errors = [str(exc)]

    app = FastAPI(title="InferSynth sidecar panel")
    app.state.cells = cells
    app.state.load_errors = load_errors
    app.state.reports_dir = reports_dir
    app.state.svg_cache_dir = svg_cache_dir

    def _find_cell(name: str, version: str) -> CellPackage | None:
        return cells.get(f"{name}@{version}")

    @app.get("/", response_class=HTMLResponse)
    def catalog_index() -> str:
        summaries = [_cell_summary(c) for c in sorted(cells.values(), key=lambda c: c.key)]
        return templates.catalog_list(summaries, load_errors)

    @app.get("/cell/{name}/{version}", response_class=HTMLResponse)
    def cell_detail(name: str, version: str) -> Response:
        cell = _find_cell(name, version)
        if cell is None:
            return HTMLResponse(
                templates.error_page(
                    "Cell not found", f"no cell {name}@{version} in the loaded catalog"
                ),
                status_code=404,
            )
        svg_url = f"/cell/{name}/{version}/fragment.svg"
        return HTMLResponse(templates.cell_page(_cell_detail(cell), svg_url))

    @app.get("/cell/{name}/{version}/fragment.svg")
    def cell_fragment_svg(name: str, version: str) -> Response:
        cell = _find_cell(name, version)
        if cell is None:
            return Response(render.PLACEHOLDER_SVG, media_type="image/svg+xml", status_code=404)
        svg_path = render.render_fragment_svg(cell, svg_cache_dir)
        if svg_path is None:
            return Response(render.PLACEHOLDER_SVG, media_type="image/svg+xml")
        return Response(svg_path.read_text(), media_type="image/svg+xml")

    @app.get("/gates", response_class=HTMLResponse)
    def gates_view(report: str | None = Query(default=None)) -> Response:
        if report is not None:
            try:
                data = gate_io.load_report_dict(report)
            except gate_io.GateReportParseError as exc:
                return HTMLResponse(
                    templates.error_page("Gate report error", str(exc)), status_code=400
                )
            return HTMLResponse(templates.gates_report(report, data))

        files: list[str] = []
        if reports_dir is not None and reports_dir.is_dir():
            files = sorted(str(p) for p in reports_dir.glob("*.json"))
        return HTMLResponse(templates.gates_index(files))

    return app
