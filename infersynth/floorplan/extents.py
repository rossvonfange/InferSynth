"""Deterministic footprint-extent estimator (docs/FLOORPLAN.md, mechanism 2).

The pure floorplanner reasons over rectangular *tiles*, never real copper. This
module turns a KiCad footprint library id (``Lib:Name``) into a conservative
``(width_mm, height_mm)`` body estimate by keyword — a small, closed table plus
a generous default. It is DELIBERATELY an over-estimate: the planner pads each
tile further (:mod:`infersynth.floorplan.plan`), so a tile always dwarfs the
real footprint's courtyard (part body + ~0.25 mm clearance). That is the
documented approximation that lets the no-overlap invariant hold WITHOUT loading
pcbnew — the pure planner stays testable in the plain venv, and the board
emitter's real courtyards land comfortably inside their reserved tiles.

The estimate need not be exact; it only needs to bound the courtyard from above
for the seed catalog's SMD/through-hole parts. When a future refinement wants
true courtyards, the board emitter can measure them via pcbnew and feed real
sizes into the same planner (the ``extents`` argument of
:func:`infersynth.floorplan.plan.floorplan`).
"""

from __future__ import annotations

import re

__all__ = ["estimate_extent", "pin_count"]

# Ordered (substring, (w_mm, h_mm)) table — first match wins, so more specific
# keys precede generic ones (e.g. ``sot-223`` before ``sot-23``).
_TABLE: tuple[tuple[str, tuple[float, float]], ...] = (
    ("0201", (1.0, 0.8)),
    ("0402", (1.6, 1.0)),
    ("0603", (2.2, 1.4)),
    ("0805", (2.6, 1.8)),
    ("1206", (4.0, 2.2)),
    ("1210", (4.0, 3.0)),
    ("2512", (7.0, 3.5)),
    ("sot-23-6", (3.2, 3.2)),
    ("sot-23-5", (3.2, 3.2)),
    ("sot-23", (3.2, 3.2)),
    ("sot-223", (7.0, 7.0)),
    ("sot-89", (5.0, 5.0)),
    ("sod-123", (4.0, 2.2)),
    ("sod-323", (3.0, 1.8)),
    ("sod-523", (2.2, 1.4)),
    ("dpak", (10.0, 10.0)),
    ("to-252", (10.0, 10.0)),
    ("to-263", (12.0, 12.0)),
    ("soic-8", (6.0, 5.5)),
    ("soic-14", (9.0, 6.5)),
    ("soic-16", (10.5, 6.5)),
    ("soic", (8.0, 6.0)),
    ("tssop", (6.5, 5.0)),
    ("msop", (4.0, 4.0)),
    ("qfn", (6.0, 6.0)),
    ("lqfp", (12.0, 12.0)),
    ("tqfp", (12.0, 12.0)),
)

# The nominal pin pitch for the connector height heuristic (2.54 mm headers are
# by far the common seed case; a smaller pitch just over-reserves, which is safe).
_CONN_PITCH_MM = 2.6
_CONN_WIDTH_MM = 6.0
_DEFAULT = (5.0, 5.0)

_NxM_RE = re.compile(r"(\d+)x(\d+)")
_1xN_RE = re.compile(r"[_-](\d{1,3})x(\d{1,3})[_-]")


def pin_count(footprint_name: str) -> int | None:
    """Best-effort pin count from a connector footprint name (``..._1x04_...``)."""
    m = _NxM_RE.search(footprint_name)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return None


def estimate_extent(footprint: str) -> tuple[float, float]:
    """Estimate a footprint's body ``(width_mm, height_mm)`` from its lib id.

    *footprint* is a KiCad ``Lib:Name`` string (or a bare name). The result is a
    conservative over-estimate of the part body (see the module docstring).
    """
    name = footprint.split(":", 1)[-1].lower()
    for key, size in _TABLE:
        if key in name:
            return size
    if any(k in name for k in ("pinheader", "connector", "conn_", "terminal", "screwterminal")):
        pins = pin_count(name) or 2
        rows = 2 if "2x" in name else 1
        cols = max(1, (pins + rows - 1) // rows)
        return (_CONN_WIDTH_MM * rows, max(_CONN_WIDTH_MM, cols * _CONN_PITCH_MM))
    return _DEFAULT
