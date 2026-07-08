"""Promote a segment to a subsystem cell — the foundry's "learn a new cell".

The subsystem-cell tier (:mod:`infersynth.recognize.subsystem`) recognizes an
interface-bounded segment by **interface + composition**. This module is the
*inverse authoring step*: given a segment the hierarchical recognizer could not
recognize (a promoted :class:`~infersynth.recognize.hierarchical.CandidateCell`),
synthesize a **review-ready** ``subsystem_cell.yaml`` that captures the segment's
observed shape as an interface+composition rule — so the segment promoted on
board 1 becomes a subsystem cell *recognized* on board 2. This is what makes the
catalog grow from every vendor board (docs/HIERARCHICAL_RECOGNITION.md,
"Subsystem-cell tier": vendor boards become a cell foundry, not one-off
recoveries).

Inference rules (all deterministic, SELECTION.md §8 — no clocks, no randomness):

* **interface** — ``kind`` is the segment's ``interface_kind``; the observed
  width is the number of distinct interface nets of that kind crossing the
  boundary. The stored ``[min_width, max_width]`` is a **band widened around**
  the observed width (:data:`WIDTH_FRAC` / :data:`WIDTH_MIN_TOL`) so the next
  board's slightly-wider/narrower bus (a 30-bit vs 32-bit DDR data bus, a 3-LED
  vs 4-LED bank) still matches — a rigid ``[observed, observed]`` would recognize
  only its own origin board.
* **composition** — one rule per refdes *class* present in the segment (``U``,
  ``R``, ``C`` …), counted by :func:`~infersynth.recognize.netlist.ref_class`.
  Each rule's ``count:[lo, hi]`` is a band widened around the observed count
  (:data:`COUNT_FRAC` / :data:`COUNT_MIN_TOL`) so instance-count variation
  (4 vs 8 devices on the same bus) matches. Each class gets a plausible ``role``
  from the interface kind (an ``R`` on an ``i2c`` boundary is a ``pullup``; on a
  ``led`` boundary a series limiter; on a ``diff_pair`` a termination).
* **anchor** — ``mpn_patterns`` are the segment's members' non-empty MPNs (the
  device class first). Emitted only when the board actually carries MPNs; a
  ``.brd`` import with no MPN yields ``anchor: null`` (adding a pattern no member
  satisfies would make the cell fail to recognize its own origin).
* **name** — a deterministic slug from the PDF block ``label`` (if the segment
  was labeled) else ``interface_kind`` + a per-kind noun (``ddr-interface``,
  ``gpio-bank``, ``sdio-slot``, ``display-header`` …).
* **verification.golden_ref_cell** — ``null`` (a purely-structural subsystem;
  the electrical-core inversion is a later, human-reviewed refinement).

Every generated cell is stamped ``provenance: {generated_by: "promote/v0",
needs_review: true, observed: {...}}`` — it is a *provisional* stub a human must
review before trusting (docs/HIERARCHICAL_RECOGNITION.md: a promoted candidate
is provisional until verified against its golden partition).

**Round-trip guarantee.** The generated cell, loaded via
:func:`~infersynth.recognize.subsystem.load_subsystem_cell` and run through
:func:`~infersynth.recognize.subsystem.match_subsystem` against the ORIGINAL
segment, MUST match — a promoted cell recognizes its own origin. The bands
always straddle the observed value and the MPN patterns are drawn from actual
members, so the round-trip holds by construction (asserted in the tests).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infersynth.recognize.netlist import ref_class

if TYPE_CHECKING:
    from infersynth.catalog import Catalog
    from infersynth.recognize.hierarchical import CandidateCell, HierarchicalResult
    from infersynth.recognize.netlist import DesignNetlist
    from infersynth.recognize.segment import Segment

__all__ = [
    "SCHEMA",
    "WIDTH_FRAC",
    "WIDTH_MIN_TOL",
    "COUNT_FRAC",
    "COUNT_MIN_TOL",
    "candidate_to_subsystem_cell",
    "promote_result",
    "shape_key",
]

SCHEMA = "infersynth.recognize.promote/v0"

#: Fractional widening of the interface-width band: the half-width tolerance is
#: ``max(WIDTH_MIN_TOL, ceil(observed * WIDTH_FRAC))``. A 25% band lets a 32-bit
#: bus recognize a 24..40-bit sibling; a floor of 1 keeps a 2-net diff pair
#: recognizing a 1..3-net one.
WIDTH_FRAC = 0.25
WIDTH_MIN_TOL = 1

#: Fractional widening of each composition count band. Instance counts vary more
#: than bus widths (a 4-LED vs 8-LED bank), so a wider 50% band with a floor of 1.
COUNT_FRAC = 0.5
COUNT_MIN_TOL = 1

# Per-kind noun completing an unlabeled cell name (ddr -> "ddr-interface").
_KIND_NOUN = {
    "ddr": "interface",
    "gpio": "bank",
    "sdio": "slot",
    "sd": "slot",
    "display": "header",
    "led": "bank",
    "i2c": "bus",
    "spi": "bus",
    "uart": "link",
    "diff_pair": "link",
    "bus": "bank",
    "power": "tree",
}

# Role hint per (interface kind -> refdes class). Falls back to _ROLE_BY_CLASS.
_ROLE_BY_KIND: dict[str, dict[str, str]] = {
    "i2c": {"R": "pullup", "U": "device", "C": "decoupling"},
    "spi": {"R": "pullup", "U": "device"},
    "led": {"R": "series", "LED": "indicator", "D": "indicator", "U": "driver"},
    "diff_pair": {"R": "termination", "C": "ac_couple", "U": "device"},
    "ddr": {"R": "termination", "C": "decoupling", "U": "memory"},
    "gpio": {"R": "series", "U": "device", "SW": "switch", "D": "protection"},
    "sdio": {"R": "pullup", "C": "decoupling", "U": "device", "J": "socket"},
    "display": {"R": "series", "U": "driver", "J": "header"},
}

# Generic role hint per refdes class (interface-kind agnostic fallback).
_ROLE_BY_CLASS = {
    "U": "device",
    "R": "passive",
    "C": "decoupling",
    "L": "filter",
    "FB": "ferrite",
    "D": "diode",
    "LED": "indicator",
    "Q": "transistor",
    "Y": "crystal",
    "X": "crystal",
    "J": "connector",
    "P": "connector",
    "SW": "switch",
    "T": "transformer",
}


def _name_slug(text: str) -> str:
    """A deterministic hyphen slug for a cell *name* (lowercase, alnum runs)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _param_slug(text: str) -> str:
    """A deterministic underscore slug for a param identifier."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _band(observed: int, *, frac: float, min_tol: int, floor: int) -> tuple[int, int]:
    """A widened ``[lo, hi]`` band that always straddles *observed* (so a cell
    recognizes its own origin) yet admits nearby values (so a sibling board's
    slightly-different width/count still matches). ``tol = max(min_tol,
    ceil(observed*frac))``; ``lo = max(floor, observed - tol)``; ``hi =
    observed + tol``. Pure integer arithmetic — deterministic."""
    tol = max(min_tol, math.ceil(observed * frac))
    return max(floor, observed - tol), observed + tol


def _observed_width(segment: Segment, kind: str) -> int:
    """Distinct interface nets of *kind* crossing the boundary — the same
    quantity :func:`infersynth.recognize.subsystem._boundary_width` tests."""
    return len({b.net for b in segment.boundary if b.interface_kind == kind})


def _member_counts(segment: Segment) -> dict[str, int]:
    return dict(Counter(ref_class(r) for r in segment.component_refs))


def _role_for(kind: str, cls: str) -> str:
    return _ROLE_BY_KIND.get(kind, {}).get(cls) or _ROLE_BY_CLASS.get(cls, "member")


def _cell_name(kind: str, label: str | None) -> str:
    if label:
        slug = _name_slug(label)
        if slug:
            return slug
    base = _name_slug(kind) or "subsystem"
    noun = _KIND_NOUN.get(kind, "subsystem")
    return f"{base}-{noun}"


def _anchor_patterns(segment: Segment, design: DesignNetlist) -> tuple[str, ...]:
    """Non-empty member MPNs to anchor the cell — device class (``U``) first,
    then any other member. Drawn from actual members so a member always
    satisfies the pattern (round-trip safe). Empty when the board has no MPNs."""
    devices = sorted(
        design.components[r].mpn
        for r in segment.component_refs
        if r in design.components
        and ref_class(r) == "U"
        and design.components[r].mpn
    )
    if devices:
        return tuple(dict.fromkeys(devices))
    others = sorted(
        design.components[r].mpn
        for r in segment.component_refs
        if r in design.components and design.components[r].mpn
    )
    return tuple(dict.fromkeys(others))


def candidate_to_subsystem_cell(
    candidate: CandidateCell,
    segment: Segment,
    design: DesignNetlist,
    *,
    catalog: Catalog | None = None,
    board_hint: str | None = None,
) -> dict[str, Any]:
    """Synthesize the ``subsystem_cell.yaml`` content for one promoted segment.

    Returns the cell as an ordered ``dict`` (kind/name/interface/composition/
    anchor/params_stuffable/verification/provenance). Pure + deterministic; the
    result loads via :func:`load_subsystem_cell` and re-matches its origin
    segment via :func:`match_subsystem` (the round-trip guarantee).

    Raises :class:`ValueError` when the segment has no ``interface_kind`` — a
    subsystem cell is defined by its interface, so an interface-less cluster
    (a pure-local glue cluster, an FPGA core) cannot become one.
    """
    kind = segment.interface_kind
    if not kind:
        raise ValueError(
            f"segment {segment.id!r} has no interface_kind; an interface-less "
            "cluster cannot be promoted to a subsystem cell"
        )

    width = _observed_width(segment, kind)
    min_w, max_w = _band(width, frac=WIDTH_FRAC, min_tol=WIDTH_MIN_TOL, floor=1)

    counts = _member_counts(segment)
    composition = []
    for cls in sorted(counts):
        lo, hi = _band(counts[cls], frac=COUNT_FRAC, min_tol=COUNT_MIN_TOL, floor=0)
        composition.append(
            {"class": cls, "role": _role_for(kind, cls), "count": [lo, hi]}
        )

    name = _cell_name(kind, segment.label)
    patterns = _anchor_patterns(segment, design)
    anchor: dict[str, Any] | None = (
        {"mpn_patterns": list(patterns)} if patterns else None
    )

    cell: dict[str, Any] = {
        "kind": "subsystem",
        "name": name,
        "description": (
            f"[PROVISIONAL] Auto-promoted {kind} subsystem from segment "
            f"{segment.id}"
            + (f" ({segment.label})" if segment.label else "")
            + " — review before trusting."
        ),
        "interface": {"kind": kind, "min_width": min_w, "max_width": max_w},
        "composition": composition,
        "anchor": anchor,
        "params_stuffable": [f"{_param_slug(kind)}_width"],
        "verification": {"golden_ref_cell": None},
        "provenance": {
            "generated_by": "promote/v0",
            "from_board": board_hint,
            "needs_review": True,
            "observed": {
                "segment_id": segment.id,
                "interface_kind": kind,
                "width": width,
                "member_counts": {k: counts[k] for k in sorted(counts)},
                "n_components": len(segment.component_refs),
                "label": segment.label,
            },
        },
    }
    return cell


def shape_key(cell: dict[str, Any]) -> tuple[Any, ...]:
    """A hashable identity of a generated cell's *shape* (interface band +
    composition rules + anchor patterns) — two candidates with the same shape
    are the same cell and are emitted once. Excludes name/description/provenance
    (which carry per-source detail, not shape)."""
    iface = cell["interface"]
    comp = tuple(
        (r["class"], r["role"], tuple(r["count"])) for r in cell["composition"]
    )
    anchor = cell.get("anchor")
    patterns = tuple(anchor["mpn_patterns"]) if anchor else ()
    ver = cell.get("verification") or {}
    return (
        iface["kind"],
        iface["min_width"],
        iface["max_width"],
        comp,
        patterns,
        ver.get("golden_ref_cell"),
    )


def _dump_yaml(cell: dict[str, Any], sources: list[str]) -> str:
    """Serialize a generated cell to YAML with a provenance header banner."""
    import yaml

    banner = [
        "# [PROVISIONAL — auto-generated by promote/v0, needs_review]",
        f"# Promoted from segment(s): {', '.join(sources)}",
        "# Review the interface band, composition roles/counts, and anchor",
        "# before trusting. See docs/HIERARCHICAL_RECOGNITION.md (Subsystem tier).",
        "",
    ]
    body = yaml.safe_dump(cell, sort_keys=False, default_flow_style=False)
    return "\n".join(banner) + body


def promote_result(
    hresult: HierarchicalResult,
    design: DesignNetlist,
    out_dir: str | Path,
    *,
    catalog: Catalog | None = None,
    board_hint: str | None = None,
) -> list[Path]:
    """Promote every promoted candidate in *hresult* to a ``subsystem_cell.yaml``.

    Writes one ``<out_dir>/<name>/subsystem_cell.yaml`` per distinct cell shape
    (identical shapes are deduplicated — emitted once with all their source
    segments noted) plus a ``PROMOTED.md`` review summary. Candidates with no
    ``interface_kind`` (interface-less glue clusters that cannot be subsystem
    cells) are skipped and reported. Returns the written YAML paths, sorted.

    Pure + deterministic: candidates processed in segment-id order, shapes keyed
    deterministically, name collisions among distinct shapes broken by a stable
    ``-2``/``-3`` suffix.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    seg_by_id = {s.id: s for s in hresult.segmentation.segments}

    # candidate -> generated cell, in segment-id order (deterministic)
    generated: list[tuple[dict[str, Any], str]] = []  # (cell, source segment id)
    skipped: list[tuple[str, str]] = []  # (segment id, reason)
    for cand in sorted(hresult.promoted_candidates, key=lambda c: c.segment_id):
        seg = seg_by_id.get(cand.segment_id)
        if seg is None:  # pragma: no cover - hresult is internally consistent
            skipped.append((cand.segment_id, "segment not found in result"))
            continue
        if not seg.interface_kind:
            skipped.append((cand.segment_id, "no interface_kind (glue cluster)"))
            continue
        cell = candidate_to_subsystem_cell(
            cand, seg, design, catalog=catalog, board_hint=board_hint
        )
        generated.append((cell, seg.id))

    # dedup by shape: group source segment ids under one representative cell
    by_shape: dict[tuple[Any, ...], dict[str, Any]] = {}
    sources_by_shape: dict[tuple[Any, ...], list[str]] = {}
    for cell, src in generated:
        key = shape_key(cell)
        if key not in by_shape:
            by_shape[key] = cell
        sources_by_shape.setdefault(key, []).append(src)

    # stable order: by cell name then shape key
    ordered = sorted(by_shape.items(), key=lambda kv: (kv[1]["name"], kv[0]))

    # resolve name collisions among *distinct* shapes deterministically
    used: dict[str, int] = {}
    written: list[Path] = []
    summary_rows: list[tuple[str, dict[str, Any], list[str]]] = []
    for key, cell in ordered:
        base = cell["name"]
        n = used.get(base, 0) + 1
        used[base] = n
        name = base if n == 1 else f"{base}-{n}"
        cell = {**cell, "name": name}
        sources = sorted(sources_by_shape[key])
        if len(sources) > 1:
            cell["provenance"] = {
                **cell["provenance"],
                "from_segments": sources,
            }
        cell_dir = out / name
        cell_dir.mkdir(parents=True, exist_ok=True)
        path = cell_dir / "subsystem_cell.yaml"
        path.write_text(_dump_yaml(cell, sources))
        written.append(path)
        summary_rows.append((name, cell, sources))

    (out / "PROMOTED.md").write_text(
        _promoted_markdown(summary_rows, skipped, board_hint)
    )
    return sorted(written)


