"""Auto-floorplan + placed-board emission (docs/FLOORPLAN.md).

``plan`` is the pure, deterministic floorplanner (flow order + edge bands +
non-overlapping cluster packing); ``board`` is the impure shell that gathers a
synthesized design's structure and emits a grouped, UNROUTED ``.kicad_pcb`` via
pcbnew. KiCad (pcbnew) IS the floorplanner from here on — this stage only gives
the user a placed starting point to route.
"""

from __future__ import annotations

from infersynth.floorplan.plan import (
    ClusterPlacement,
    ComponentLite,
    Floorplan,
    FootprintPlacement,
    NetLite,
    PlacementHints,
    check_no_overlap,
    floorplan,
)

__all__ = [
    "ClusterPlacement",
    "ComponentLite",
    "Floorplan",
    "FootprintPlacement",
    "NetLite",
    "PlacementHints",
    "check_no_overlap",
    "floorplan",
    "emit_board",
    "BoardEmitError",
    "build_floorplan",
]


def __getattr__(name: str):  # lazy: board.py imports subprocess/json, keep plan pure-light
    if name in ("emit_board", "BoardEmitError", "build_floorplan"):
        from infersynth.floorplan import board

        return getattr(board, name)
    raise AttributeError(name)
