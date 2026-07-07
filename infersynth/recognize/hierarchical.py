"""Hierarchical recognition — segment, then recognize each segment.

The pipeline of docs/HIERARCHICAL_RECOGNITION.md, step 2: run
:func:`~infersynth.recognize.segment.segment` on the design, then for every
:class:`~infersynth.recognize.segment.Segment` run the EXISTING flat recognizer
(:func:`infersynth.recognize.recognizer.recognize`) scoped to a sub-netlist
restricted to that segment's refs+nets — so recognition can actually fire on a
clean, cell-sized cluster instead of a 562-component board. Each segment then
either

* **recognizes** to one or more catalog cell instances, OR
* **promotes** to a candidate new cell — an unrecognized segment is a candidate
  new *subsystem* cell (a DDR interface, an LCD header): we emit a
  :class:`CandidateCell` stub (its refs, its boundary interface, a suggested
  name from the label / interface_kind) — the capture/growth path that turns a
  vendor board's protocol clusters into new library cells.

The true residual (the segmenter's unclustered singletons) is surfaced
verbatim, never silently dropped. Pure + deterministic (SELECTION.md §8): the
segmenter is deterministic, the recognizer is deterministic, and segments are
processed in id order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from infersynth.catalog import Catalog
from infersynth.recognize.netlist import DesignNetlist
from infersynth.recognize.recognizer import RecognitionResult, recognize
from infersynth.recognize.segment import (
    LabelClaim,
    Segment,
    SegmentationResult,
    segment,
)
from infersynth.recognize.subsystem import SubsystemMatch, match_subsystems

__all__ = [
    "SCHEMA",
    "CandidateCell",
    "SegmentRecognition",
    "HierarchicalResult",
    "restrict_netlist",
    "hierarchical_recognize",
]

SCHEMA = "infersynth.recognize.hierarchical/v0"


def restrict_netlist(design: DesignNetlist, refs: tuple[str, ...] | list[str]) -> DesignNetlist:
    """Build the sub-:class:`DesignNetlist` seen by one segment: only *refs*'
    components, and every net filtered to just those components' pins (a net
    that also leaves the segment appears here carrying only its in-segment
    pins). Deterministic: components sorted, each net's pins sorted."""
    keep = set(refs)
    components = {r: c for r, c in design.components.items() if r in keep}
    nets: dict[str, list[tuple[str, str]]] = {}
    for name, pins in design.nets.items():
        inside = sorted((r, p) for r, p in pins if r in keep)
        if inside:
            nets[name] = inside
    nets = {name: nets[name] for name in sorted(nets)}
    pin_net = {(r, p): name for name, pins in nets.items() for r, p in pins}
    return DesignNetlist(
        components=dict(sorted(components.items())), nets=nets, pin_net=pin_net
    )


@dataclass(frozen=True)
class CandidateCell:
    """A promotable candidate new subsystem cell from an unrecognized segment."""

    suggested_name: str
    segment_id: str
    component_refs: tuple[str, ...]
    interface_kind: str | None
    label: str | None
    boundary_kinds: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggested_name": self.suggested_name,
            "segment_id": self.segment_id,
            "component_refs": list(self.component_refs),
            "interface_kind": self.interface_kind,
            "label": self.label,
            "boundary_kinds": list(self.boundary_kinds),
            "provisional": True,
        }


@dataclass(frozen=True)
class SegmentRecognition:
    """One segment's outcome: a small-cell recognition, a subsystem-cell match,
    and/or a promoted candidate. At most one of ``recognition.instances`` /
    ``subsystem_match`` / ``candidate`` carries the outcome — small cell first,
    then subsystem cell, else promote (see :func:`hierarchical_recognize`)."""

    segment: Segment
    recognition: RecognitionResult
    candidate: CandidateCell | None = None
    subsystem_match: SubsystemMatch | None = None

    @property
    def recognized_small(self) -> bool:
        """Recognized by the flat small-cell recognizer (exact subgraph)."""
        return bool(self.recognition.instances)

    @property
    def recognized_subsystem(self) -> bool:
        """Recognized as a subsystem cell (interface + composition)."""
        return self.subsystem_match is not None

    @property
    def recognized(self) -> bool:
        """Recognized at either tier (small cell or subsystem cell)."""
        return self.recognized_small or self.recognized_subsystem


