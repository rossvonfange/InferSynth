"""Tool handlers for infersynth-mcp (BUILD_PLAN WP7, DESIGN.md section 9).

Every handler is a plain function: JSON-serializable kwargs in, a
JSON-serializable ``dict`` out (or a raised exception on error). This module
owns *no* logic of its own — it is a thin adapter that imports and calls the
library (:mod:`infersynth.lint`, :mod:`infersynth.catalog`,
:mod:`infersynth.bind`, :mod:`infersynth.compile_kicad.emit`,
:mod:`infersynth.gates`), per UX.md's "all four surfaces are adapters"
principle. :mod:`infersynth.mcp_server.server` wraps these for the MCP wire
protocol; tests call them directly (see module docstring in ``tests/
test_mcp_server.py``).

Tool list (DESIGN.md section 9, ``infersynth-mcp``):

* implemented today: ``lint_frd``, ``catalog_search``, ``catalog_validate``,
  ``bind_cell``, ``instantiate_cell``, ``run_gates``
* stubbed, raise :class:`NotImplementedStageError` naming the BUILD_PLAN
  stage that delivers them: ``elaborate_spec``, ``match_catalog``,
  ``synthesize``, ``catalog_submit_check``
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

__all__ = [
    "NotImplementedStageError",
    "lint_frd",
    "catalog_search",
    "catalog_validate",
    "bind_cell",
    "instantiate_cell",
    "run_gates",
    "elaborate_spec",
    "match_catalog",
    "synthesize",
    "catalog_submit_check",
    "TOOL_NAMES",
]


class NotImplementedStageError(NotImplementedError):
    """Raised by a registered-but-not-yet-implemented tool.

    Names the BUILD_PLAN/DESIGN.md roadmap stage that will deliver it, so an
    agent driving the MCP server gets an actionable "not yet, here's why"
    instead of a bare traceback.
    """

    def __init__(self, tool: str, stage: str) -> None:
        self.tool = tool
        self.stage = stage
        super().__init__(f"{tool}: not implemented yet — lands with {stage}.")


# --------------------------------------------------------------------------
# lint_frd
# --------------------------------------------------------------------------


def lint_frd(
    path: str | None = None,
    text: str | None = None,
    catalog_dir: str | None = None,
) -> dict[str, Any]:
    """Lint an FRD given either a *path* or inline *text* (exactly one).

    Returns ``{"diagnostics": [<LSP Diagnostic dict>, ...], "counts": {code:
    n, ...}}``. Catalog-vocabulary lint runs only when *catalog_dir* is
    given (grammar-only lint otherwise) — same contract as
    :func:`infersynth.lint.lint_path`.
    """
    if (path is None) == (text is None):
        raise ValueError("lint_frd: exactly one of 'path' or 'text' must be given")

    from infersynth.lint import lint_path as _lint_path
    from infersynth.lint import lint_requirement_set, parse_frd_text

    if path is not None:
        _, diagnostics = _lint_path(path, catalog_dir=catalog_dir)
    else:
        from infersynth.lint.vocab import Vocabulary

        reqset = parse_frd_text(text or "", "<text>")
        vocab = None
        if catalog_dir is not None:
            from infersynth.catalog import Catalog

            vocab = Vocabulary.from_catalog(Catalog.load(catalog_dir))
        diagnostics = lint_requirement_set(reqset, vocab)

    diag_dicts = [d.to_lsp() for d in diagnostics]
    counts = Counter(d.code for d in diagnostics)
    return {"diagnostics": diag_dicts, "counts": dict(sorted(counts.items()))}


# --------------------------------------------------------------------------
# catalog_search / catalog_validate
# --------------------------------------------------------------------------


def _cell_haystack(cell: Any) -> str:
    parts = [cell.name, cell.manifest.get("description", ""), *cell.keywords]
    return " ".join(str(p) for p in parts).lower()


def catalog_search(query: str, catalog_dir: str = "catalog") -> dict[str, Any]:
    """Cells whose keywords/description/name contain *query* (substring match).

    Simple, mechanical matching over :class:`infersynth.catalog.CellPackage`
    metadata — no ranking, no synthesis (that is the v2 inference engine,
    DESIGN.md section 10). Returns
    ``{"query": ..., "results": [{"cell": "name@version", "keywords": [...],
    "description": ...}, ...]}`` sorted by cell key.
    """
    from infersynth.catalog import Catalog

    catalog = Catalog.load(catalog_dir)
    q = (query or "").strip().lower()
    results = []
    for key in sorted(catalog.cells):
        cell = catalog.cells[key]
        if q and q not in _cell_haystack(cell):
            continue
        results.append(
            {
                "cell": key,
                "description": cell.manifest.get("description", ""),
                "keywords": list(cell.keywords),
            }
        )
    return {"query": query, "results": results}


def catalog_validate(catalog_dir: str) -> dict[str, Any]:
    """Run the catalog validator (same path as ``infersynth catalog validate``).

    Returns ``{"ok": True, "cells": [<name@version>, ...]}`` or ``{"ok":
    False, "diagnostics": [...]}``.
    """
    from infersynth.catalog import Catalog, CatalogError

    try:
        catalog = Catalog.load(catalog_dir)
    except CatalogError as exc:
        return {"ok": False, "diagnostics": list(exc.diagnostics)}
    return {"ok": True, "cells": sorted(catalog.cells)}


# --------------------------------------------------------------------------
# bind_cell / instantiate_cell
# --------------------------------------------------------------------------


def bind_cell(cell_dir: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve *cell_dir*'s bindings against *params* (:mod:`infersynth.bind`).

    Returns ``{"cell": "name@version", "bindings": {ref: value, ...}}``.
    Raises :class:`infersynth.catalog.CellPackageError` for an invalid cell
    package or :class:`infersynth.bind.BindingError` for invalid/out-of-range
    params — surfaced as-is (thin adapter; no logic added here).
    """
    from infersynth.bind import bind_cell as _bind_cell
    from infersynth.catalog import load_cell

    cell = load_cell(cell_dir)
    values = _bind_cell(cell, params or {})
    return {"cell": cell.key, "bindings": values}