def _promoted_markdown(
    rows: list[tuple[str, dict[str, Any], list[str]]],
    skipped: list[tuple[str, str]],
    board_hint: str | None,
) -> str:
    lines = [
        "# Promoted subsystem cells (PROVISIONAL — review required)",
        "",
        f"- schema: `{SCHEMA}`",
        f"- source board: {board_hint or '(unspecified)'}",
        f"- distinct subsystem cells generated: **{len(rows)}**",
        f"- candidates skipped (no interface_kind): **{len(skipped)}**",
        "",
        "> Every cell below is auto-generated and marked `needs_review: true`. "
        "Review its interface band, composition roles/counts, and anchor before "
        "adding it to a trusted catalog.",
        "",
        "## Generated cells",
        "",
    ]
    for name, cell, sources in rows:
        iface = cell["interface"]
        comp = ", ".join(
            f"{r['count'][0]}-{r['count'][1]}x{r['class']}({r['role']})"
            for r in cell["composition"]
        )
        anchor = cell.get("anchor")
        anchor_note = (
            f" anchor={anchor['mpn_patterns']}" if anchor else " anchor=null"
        )
        src_note = (
            f" from {len(sources)} segments {sources}"
            if len(sources) > 1
            else f" from {sources[0]}"
        )
        lines.append(
            f"- **{name}** [`{iface['kind']}` "
            f"w{iface['min_width']}-{iface['max_width']}] "
            f"{{{comp}}}{anchor_note}{src_note}"
        )
    lines.append("")
    if skipped:
        lines += ["## Skipped candidates (not promotable to subsystem cells)", ""]
        for seg_id, reason in sorted(skipped):
            lines.append(f"- `{seg_id}` — {reason}")
        lines.append("")
    return "\n".join(lines)
