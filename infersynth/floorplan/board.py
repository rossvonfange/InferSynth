"""Placed-board emission (docs/FLOORPLAN.md deliverable 4).

Gathers a synthesized design's structure — the century-stamped netlist export
(refs + footprints), ``wiring_plan.json`` (nets + instance order), and the
catalog (port directions + connector facts) — runs the pure floorplanner
(:mod:`infersynth.floorplan.plan`), and materializes the result as a grouped,
UNROUTED ``.kicad_pcb``: one KiCad ``(group)`` per instance, footprints at their
floorplan positions, a board outline on Edge.Cuts.

pcbnew lives only in a subprocess (:mod:`infersynth.floorplan._pcbnew_emit`,
run under the KiCad-bundled python) — this module and the pure planner never
import it, so the whole floorplanner stays importable and testable in the plain
venv. Determinism is preserved: the subprocess only *places* the pre-computed
plan; it makes no layout decisions.

**Electrically unconnected in v0 (loud):** the emitted board carries no nets and
no ratsnest — the deliverable is PLACEMENT structure, a starting point the user
routes in pcbnew (which IS the floorplanner from here). Ratsnest import from the
schematic netlist is the documented next refinement.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from infersynth.catalog import Catalog
from infersynth.floorplan.plan import (
    ComponentLite,
    Floorplan,
    NetLite,
    PlacementHints,
    check_no_overlap,
    floorplan,
)
from infersynth.spec import PlacementSpec

__all__ = ["BoardEmitError", "build_floorplan", "emit_board", "EmitResult"]

#: KiCad-bundled python (has pcbnew). Override with $INFERSYNTH_PCBNEW_PYTHON.
_DEFAULT_PCBNEW_PYTHON = "/home/cycix/Desktop/fai-tuner/KiCAD-MCP-Server/venv/bin/python"
#: stock footprint library root. Override with $INFERSYNTH_FP_DIR.
_DEFAULT_FP_DIR = "/usr/share/kicad/footprints"
_EMIT_SCRIPT = Path(__file__).with_name("_pcbnew_emit.py")


class BoardEmitError(RuntimeError):
    """Raised when board gathering or pcbnew emission fails."""


@dataclass(frozen=True)
class EmitResult:
    """Outcome of :func:`emit_board`: the plan + written board + stats."""

    board_path: Path
    plan: Floorplan
    footprint_count: int
    group_count: int
    overlaps: list[tuple[str, str]]


def _parse_components(xml_path: Path) -> list[ComponentLite]:
    """Parse ``<components>`` (ref, value, footprint) from a kicadxml netlist."""
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise BoardEmitError(f"cannot parse netlist {xml_path}: {exc}") from exc
    comps: list[ComponentLite] = []
    container = root.find("components")
    if container is None:
        return comps
    for comp in container.findall("comp"):
        ref = comp.get("ref") or ""
        fp = (comp.findtext("footprint") or "").strip()
        val = (comp.findtext("value") or "").strip()
        if ref.startswith("#") or not fp:
            continue  # virtual ref or unfootprinted part — not placeable
        comps.append(ComponentLite(ref=ref, value=val, footprint=fp))
    return comps


def _export_components(root_sch: Path) -> list[ComponentLite]:
    if shutil.which("kicad-cli") is None:
        raise BoardEmitError("kicad-cli not found on PATH (needed for the netlist export)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "netlist.xml"
        subprocess.run(
            ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
             "-o", str(out), str(root_sch)],
            capture_output=True, text=True,
        )
        if not out.is_file():
            raise BoardEmitError(f"kicad-cli netlist export produced no file for {root_sch}")
        return _parse_components(out)


def _hints_from_catalog(
    instance_cells: dict[str, str], catalog: Catalog, placement: PlacementSpec | None
) -> PlacementHints:
    port_dirs: dict[str, dict[str, str]] = {}
    connectors: set[str] = set()
    used_cells = set(instance_cells.values())
    for cell_key in used_cells:
        cell = catalog.cells.get(cell_key)
        if cell is None:
            continue
        port_dirs[cell_key] = {p: spec.get("direction", "") for p, spec in cell.ports.items()}
    # connectors: instances whose cell claims the 'connectivity' function.
    for inst, cell_key in instance_cells.items():
        cell = catalog.cells.get(cell_key)
        if cell is not None and "connectivity" in cell.functions:
            connectors.add(inst)
    board = None
    edges: dict[str, str] = {}
    if placement is not None:
        if placement.board is not None:
            board = (placement.board.width_mm, placement.board.height_mm)
        edges = dict(placement.edges)
    return PlacementHints(
        board=board, edges=edges, port_dirs=port_dirs, connectors=frozenset(connectors)
    )


def build_floorplan(
    design_dir: str | Path,
    catalog: Catalog,
    placement: PlacementSpec | None = None,
) -> Floorplan:
    """Assemble the pure floorplanner's inputs from *design_dir* and run it.

    Reads ``wiring_plan.json`` (instances + nets) and exports the root
    schematic's netlist (refs + footprints), builds :class:`PlacementHints` from
    the catalog + *placement*, and returns the computed :class:`Floorplan`.
    """
    design_dir = Path(design_dir)
    plan_json = design_dir / "wiring_plan.json"
    if not plan_json.is_file():
        raise BoardEmitError(
            f"{plan_json} not found — run `infersynth synthesize` (wiring on) first"
        )
    data = json.loads(plan_json.read_text())
    instance_cells: dict[str, str] = dict(data["instances"])
    instances = list(instance_cells)  # instantiation order == century order
    nets = tuple(
        NetLite(kind=n["kind"], name=n["name"], members=tuple((m[0], m[1]) for m in n["members"]))
        for n in data["nets"]
    )

    roots = sorted(design_dir.glob("*.kicad_sch"))
    root_sch = _pick_root(design_dir, instances, roots)
    components = _export_components(root_sch)

    hints = _hints_from_catalog(instance_cells, catalog, placement)
    return floorplan(instances, instance_cells, nets, components, hints)


def _pick_root(design_dir: Path, instances: list[str], roots: list[Path]) -> Path:
    """The root schematic is the one that is NOT a child instance sheet."""
    child_names = {f"{i}.kicad_sch" for i in instances}
    candidates = [r for r in roots if r.name not in child_names]
    if not candidates:
        raise BoardEmitError(f"no root schematic found in {design_dir}")
    return candidates[0]


def emit_board(
    design_dir: str | Path,
    out_path: str | Path,
    catalog: Catalog,
    placement: PlacementSpec | None = None,
    *,
    pcbnew_python: str | None = None,
    fp_dir: str | None = None,
) -> EmitResult:
    """Floorplan *design_dir* and write a grouped, unrouted ``.kicad_pcb``.

    Runs :func:`build_floorplan`, checks the no-overlap invariant, then emits the
    board via the pcbnew subprocess. Returns an :class:`EmitResult`.
    """
    out_path = Path(out_path)
    plan = build_floorplan(design_dir, catalog, placement)
    overlaps = check_no_overlap(plan)
    if overlaps:  # invariant breach — refuse to emit a bad board
        raise BoardEmitError(
            f"floorplan has {len(overlaps)} overlapping tile pair(s): {overlaps[:3]}"
        )

    groups: dict[str, list[str]] = {}
    footprints: list[dict[str, object]] = []
    for cluster in plan.clusters:
        refs = [fp.ref for fp in cluster.footprints]
        if refs:
            groups[cluster.instance] = refs
        for fp in cluster.footprints:
            footprints.append(
                {"ref": fp.ref, "value": fp.value, "libid": fp.footprint,
                 "x": fp.x_mm, "y": fp.y_mm}
            )

    payload = {
        "out": str(out_path),
        "fp_dir": fp_dir or os.environ.get("INFERSYNTH_FP_DIR", _DEFAULT_FP_DIR),
        "board": {"w": plan.board_w_mm, "h": plan.board_h_mm},
        "footprints": footprints,
        "groups": groups,
    }

    python = (
        pcbnew_python
        or os.environ.get("INFERSYNTH_PCBNEW_PYTHON")
        or _DEFAULT_PCBNEW_PYTHON
    )
    if not Path(python).exists():
        raise BoardEmitError(
            f"pcbnew python not found: {python} "
            "(set $INFERSYNTH_PCBNEW_PYTHON to a python with pcbnew)"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        payload_path = fh.name
    try:
        proc = subprocess.run(
            [python, str(_EMIT_SCRIPT), payload_path],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(payload_path)
    if not out_path.is_file():
        raise BoardEmitError(
            f"pcbnew emit produced no board (rc={proc.returncode}):\n"
            f"{proc.stderr.strip()[-2000:] or proc.stdout.strip()[-2000:]}"
        )
    fp_count = sum(len(v) for v in groups.values())
    return EmitResult(
        board_path=out_path,
        plan=plan,
        footprint_count=fp_count,
        group_count=len(groups),
        overlaps=overlaps,
    )
