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
* ``GET /designs`` — list subdirectories (under --designs, or ``?dir=``)
  that contain a ``SYNTHESIS.md``, with decided/undecided counts read from
  each design's ``selection_trace.json``.
* ``GET /design?path=...`` — render one design directory: its
  ``SYNTHESIS.md`` and its ``selection_trace.json`` (decision explanation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, Response

from infersynth.catalog import Catalog, CatalogError, CellPackage
from infersynth.panel import gate_io, render, templates, trace_io

__all__ = ["create_app"]


def _cell_summary(cell: CellPackage) -> dict[str, Any]:
    return {
        "name": cell.name,
        "version": cell.version,
        "library": cell.library or "",
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


def _list_designs(base: Path) -> list[dict[str, Any]]:
    """Immediate subdirectories of *base* that contain a ``SYNTHESIS.md``.

    Decided/undecided counts come from each design's ``selection_trace.json``
    when present and shape-valid; a design without a (valid) trace still
    lists (0/0 — the panel never hides a design over a trace problem, it
    surfaces the problem on the design's own page instead).
    """
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for sub in sorted(p for p in base.iterdir() if p.is_dir()):
        if not (sub / "SYNTHESIS.md").is_file():
            continue
        decided = total = 0
        trace_file = sub / "selection_trace.json"
        if trace_file.is_file():
            try:
                trace = trace_io.load_trace_dict(trace_file)
                total = len(trace["requirements"])
                decided = sum(1 for r in trace["requirements"] if r["status"] == "decided")
            except trace_io.TraceParseError:
                pass
        out.append({"name": sub.name, "path": str(sub), "decided": decided, "total": total})
    return out


def create_app(
    catalog_dir: str | Path = "catalog",
    reports_dir: str | Path | None = None,
    designs_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    Catalog is loaded once at construction time (read-mostly: the panel does
    not watch for changes). A catalog that fails validation does not crash
    the panel — the load error is shown as a banner on the catalog page.
    """
    catalog_dir = Path(catalog_dir)
    reports_dir = Path(reports_dir) if reports_dir is not None else None
    designs_dir = Path(designs_dir) if designs_dir is not None else None
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
    app.state.designs_dir = designs_dir
    app.state.svg_cache_dir = svg_cache_dir

    def _find_cell(name: str, version: str) -> CellPackage | None:
        # Route stays library-agnostic (name/version only — no panel
        # redesign); resolve by bare_key since cells loaded from a two-level
        # catalog are keyed "library/name@version" internally.
        bare = f"{name}@{version}"
        return next((c for c in cells.values() if c.bare_key == bare), None)

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

    @app.get("/designs", response_class=HTMLResponse)
    def designs_view(dir: str | None = Query(default=None)) -> Response:  # noqa: A002
        base = Path(dir) if dir is not None else designs_dir
        designs = _list_designs(base) if base is not None else []
        return HTMLResponse(
            templates.designs_index(designs, str(base) if base is not None else None)
        )

    @app.get("/design", response_class=HTMLResponse)
    def design_view(path: str = Query(...)) -> Response:
        design_dir = Path(path)
        synth_path = design_dir / "SYNTHESIS.md"
        if not synth_path.is_file():
            return HTMLResponse(
                templates.error_page(
                    "Design not found", f"no SYNTHESIS.md under {path}"
                ),
                status_code=404,
            )
        synthesis_html = templates.render_markdown(synth_path.read_text(encoding="utf-8"))

        trace: dict[str, Any] | None = None
        trace_error: str | None = None
        trace_path = design_dir / "selection_trace.json"
        if trace_path.is_file():
            try:
                trace = trace_io.load_trace_dict(trace_path)
            except trace_io.TraceParseError as exc:
                trace_error = str(exc)

        return HTMLResponse(
            templates.design_page(design_dir.name, synthesis_html, trace, trace_error)
        )

    return app