def instantiate_cell(
    cell_dir: str,
    instname: str,
    design_dir: str,
    params: dict[str, Any] | None = None,
    parent_sch: str | None = None,
) -> dict[str, Any]:
    """Instantiate *cell_dir* as *instname* into *design_dir* (WP3 emitter).

    When *parent_sch* is omitted, a fresh design is created via
    :func:`infersynth.compile_kicad.emit.new_design`, named after
    ``Path(design_dir).stem``. Returns ``{"design_dir", "parent_sch",
    "child_sch"}`` (paths, as strings) — the files WP3's ``instantiate``
    wrote.
    """
    from infersynth.catalog import load_cell
    from infersynth.compile_kicad import emit

    cell = load_cell(cell_dir)
    design_dir_path = Path(design_dir)

    if parent_sch is None:
        design_dir_path.mkdir(parents=True, exist_ok=True)
        parent_sch_path = emit.new_design(design_dir_path, design_dir_path.stem)
    else:
        parent_sch_path = Path(parent_sch)

    child_sch_path = emit.instantiate(
        cell, params or {}, instname, design_dir_path, parent_sch_path
    )
    return {
        "design_dir": str(design_dir_path),
        "parent_sch": str(parent_sch_path),
        "child_sch": str(child_sch_path),
    }


# --------------------------------------------------------------------------
# run_gates
# --------------------------------------------------------------------------


