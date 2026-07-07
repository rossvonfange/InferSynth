"""The pure, deterministic auto-floorplanner (docs/FLOORPLAN.md).

Turns a synthesized design's *structure* — instance list, inter-cell nets, and
per-instance footprints — into placed rectangles: one KiCad group per instance
(a *cluster*), ordered by signal flow, pulled to board edges by spec hints, and
packed into the board outline WITHOUT overlapping courtyards. It imports nothing
from KiCad/pcbnew and reads no files; every output is a function of its
arguments (SELECTION §8 determinism), so it is fully unit-testable in the plain
venv. :mod:`infersynth.floorplan.board` is the impure shell that gathers these
arguments from a design dir and emits the ``.kicad_pcb`` via pcbnew.

Algorithm v0 (the three mechanisms of docs/FLOORPLAN.md, mechanism 2):

* **cluster = instance** — every footprint whose refdes century maps to an
  instance (ref ``U101`` ⇒ instance 1; the emit.py century scheme) is one
  cluster, laid out as a simple grid (L1 cell floorplans are the future
  refinement — see the doc).
* **flow order** — signal nets induce a DAG over instances (driver→sink from
  port directions); a longest-path layering gives each connected cluster a
  ``flow_depth``. Clusters left-to-right in that order; flow-disconnected
  clusters sort by name AFTER the connected ones.
* **edge bands** — a spec ``placement.edges`` hint (or the documented defaults:
  rail-source instances west, output-side connectors east) pulls a cluster to a
  board edge; north/south bias its row, west/east bias its column.

Overlap freedom is structural: clusters are row-packed (side-by-side within a
row, rows stacked by the row's tallest cluster) and each footprint sits in its
own padded tile, so no two courtyards can intersect (:func:`check_no_overlap`
verifies it as an invariant, never as a hope).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from infersynth.floorplan.extents import estimate_extent

__all__ = [
    "ComponentLite",
    "NetLite",
    "PlacementHints",
    "FootprintPlacement",
    "ClusterPlacement",
    "Floorplan",
    "floorplan",
    "check_no_overlap",
    "sanitize_ref_prefix",
]

# --- layout constants (mm) ---------------------------------------------------
#: pad added on every side of a footprint body — its reserved tile is
#: body + 2*pad, so real courtyards (body + ~0.25 mm) never touch a neighbour.
TILE_PAD_MM = 1.75
#: gap between adjacent clusters within a row and between rows.
CLUSTER_GAP_MM = 4.0
#: inner margin between the packed content and the board outline.
BOARD_MARGIN_MM = 5.0
#: default board aspect (width : height) target when no spec outline is given.
_DEFAULT_ASPECT = 1.4

_REF_RE = re.compile(r"^([A-Za-z_]+?)(\d+)$")
_BAND_ROW = {"north": 0, "south": 2}  # else 1
_BAND_COL = {"west": 0, "east": 2}  # else 1
_EDGES = ("north", "south", "east", "west")


def sanitize_ref_prefix(req_id: str) -> str:
    """Match emit.py's instname sanitizer prefix: ``SNS-01`` -> ``sns_01``.

    A ``placement.edges`` hint is keyed by requirement id; instances are named
    ``<sanitized-req-id>_<cell-name>``. This recovers the prefix so an edge hint
    resolves to its instance by ``startswith``.
    """
    return re.sub(r"[^A-Za-z0-9_]+", "_", req_id).strip("_").lower()


@dataclass(frozen=True)
class ComponentLite:
    """One placed part from the netlist export: ref, value, footprint lib id."""

    ref: str
    value: str
    footprint: str


@dataclass(frozen=True)
class NetLite:
    """One net for flow inference: ``kind`` (``rail``/``signal``) + members."""

    kind: str
    name: str
    members: tuple[tuple[str, str], ...]  # (instance, port)


@dataclass(frozen=True)
class PlacementHints:
    """Resolved placement inputs (spec ``placement:`` + catalog-derived facts).

    ``board`` is an optional ``(width_mm, height_mm)`` outline; ``edges`` maps an
    instance name to a compass edge; ``port_dirs`` gives each cell's port
    directions (for flow orientation); ``connectors`` is the set of connector
    instance names (for the east/west default bands).
    """

    board: tuple[float, float] | None = None
    edges: Mapping[str, str] = field(default_factory=dict)
    port_dirs: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    connectors: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FootprintPlacement:
    """One footprint placed at ``(x_mm, y_mm)`` (its anchor / tile centre)."""

    ref: str
    value: str
    footprint: str
    instance: str
    x_mm: float
    y_mm: float
    #: reserved tile size (body + 2*TILE_PAD); the no-overlap unit.
    tile_w_mm: float
    tile_h_mm: float


@dataclass(frozen=True)
class ClusterPlacement:
    """One instance's footprints as a placed grid (a future KiCad ``(group)``)."""

    instance: str
    cell_key: str
    band: str  # north|south|east|west|flow
    flow_depth: int
    x_mm: float  # cluster bbox origin (top-left)
    y_mm: float
    w_mm: float
    h_mm: float
    footprints: tuple[FootprintPlacement, ...]


