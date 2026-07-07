"""STEP 1 — MPN-anchored candidates: the reverse index.

The forward direction (:mod:`infersynth.bind.parts`) is *cell -> parts*: a
cell's ``selection.candidates`` name real MPNs and, via each candidate's
``maps`` set, which fragment refs that part binds. The recognizer inverts it:
*MPN -> cells that use it*, plus, per (MPN, cell), which golden refs that MPN
anchors — the seed refs for subgraph matching.

Building this index IS the "MPN binding work built the recognition index as a
side effect" of LOOM.md Pillar 2. It is pure and deterministic: cells are read
in sorted-key order; every returned collection is sorted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.catalog import Catalog

__all__ = ["ReverseIndex", "build_reverse_index"]


@dataclass(frozen=True)
class ReverseIndex:
    """MPN -> candidate cells, and (MPN, cell_key) -> anchor golden refs.

    ``cells_for_mpn`` maps an MPN to the sorted cell keys whose
    ``selection.candidates`` list it. ``anchor_refs`` maps ``(mpn, cell_key)``
    to the sorted golden refs that MPN's candidate ``maps`` — the refs to seed
    an anchored subgraph match on.
    """

    cells_for_mpn: dict[str, tuple[str, ...]] = field(default_factory=dict)
    anchor_refs: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)

    def candidates(self, mpn: str) -> tuple[str, ...]:
        """Sorted candidate cell keys for *mpn* (empty tuple if unknown)."""
        return self.cells_for_mpn.get(mpn, ())

    def specificity(self, mpn: str) -> int:
        """How many cells claim *mpn* — lower is a more specific anchor.

        A dedicated op-amp MPN maps to few cells (specific, a good anchor); a
        generic 0603 resistor MPN maps to many (weak — anchored only after the
        specific parts have claimed their neighborhoods)."""
        return len(self.cells_for_mpn.get(mpn, ()))


def build_reverse_index(catalog: Catalog) -> ReverseIndex:
    """Build the MPN reverse index from every cell's ``selection.candidates``."""
    cells_for: dict[str, set[str]] = {}
    anchor: dict[tuple[str, str], set[str]] = {}
    for key in sorted(catalog.cells):
        cell = catalog.cells[key]
        for cand in (cell.selection or {}).get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            mpn = str(cand.get("mpn", "")).strip()
            if not mpn:
                continue
            cells_for.setdefault(mpn, set()).add(key)
            maps = cand.get("maps") or {}
            if isinstance(maps, dict):
                refs = {r for r, on in maps.items() if on and not str(r).startswith("#")}
                if refs:
                    anchor.setdefault((mpn, key), set()).update(refs)
    return ReverseIndex(
        cells_for_mpn={m: tuple(sorted(v)) for m, v in sorted(cells_for.items())},
        anchor_refs={k: tuple(sorted(v)) for k, v in sorted(anchor.items())},
    )