def _parse_golden_netlist(text: str) -> dict[str, set[tuple[str, str]]]:
    """Parse a cell's ``golden_netlist.txt`` (catalog authoring format).

    Lines look like ``/FB: R1/2, R2/1, U1/4`` — net name, colon, comma-
    separated ``REF/PIN`` tokens. Comments (``#``) and blank lines are
    ignored. This is a data-format reader, not gate logic: the actual
    comparison is :func:`infersynth.gates.compare_partitions`.
    """
    partition: dict[str, set[tuple[str, str]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        net, rest = line.split(":", 1)
        pins: set[tuple[str, str]] = set()
        for token in rest.split(","):
            token = token.strip()
            if not token:
                continue
            ref, _, pin = token.rpartition("/")
            pins.add((ref, pin))
        partition[net.strip()] = pins
    return partition


def run_gates(
    design_dir: str | None = None,
    cell_dir: str | None = None,
    ir_partition: dict[str, Any] | None = None,
    netlist_partition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run :func:`infersynth.gates.default_runner` and return a ``GateReport`` dict.

    The full cell-CI harness (BUILD_PLAN WP4: harness generator + kicad-cli
    ERC + SystemC-AMS simulation) may still be stubbed on this branch — this
    tool calls whatever :mod:`infersynth.gates` exposes *today* and lets
    those gates report themselves SKIPPED (loudly; see ``GateReport.summary``).

    What actually runs here: netlist-partition-equivalence, when it has both
    partitions. *ir_partition* / *netlist_partition* may be passed directly;
    if *netlist_partition* is omitted and *cell_dir* names a cell with a
    ``verification.golden_netlist`` file, that golden file is parsed and used
    as the netlist partition (comparing against the caller-supplied
    *ir_partition* — this is the "vs golden file when given" path).
    """
    from infersynth.gates import default_runner

    context: dict[str, Any] = {}
    if ir_partition is not None:
        context["ir_partition"] = ir_partition

    resolved_netlist = netlist_partition
    if resolved_netlist is None and cell_dir is not None:
        from infersynth.catalog import load_cell

        cell = load_cell(cell_dir)
        golden_name = cell.verification.get("golden_netlist")
        if golden_name:
            golden_text = (Path(cell_dir) / golden_name).read_text()
            resolved_netlist = _parse_golden_netlist(golden_text)
    if resolved_netlist is not None:
        context["netlist_partition"] = resolved_netlist

    if design_dir is not None:
        context["schematic_path"] = str(Path(design_dir))

    report = default_runner().run(context)
    return {
        "ok": report.ok,
        "results": [
            {"gate": r.gate, "status": r.status.value, "diagnostics": list(r.diagnostics)}
            for r in report.results
        ],
        "summary": report.summary(),
    }


# --------------------------------------------------------------------------
# Not-yet-implemented tools (DESIGN.md section 9 names them; DESIGN.md
# section 10 "Roadmap — Synth before Infer" says when they land).
# --------------------------------------------------------------------------


def elaborate_spec(**_kwargs: Any) -> dict[str, Any]:
    """Elaborate a formal spec into an IR :class:`~infersynth.ir.core.Design`."""
    raise NotImplementedStageError(
        "elaborate_spec",
        "BUILD_PLAN Stage 2/3 spec-loading + elaboration pipeline "
        "(DESIGN.md section 10, v1 Synth roadmap)",
    )


def match_catalog(**_kwargs: Any) -> dict[str, Any]:
    """Match spec idioms to catalog cells with scored part binding."""
    raise NotImplementedStageError(
        "match_catalog",
        "the v2 deterministic inference engine — idiom -> catalog match + "
        "selection.yaml scoring (DESIGN.md section 10, v2 Infer)",
    )


def synthesize(**_kwargs: Any) -> dict[str, Any]:
    """End-to-end FRD/spec -> verified schematic synthesis."""
    raise NotImplementedStageError(
        "synthesize",
        "the full synthesis pipeline once elaborate_spec + match_catalog land "
        "(DESIGN.md section 10); today, drive lint_frd -> bind_cell -> "
        "instantiate_cell -> run_gates by hand",
    )


def catalog_submit_check(**_kwargs: Any) -> dict[str, Any]:
    """Pre-flight a community catalog submission."""
    raise NotImplementedStageError(
        "catalog_submit_check",
        "the v3 intake stage — community submission pipeline "
        "(DESIGN.md section 10, v3 intake)",
    )


TOOL_NAMES: tuple[str, ...] = (
    "lint_frd",
    "catalog_search",
    "catalog_validate",
    "bind_cell",
    "instantiate_cell",
    "run_gates",
    "elaborate_spec",
    "match_catalog",
    "synthesize",
    "catalog_submit_check",
)
