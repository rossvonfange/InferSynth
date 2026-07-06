"""Catalog: a directory of cell packages with dataset-level checks.

Enforces unique ``name@version`` and the idiom-collision check (DESIGN.md
section 5, entry gate 4): no two entries may claim the same idiom keyword
with overlapping parameter ranges unless an explicit disambiguation rule
is declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infersynth.catalog.loader import CellPackage, CellPackageError, load_cell

__all__ = ["Catalog", "CatalogError", "IdiomCollision"]


class CatalogError(ValueError):
    """Raised when a catalog directory fails validation."""

    def __init__(self, diagnostics: list[str]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__(
            "catalog validation failed with {} error(s):\n{}".format(
                len(diagnostics), "\n".join(f"  - {d}" for d in diagnostics)
            )
        )


@dataclass(frozen=True)
class IdiomCollision:
    """Two entries claiming the same idiom with overlapping parameter ranges."""

    keyword: str
    cell_a: str  # name@version
    cell_b: str
    detail: str

    def __str__(self) -> str:
        return (
            f"idiom collision on keyword {self.keyword!r} between "
            f"{self.cell_a} and {self.cell_b}: {self.detail} "
            "(declare an idioms.disambiguation rule to resolve)"
        )


def _ranges_overlap(a: Any, b: Any) -> bool:
    """Inclusive [min, max] interval overlap; None bounds are unbounded."""
    a_lo, a_hi = a
    b_lo, b_hi = b
    lo = max(x for x in (a_lo, b_lo) if x is not None) if (a_lo, b_lo) != (None, None) else None
    hi = min(x for x in (a_hi, b_hi) if x is not None) if (a_hi, b_hi) != (None, None) else None
    if lo is None or hi is None:
        return True
    return lo <= hi


def _params_overlap(a: CellPackage, b: CellPackage) -> tuple[bool, str]:
    """Do two cells' idiom parameter spaces overlap? Returns (overlap, detail).

    Cells overlap unless at least one *shared* parameter has provably
    disjoint constraints (disjoint ranges, or disjoint allowed sets).
    A cell with no declared idiom params claims the whole space.
    """
    pa, pb = a.idiom_params, b.idiom_params
    shared = sorted(set(pa) & set(pb))
    if not shared:
        return True, "no shared idiom parameters to separate them"
    for pname in shared:
        sa, sb = pa[pname], pb[pname]
        ra, rb = sa.get("range"), sb.get("range")
        if ra is not None and rb is not None and not _ranges_overlap(ra, rb):
            return False, f"parameter {pname!r} ranges are disjoint"
        aa, ab = sa.get("allowed"), sb.get("allowed")
        if aa is not None and ab is not None and not set(aa) & set(ab):
            return False, f"parameter {pname!r} allowed sets are disjoint"
    return True, f"shared parameter(s) {shared} have overlapping ranges"


class Catalog:
    """A loaded catalog: unique cell packages plus dataset-level validation."""

    def __init__(self) -> None:
        self.cells: dict[str, CellPackage] = {}  # key: name@version

    @classmethod
    def load(cls, catalog_dir: str | Path, strict: bool = True) -> Catalog:
        """Load every cell directory under *catalog_dir* (one level deep).

        ``strict`` is forwarded to :func:`load_cell` (unknown cell.yaml
        sections are errors by default). Raises :class:`CatalogError`
        collecting all per-cell and dataset-level diagnostics.
        """
        root = Path(catalog_dir)
        if not root.is_dir():
            raise CatalogError([f"{root}: not a directory"])
        catalog = cls()
        diags: list[str] = []
        for entry in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                cell = load_cell(entry, strict=strict)
            except CellPackageError as exc:
                diags.extend(f"{entry.name}: {d}" for d in exc.diagnostics)
                continue
            try:
                catalog.add(cell)
            except CatalogError as exc:
                diags.extend(exc.diagnostics)
        diags.extend(str(c) for c in catalog.idiom_collisions())
        if diags:
            raise CatalogError(diags)
        return catalog

    def add(self, cell: CellPackage) -> None:
        if cell.key in self.cells:
            raise CatalogError(
                [
                    f"duplicate cell {cell.key}: {self.cells[cell.key].path} "
                    f"and {cell.path}"
                ]
            )
        self.cells[cell.key] = cell

    def idiom_collisions(self) -> list[IdiomCollision]:
        """DESIGN section 5 gate 4: exact-keyword + overlapping-param-range check.

        A collision is waived when either party declares an explicit
        ``idioms.disambiguation`` rule.
        """
        by_keyword: dict[str, list[CellPackage]] = {}
        for key in sorted(self.cells):
            cell = self.cells[key]
            for kw in cell.keywords:
                by_keyword.setdefault(kw, []).append(cell)

        collisions: list[IdiomCollision] = []
        for kw in sorted(by_keyword):
            claimants = by_keyword[kw]
            for i, a in enumerate(claimants):
                for b in claimants[i + 1 :]:
                    if a.name == b.name:
                        continue  # versions of the same cell may share idioms
                    if a.disambiguation or b.disambiguation:
                        continue
                    overlap, detail = _params_overlap(a, b)
                    if overlap:
                        collisions.append(
                            IdiomCollision(keyword=kw, cell_a=a.key, cell_b=b.key, detail=detail)
                        )
        return collisions
