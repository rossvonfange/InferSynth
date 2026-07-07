"""The recognizer: orchestrate STEPS 1-4 into a :class:`RecognitionResult`.

Given a :class:`~infersynth.recognize.netlist.DesignNetlist` and a loaded
:class:`~infersynth.catalog.Catalog`:

1. build the MPN reverse index (:mod:`.index`);
2. anchor on each MPN-bearing design component — *specific* MPNs (few candidate
   cells: the ICs) first, generic passives last — and, for each candidate cell,
   attempt an anchored subgraph match (:mod:`.match`);
3. for the winning match, invert the cell's bindings to recover params
   (:mod:`.invert`);
4. components claimed by no recognized instance are the residual.

Determinism (SELECTION.md §8): anchors are processed in a stable
``(specificity, ref)`` order; among an anchor's successful candidate-cell
matches the *best explanation* wins — largest claimed-component set, ties
broken by lexicographic cell key. Claimed components are never re-used, so two
runs are byte-identical.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from infersynth.catalog import Catalog
from infersynth.recognize.index import build_reverse_index
from infersynth.recognize.invert import InversionResult, invert_params
from infersynth.recognize.match import CellMatch, GoldenGraph, load_golden_graph, match_cell
from infersynth.recognize.netlist import DesignNetlist

__all__ = ["RecognizedInstance", "RecognitionResult", "recognize"]

SCHEMA = "infersynth.recognize/v0"


@dataclass(frozen=True)
class RecognizedInstance:
    """One recognized cell instance."""

    cell_key: str
    anchor_ref: str
    match: CellMatch
    inversion: InversionResult

    @property
    def design_refs(self) -> tuple[str, ...]:
        return self.match.design_refs

    def to_dict(self) -> dict[str, Any]:
        inv = self.inversion
        return {
            "cell_key": self.cell_key,
            "anchor_ref": self.anchor_ref,
            "design_refs": list(self.design_refs),
            "phi": {g: d for g, d in sorted(self.match.phi.items())},
            "params": {k: inv.params[k] for k in sorted(inv.params)},
            "param_method": {k: inv.method[k] for k in sorted(inv.method)},
            "assumed_default": list(inv.assumed_default),
            "unresolved_params": list(inv.unresolved),
            "residual": inv.residual,
            "confidence": inv.confidence,
        }


@dataclass(frozen=True)
class RecognitionResult:
    """The full result: recognized instances + residual components."""

    instances: tuple[RecognizedInstance, ...] = ()
    residual: tuple[str, ...] = ()
    design: DesignNetlist | None = None
    unrecognizable_cells: tuple[str, ...] = ()  # catalog cells with no golden graph

    @property
    def recognized_cell_keys(self) -> tuple[str, ...]:
        return tuple(sorted({i.cell_key for i in self.instances}))

    def to_dict(self) -> dict[str, Any]:
        residual_comps = []
        if self.design is not None:
            for ref in self.residual:
                c = self.design.components[ref]
                residual_comps.append(
                    {
                        "ref": c.ref,
                        "class": c.cls,
                        "value": c.value_str,
                        "mpn": c.mpn,
                        "footprint": c.footprint,
                    }
                )
        else:
            residual_comps = [{"ref": r} for r in self.residual]
        return {
            "schema": SCHEMA,
            "summary": {
                "instances_recognized": len(self.instances),
                "distinct_cells": len(self.recognized_cell_keys),
                "residual_components": len(self.residual),
            },
            "recognized_cell_keys": list(self.recognized_cell_keys),
            "instances": [i.to_dict() for i in self.instances],
            "residual": residual_comps,
            "unrecognizable_cells": list(self.unrecognizable_cells),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        lines = [
            "# Recognition report",
            "",
            f"- schema: `{SCHEMA}`",
            f"- cell instances recognized: **{len(self.instances)}** "
            f"({len(self.recognized_cell_keys)} distinct cell(s))",
            f"- residual components: **{len(self.residual)}**",
            "",
        ]
        if self.instances:
            lines += ["## Recognized cell instances", ""]
            for inst in self.instances:
                inv = inst.inversion
                params = ", ".join(
                    f"{k}={_fmt_num(inv.params[k])}" for k in sorted(inv.params)
                )
                lines.append(
                    f"- **{inst.cell_key}** @ `{inst.anchor_ref}` "
                    f"→ refs {list(inst.design_refs)}"
                )
                if params:
                    lines.append(f"  - params: {params}")
                lines.append(
                    f"  - residual={_fmt_num(inv.residual)}, "
                    f"confidence={inv.confidence:.4f}"
                )
                if inv.assumed_default:
                    lines.append(f"  - assumed default(s): {list(inv.assumed_default)}")
                if inv.unresolved:
                    lines.append(f"  - UNRESOLVED: {list(inv.unresolved)}")
            lines.append("")
        if self.residual and self.design is not None:
            lines += ["## Residual components (capture-as-cell / declared glue)", ""]
            for ref in self.residual:
                c = self.design.components[ref]
                mpn = f" mpn={c.mpn}" if c.mpn else ""
                lines.append(f"- `{c.ref}` ({c.cls}) value={c.value_str}{mpn}")
            lines.append("")
        return "\n".join(lines)


def _fmt_num(x: float) -> str:
    if x != x or x in (float("inf"), float("-inf")):
        return str(x)
    if abs(x - round(x)) < 1e-9 and abs(x) < 1e15:
        return str(int(round(x)))
    return f"{x:.6g}"


def _portname_agreement(match: CellMatch, golden: GoldenGraph, design: DesignNetlist) -> int:
    """How many golden *port* nets map to a design net whose (sheet-stripped)
    name equals the golden port name.

    A disambiguator, NOT a requirement: two cells can be structurally
    isomorphic yet differ only in which external node is the signal input vs
    ground (e.g. inverting vs non-inverting gain stages share an identical
    resistor topology). When net names are meaningful — as in InferSynth
    designs and most vendor netlists, where power/ground/IO nets are named —
    this agreement count breaks the tie toward the cell whose port semantics
    actually line up. When names are opaque net codes it contributes nothing
    and matching falls back to structure + cell-key order alone.
    """
    score = 0
    for gnet, dnet in match.net_map.items():
        gname = gnet.lstrip("/")
        if gname in golden.ports:
            dname = dnet.rsplit("/", 1)[-1]
            if dname == gname:
                score += 1
    return score


def _observed(match: CellMatch, design: DesignNetlist) -> dict[str, float]:
    """Golden ref -> observed numeric value, for refs whose design component
    carries an invertible value."""
    out: dict[str, float] = {}
    for gref, dref in match.phi.items():
        comp = design.components.get(dref)
        if comp is not None and comp.value is not None:
            out[gref] = comp.value
    return out


def recognize(design: DesignNetlist, catalog: Catalog) -> RecognitionResult:
    """Recognize catalog cells in *design*. Pure and deterministic."""
    index = build_reverse_index(catalog)

    # Preload golden graphs; note cells that cannot be recognized (no golden).
    graphs: dict[str, GoldenGraph] = {}
    unrecognizable: list[str] = []
    for key in sorted(catalog.cells):
        g = load_golden_graph(catalog.cells[key])
        if g is None:
            unrecognizable.append(key)
        else:
            graphs[key] = g

    # Anchor order: specific MPNs first (ICs), generic passives last; ref tie.
    anchors = [
        c.ref
        for c in design.components.values()
        if c.mpn and index.specificity(c.mpn) > 0
    ]
    anchors.sort(key=lambda ref: (index.specificity(design.components[ref].mpn), ref))

    claimed: set[str] = set()
    instances: list[RecognizedInstance] = []

    for anchor_ref in anchors:
        if anchor_ref in claimed:
            continue
        mpn = design.components[anchor_ref].mpn
        # rank key: largest claimed set, then best port-name agreement, then
        # lexicographic cell key — all deterministic.
        best: tuple[tuple[int, int, str], str, CellMatch] | None = None
        for cell_key in index.candidates(mpn):
            golden = graphs.get(cell_key)
            if golden is None:
                continue
            seed_refs = [
                r for r in index.anchor_refs.get((mpn, cell_key), ()) if r in golden.refs
            ]
            for seed_golden in seed_refs:
                m = match_cell(
                    catalog.cells[cell_key], golden, design, seed_golden, anchor_ref
                )
                if m is None:
                    continue
                if not claimed.isdisjoint(m.design_refs):
                    continue
                rank = (-len(m.design_refs), -_portname_agreement(m, golden, design), cell_key)
                cand = (rank, cell_key, m)
                if best is None or rank < best[0]:
                    best = cand
                break  # first seed that matches this cell suffices
        if best is None:
            continue
        _, cell_key, match = best
        cell = catalog.cells[cell_key]
        inversion = invert_params(cell, _observed(match, design))
        instances.append(
            RecognizedInstance(
                cell_key=cell_key,
                anchor_ref=anchor_ref,
                match=match,
                inversion=inversion,
            )
        )
        claimed.update(match.design_refs)

    instances.sort(key=lambda i: (i.anchor_ref, i.cell_key))
    residual = tuple(sorted(r for r in design.components if r not in claimed))
    return RecognitionResult(
        instances=tuple(instances),
        residual=residual,
        design=design,
        unrecognizable_cells=tuple(unrecognizable),
    )