@dataclass(frozen=True)
class HierarchicalResult:
    """The full hierarchical result: per-segment outcomes + true residual."""

    segmentation: SegmentationResult
    per_segment: tuple[SegmentRecognition, ...] = ()
    residual: tuple[str, ...] = ()

    @property
    def recognized_segments(self) -> tuple[SegmentRecognition, ...]:
        """Every segment recognized at either tier (small cell or subsystem)."""
        return tuple(s for s in self.per_segment if s.recognized)

    @property
    def recognized_small(self) -> tuple[SegmentRecognition, ...]:
        """Segments recognized by the flat small-cell recognizer."""
        return tuple(s for s in self.per_segment if s.recognized_small)

    @property
    def recognized_subsystem(self) -> tuple[SegmentRecognition, ...]:
        """Segments recognized as subsystem cells (interface + composition)."""
        return tuple(s for s in self.per_segment if s.recognized_subsystem)

    @property
    def promoted_candidates(self) -> tuple[CandidateCell, ...]:
        return tuple(s.candidate for s in self.per_segment if s.candidate is not None)

    def to_dict(self) -> dict[str, Any]:
        recognized = self.recognized_segments
        return {
            "schema": SCHEMA,
            "summary": {
                "segments": len(self.per_segment),
                "recognized_segments": len(recognized),
                "recognized_small": len(self.recognized_small),
                "recognized_subsystem": len(self.recognized_subsystem),
                "promoted_candidates": len(self.promoted_candidates),
                "residual_components": len(self.residual),
            },
            "recognized_small": [
                {
                    "segment_id": s.segment.id,
                    "cell_keys": list(s.recognition.recognized_cell_keys),
                    "instances": [i.to_dict() for i in s.recognition.instances],
                }
                for s in self.recognized_small
            ],
            "recognized_subsystem": [
                {
                    "segment_id": s.segment.id,
                    "subsystem_cell": s.subsystem_match.cell_name,
                    "match": s.subsystem_match.to_dict(),
                }
                for s in self.recognized_subsystem
            ],
            "promoted": [c.to_dict() for c in self.promoted_candidates],
            "residual": list(self.residual),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        small = self.recognized_small
        subsystem = self.recognized_subsystem
        promoted = self.promoted_candidates
        lines = [
            "# Hierarchical recognition report",
            "",
            f"- schema: `{SCHEMA}`",
            f"- segments: **{len(self.per_segment)}** "
            f"({len(small)} small-cell, {len(subsystem)} subsystem-cell, "
            f"{len(promoted)} promoted candidate(s))",
            f"- true residual components: **{len(self.residual)}**",
            "",
        ]
        if small:
            lines += ["## Recognized small cells", ""]
            for s in small:
                lines.append(
                    f"- **{s.segment.id}** -> {list(s.recognition.recognized_cell_keys)}"
                )
            lines.append("")
        if subsystem:
            lines += ["## Recognized subsystem cells", ""]
            for s in subsystem:
                m = s.subsystem_match
                assert m is not None
                lines.append(
                    f"- **{s.segment.id}** -> {m.cell_name} "
                    f"[{m.interface_kind} w{m.width}] (conf {m.confidence:.2f})"
                )
            lines.append("")
        if promoted:
            lines += ["## Promoted candidate cells (provisional)", ""]
            for c in promoted:
                kind = f" [{c.interface_kind}]" if c.interface_kind else ""
                lines.append(
                    f"- **{c.suggested_name}**{kind} <- {c.segment_id} "
                    f"({len(c.component_refs)} comps)"
                )
            lines.append("")
        return "\n".join(lines)


def _suggest_name(seg: Segment) -> str:
    """A stable suggested cell name for a promoted segment: label > interface
    kind > generic, always suffixed with the segment id for uniqueness."""
    import re

    if seg.label:
        base = re.sub(r"[^a-z0-9]+", "-", seg.label.lower()).strip("-")
    elif seg.interface_kind:
        base = f"{seg.interface_kind}-subsystem"
    else:
        base = "subsystem"
    return f"cand-{base}-{seg.id}"


def hierarchical_recognize(
    design: DesignNetlist,
    catalog: Catalog,
    *,
    labels: list[LabelClaim] | None = None,
) -> HierarchicalResult:
    """Segment *design*, then recognize (or promote) each segment. Deterministic.

    Each segment is tried at two tiers before promotion (the subsystem-cell tier
    closes the foundry loop — a segment promoted on one board is recognized on
    the next): first the flat small-cell recognizer scoped to the segment; if it
    finds nothing, the subsystem-cell matcher (interface + composition) against
    the catalog's ``subsystem_cells``; only if BOTH miss is the segment promoted
    to a provisional candidate cell.
    """
    seg_result = segment(design, catalog, labels=labels)
    subsystem_cells = getattr(catalog, "subsystem_cells", {}) or {}
    per_segment: list[SegmentRecognition] = []
    for seg in seg_result.segments:
        sub = restrict_netlist(design, seg.component_refs)
        rec = recognize(sub, catalog)
        subsystem_match: SubsystemMatch | None = None
        candidate: CandidateCell | None = None
        if not rec.instances:
            subsystem_match = match_subsystems(seg, subsystem_cells, design, catalog=catalog)
        if not rec.instances and subsystem_match is None:
            boundary_kinds = tuple(
                sorted({b.interface_kind for b in seg.boundary if b.interface_kind})
            )
            candidate = CandidateCell(
                suggested_name=_suggest_name(seg),
                segment_id=seg.id,
                component_refs=seg.component_refs,
                interface_kind=seg.interface_kind,
                label=seg.label,
                boundary_kinds=boundary_kinds,
            )
        per_segment.append(
            SegmentRecognition(
                segment=seg,
                recognition=rec,
                candidate=candidate,
                subsystem_match=subsystem_match,
            )
        )
    return HierarchicalResult(
        segmentation=seg_result,
        per_segment=tuple(per_segment),
        residual=seg_result.residual,
    )