@dataclass(frozen=True)
class Floorplan:
    """The whole placed board: outline + clusters + advisory diagnostics."""

    board_w_mm: float
    board_h_mm: float
    clusters: tuple[ClusterPlacement, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def footprints(self) -> tuple[FootprintPlacement, ...]:
        return tuple(fp for c in self.clusters for fp in c.footprints)


# --- ref-century → instance --------------------------------------------------
def _instance_for_ref(ref: str, instances: Sequence[str]) -> str | None:
    """Map a stamped refdes to its instance via the century scheme (emit.py).

    ``U101`` ⇒ century 1 ⇒ ``instances[0]``; ``C402`` ⇒ century 4 ⇒
    ``instances[3]``. Virtual (``#``) and unparseable refs return ``None``.
    """
    if ref.startswith("#"):
        return None
    m = _REF_RE.match(ref)
    if not m:
        return None
    century = int(m.group(2)) // 100
    if 1 <= century <= len(instances):
        return instances[century - 1]
    return None


# --- flow DAG ----------------------------------------------------------------
def _orient_net(
    members: tuple[tuple[str, str], ...],
    port_dirs: Mapping[str, Mapping[str, str]],
    instance_cells: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    """Split a signal net's members into (sources, sinks) by port direction.

    Priority: an ``out``/``inout`` port drives; else a ``passive`` port drives an
    ``in`` port (connector→sink). Ambiguous nets (no orientable pair) return
    ``([], [])`` — they contribute adjacency but no directed edge.
    """
    def direction(inst: str, port: str) -> str:
        return port_dirs.get(instance_cells.get(inst, ""), {}).get(port, "")

    outs = [i for i, p in members if direction(i, p) in ("out", "inout")]
    ins = [i for i, p in members if direction(i, p) == "in"]
    passives = [i for i, p in members if direction(i, p) == "passive"]
    if outs:
        sinks = [i for i, _ in members if i not in outs]
        return outs, sinks
    if passives and ins:
        return passives, ins
    return [], []


def _flow_depths(
    instances: Sequence[str],
    nets: Sequence[NetLite],
    port_dirs: Mapping[str, Mapping[str, str]],
    instance_cells: Mapping[str, str],
) -> dict[str, int]:
    """Longest-path depth per instance over the signal-flow DAG.

    Instances touched by no signal net get depth ``-1`` (flow-disconnected);
    they sort AFTER connected clusters (see :func:`_sort_key`). A defensive
    cycle guard bounds the relaxation to ``len(edges)`` passes.
    """
    edges: list[tuple[str, str]] = []
    touched: set[str] = set()
    for net in nets:
        if net.kind != "signal":
            continue
        srcs, sinks = _orient_net(net.members, port_dirs, instance_cells)
        for i, _ in net.members:
            touched.add(i)
        for s in srcs:
            for d in sinks:
                if s != d:
                    edges.append((s, d))

    depth = {i: (0 if i in touched else -1) for i in instances}
    for _ in range(len(edges) + 1):
        changed = False
        for s, d in edges:
            if depth.get(s, 0) + 1 > depth.get(d, 0):
                depth[d] = depth[s] + 1
                changed = True
        if not changed:
            break
    return depth


# --- edge bands --------------------------------------------------------------
def _drives_rail(inst: str, nets, port_dirs, instance_cells) -> bool:
    cell = instance_cells.get(inst, "")
    for net in nets:
        if net.kind != "rail":
            continue
        for i, p in net.members:
            if i == inst and port_dirs.get(cell, {}).get(p) == "out":
                return True
    return False


def _has_signal(inst: str, nets) -> bool:
    return any(
        net.kind == "signal" and any(i == inst for i, _ in net.members) for net in nets
    )


def _default_band(inst: str, nets, hints: PlacementHints, instance_cells) -> str:
    """The documented default edge band for an un-hinted instance.

    * **west** — a rail source: an ``out``-direction rail driver (regulator), or
      a connector that lives only on rails (a power-input connector).
    * **east** — an output-side connector: a connector that appears in signal
      nets but drives none (a pure signal sink).
    * **flow** — everything else (the signal chain body).
    """
    is_conn = inst in hints.connectors
    drives_rail = _drives_rail(inst, nets, hints.port_dirs, instance_cells)
    has_signal = _has_signal(inst, nets)
    if drives_rail or (is_conn and not has_signal):
        return "west"
    if is_conn and has_signal:
        # connector on the signal chain; east unless it is itself a driver.
        srcs = set()
        for net in nets:
            if net.kind != "signal":
                continue
            s, _ = _orient_net(net.members, hints.port_dirs, instance_cells)
            srcs.update(s)
        if inst not in srcs:
            return "east"
    return "flow"


def _sort_key(cluster_band: str, depth: int, name: str) -> tuple:
    row = _BAND_ROW.get(cluster_band, 1)
    col = _BAND_COL.get(cluster_band, 1)
    # flow-disconnected (depth -1) must sort AFTER connected clusters; remap to a
    # large sentinel so the natural ascending sort places them last in the band.
    flow_rank = depth if depth >= 0 else 10_000
    return (row, col, flow_rank, name)


# --- cluster grid ------------------------------------------------------------
def _grid_cluster(
    instance: str, cell_key: str, comps: list[ComponentLite]
) -> tuple[list[FootprintPlacement], float, float]:
    """Lay a cluster's footprints in a uniform grid; return (placements, w, h).

    Positions are RELATIVE to the cluster origin (0,0); the packer offsets them.
    Uniform cell = the largest tile in the cluster, so the grid stays regular
    and no tile overruns its cell.
    """
    comps = sorted(comps, key=lambda c: _ref_sort(c.ref))
    tiles = [
        (c, tuple(v + 2 * TILE_PAD_MM for v in estimate_extent(c.footprint)))
        for c in comps
    ]
    cell_w = max((t[1][0] for t in tiles), default=1.0)
    cell_h = max((t[1][1] for t in tiles), default=1.0)
    ncols = max(1, math.ceil(math.sqrt(len(tiles))))
    placements: list[FootprintPlacement] = []
    for idx, (comp, (tw, th)) in enumerate(tiles):
        row, col = divmod(idx, ncols)
        cx = col * cell_w + cell_w / 2
        cy = row * cell_h + cell_h / 2
        placements.append(
            FootprintPlacement(
                ref=comp.ref,
                value=comp.value,
                footprint=comp.footprint,
                instance=instance,
                x_mm=cx,
                y_mm=cy,
                tile_w_mm=tw,
                tile_h_mm=th,
            )
        )
    nrows = math.ceil(len(tiles) / ncols) if tiles else 1
    return placements, ncols * cell_w, nrows * cell_h


def _ref_sort(ref: str) -> tuple[str, int]:
    m = _REF_RE.match(ref)
    if m:
        return (m.group(1), int(m.group(2)))
    return (ref, 0)


# --- assembly ----------------------------------------------------------------
def floorplan(
    instances: Sequence[str],
    instance_cells: Mapping[str, str],
    nets: Sequence[NetLite],
    components: Sequence[ComponentLite],
    hints: PlacementHints | None = None,
) -> Floorplan:
    """Compute the placed :class:`Floorplan` (see module docstring).

    *instances* is the instantiation-order instance list (the century index);
    *instance_cells* maps instance→cell_key; *nets* the wiring plan's nets;
    *components* the netlist export's parts; *hints* the resolved placement
    inputs (:class:`PlacementHints`).
    """
    hints = hints or PlacementHints()
    diagnostics: list[str] = []

    # group components by instance via the refdes century.
    by_instance: dict[str, list[ComponentLite]] = {i: [] for i in instances}
    for comp in components:
        inst = _instance_for_ref(comp.ref, instances)
        if inst is None:
            diagnostics.append(f"unmapped ref {comp.ref!r} (no instance century) — skipped")
            continue
        by_instance[inst].append(comp)

    depths = _flow_depths(instances, nets, hints.port_dirs, instance_cells)

    # resolve edge hints (keyed by requirement id) onto instance names.
    resolved_edges: dict[str, str] = {}
    for key, edge in hints.edges.items():
        if edge not in _EDGES:
            diagnostics.append(f"placement edge {key!r}: unknown edge {edge!r} — ignored")
            continue
        inst = _resolve_edge_instance(key, instances)
        if inst is None:
            diagnostics.append(f"placement edge {key!r}: no matching instance — ignored")
            continue
        resolved_edges[inst] = edge

    # build unplaced clusters (grid + band + depth).
    unplaced: list[tuple[tuple, str, str, str, int, list[FootprintPlacement], float, float]] = []
    for inst in instances:
        cell_key = instance_cells.get(inst, "")
        band = resolved_edges.get(inst) or _default_band(inst, nets, hints, instance_cells)
        depth = depths.get(inst, -1)
        fps, w, h = _grid_cluster(inst, cell_key, by_instance.get(inst, []))
        key = _sort_key(band, depth, inst)
        unplaced.append((key, inst, cell_key, band, depth, fps, w, h))
    unplaced.sort(key=lambda t: t[0])

    board_w, board_h, clusters = _pack(unplaced, hints.board, diagnostics)

    return Floorplan(
        board_w_mm=round(board_w, 4),
        board_h_mm=round(board_h, 4),
        clusters=tuple(clusters),
        diagnostics=tuple(diagnostics),
    )


def _resolve_edge_instance(key: str, instances: Sequence[str]) -> str | None:
    if key in instances:
        return key
    prefix = sanitize_ref_prefix(key)
    for inst in instances:
        if inst == prefix or inst.startswith(prefix + "_"):
            return inst
    return None


def _pack(unplaced, board, diagnostics) -> tuple[float, float, list[ClusterPlacement]]:
    """Row-pack sorted clusters left-to-right into the board width; return the
    final ``(board_w, board_h, clusters)``.
    """
    packed: list[ClusterPlacement] = []
    total_area = sum(w * h for *_head, w, h in unplaced) or 1.0
    max_cluster_w = max((w for *_head, w, _h in unplaced), default=1.0)
    if board is not None:
        board_w = max(board[0], max_cluster_w + 2 * BOARD_MARGIN_MM)
        fixed_h: float | None = board[1]
    else:
        target = math.sqrt(total_area * _DEFAULT_ASPECT) * 1.35
        board_w = max(target, max_cluster_w + 2 * BOARD_MARGIN_MM)
        fixed_h = None

    inner_w = board_w - 2 * BOARD_MARGIN_MM
    cx = BOARD_MARGIN_MM
    cy = BOARD_MARGIN_MM
    row_h = 0.0
    for _key, inst, cell_key, band, depth, fps, w, h in unplaced:
        if cx > BOARD_MARGIN_MM and cx + w > BOARD_MARGIN_MM + inner_w:
            # wrap to a new row.
            cx = BOARD_MARGIN_MM
            cy += row_h + CLUSTER_GAP_MM
            row_h = 0.0
        offset_fps = tuple(
            FootprintPlacement(
                ref=fp.ref,
                value=fp.value,
                footprint=fp.footprint,
                instance=fp.instance,
                x_mm=round(cx + fp.x_mm, 4),
                y_mm=round(cy + fp.y_mm, 4),
                tile_w_mm=fp.tile_w_mm,
                tile_h_mm=fp.tile_h_mm,
            )
            for fp in fps
        )
        packed.append(
            ClusterPlacement(
                instance=inst,
                cell_key=cell_key,
                band=band,
                flow_depth=depth,
                x_mm=round(cx, 4),
                y_mm=round(cy, 4),
                w_mm=round(w, 4),
                h_mm=round(h, 4),
                footprints=offset_fps,
            )
        )
        cx += w + CLUSTER_GAP_MM
        row_h = max(row_h, h)

    content_bottom = cy + row_h + BOARD_MARGIN_MM
    if fixed_h is not None:
        board_h = fixed_h
        if content_bottom > fixed_h:
            diagnostics.append(
                f"content ({content_bottom:.1f} mm) exceeds spec board height "
                f"({fixed_h:.1f} mm); grew outline to fit (no overlap)"
            )
            board_h = content_bottom
    else:
        board_h = content_bottom
    return board_w, board_h, packed


def check_no_overlap(fp: Floorplan) -> list[tuple[str, str]]:
    """Return every pair of footprint refs whose reserved tiles overlap.

    The no-overlap invariant: an empty list. Tiles are axis-aligned rects
    centred on each footprint anchor; a touching edge (shared boundary) is not
    an overlap.
    """
    fps = fp.footprints
    bad: list[tuple[str, str]] = []
    rects = [
        (
            p.ref,
            p.x_mm - p.tile_w_mm / 2,
            p.y_mm - p.tile_h_mm / 2,
            p.x_mm + p.tile_w_mm / 2,
            p.y_mm + p.tile_h_mm / 2,
        )
        for p in fps
    ]
    eps = 1e-6
    for i in range(len(rects)):
        ra = rects[i]
        for j in range(i + 1, len(rects)):
            rb = rects[j]
            if (
                ra[1] < rb[3] - eps
                and rb[1] < ra[3] - eps
                and ra[2] < rb[4] - eps
                and rb[2] < ra[4] - eps
            ):
                bad.append((ra[0], rb[0]))
    return bad
