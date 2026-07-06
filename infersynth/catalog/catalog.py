"""Catalog: a directory of cell packages with dataset-level checks.

Enforces unique ``library/name@version`` and the idiom-collision check
(DESIGN.md section 5, entry gate 4): no two entries may claim the same idiom
keyword with overlapping parameter ranges unless an explicit disambiguation
rule is declared — cross-library, catalog-wide (unchanged by SELECTION.md's
library layout: a collision is a vocabulary clash regardless of which
library each claimant lives in).

Layout (SELECTION.md sec 1): a catalog directory holds either library
directories (KiCad-style grouping, each identified by a ``library.yaml``
manifest) — the two-level layout — or cell directories directly (the old
flat layout, kept for tests/fixtures). Detected by presence of
``library.yaml`` files among the catalog root's immediate subdirectories: if
any subdir has one, *every* subdir is expected to be a library dir; if none
do, the root is treated as flat.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infersynth.catalog.interfaces import InterfaceDef, InterfacesError, load_interfaces
from infersynth.catalog.loader import CellPackage, CellPackageError, load_cell
from infersynth.catalog.taxonomy import TaxonomyError, load_taxonomy

__all__ = ["Catalog", "CatalogError", "IdiomCollision"]

_LIBRARY_KEYS = ("name", "description", "tier", "maintainer")
_LIBRARY_TIERS = ("official", "community", "local")


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


def _is_library_dir(p: Path) -> bool:
    return (p / "library.yaml").is_file()


def _load_library_manifest(lib_dir: Path) -> tuple[str | None, list[str]]:
    """Validate one library's ``library.yaml`` (SELECTION.md sec 1).

    Returns ``(library_name, diagnostics)``; ``library_name`` is ``None`` if
    the manifest is too broken to trust its ``name`` field.
    """
    import yaml

    diags: list[str] = []
    lib_path = lib_dir / "library.yaml"
    try:
        data = yaml.safe_load(lib_path.read_text())
    except yaml.YAMLError as exc:
        return None, [f"{lib_dir.name}/library.yaml: invalid YAML: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{lib_dir.name}/library.yaml: must be a mapping"]
    unknown = sorted(set(data) - set(_LIBRARY_KEYS))
    if unknown:
        diags.append(f"{lib_dir.name}/library.yaml: unknown key(s) {unknown}")
    for key in _LIBRARY_KEYS:
        if not data.get(key):
            diags.append(f"{lib_dir.name}/library.yaml: {key} is required and must be non-empty")
    tier = data.get("tier")
    if tier is not None and tier not in _LIBRARY_TIERS:
        diags.append(
            f"{lib_dir.name}/library.yaml: tier must be one of {_LIBRARY_TIERS}, got {tier!r}"
        )
    name = data.get("name")
    return (str(name) if name else None), diags


class Catalog:
    """A loaded catalog: unique cell packages plus dataset-level validation."""

    def __init__(self) -> None:
        self.cells: dict[str, CellPackage] = {}  # key: library/name@version (or bare)
        #: catalog-wide interfaces.yaml definitions (NETFLOW.md "Interfaces
        #: (bundles)"), threaded through to every cell the same way taxonomy
        #: is; empty when the catalog has no interfaces.yaml.
        self.interfaces: dict[str, InterfaceDef] = {}

    @classmethod
    def load(cls, catalog_dir: str | Path, strict: bool = True) -> Catalog:
        """Load *catalog_dir* — two-level (library dirs of cell dirs) if any
        immediate subdirectory has a ``library.yaml``, else flat (cell dirs
        directly, one level deep — the old layout, kept for fixtures).

        ``strict`` is forwarded to :func:`load_cell` (unknown cell.yaml
        sections are errors by default). ``catalog_dir/taxonomy.yaml``, if
        present, is loaded once here and threaded through to every cell so
        ``idioms.functions`` validates against it (SELECTION.md sec 3).
        Raises :class:`CatalogError` collecting all per-cell, per-library,
        and dataset-level diagnostics.
        """
        root = Path(catalog_dir)
        if not root.is_dir():
            raise CatalogError([f"{root}: not a directory"])
        catalog = cls()
        diags: list[str] = []

        taxonomy_path = root / "taxonomy.yaml"
        taxonomy: dict[str, Any] | None = None
        if taxonomy_path.is_file():
            try:
                taxonomy = load_taxonomy(taxonomy_path)
            except TaxonomyError as exc:
                diags.append(str(exc))

        interfaces_path = root / "interfaces.yaml"
        interfaces: dict[str, InterfaceDef] | None = None
        if interfaces_path.is_file():
            try:
                interfaces = load_interfaces(interfaces_path)
            except InterfacesError as exc:
                diags.append(str(exc))
        catalog.interfaces = interfaces or {}

        subdirs = sorted(p for p in root.iterdir() if p.is_dir())
        library_dirs = [p for p in subdirs if _is_library_dir(p)]

        def _load_cell_dir(entry: Path, library: str | None, label: str) -> None:
            try:
                cell = load_cell(entry, strict=strict, taxonomy=taxonomy, interfaces=interfaces)
            except CellPackageError as exc:
                diags.extend(f"{label}: {d}" for d in exc.diagnostics)
                return
            if library is not None:
                cell = dataclasses.replace(cell, library=library)
            try:
                catalog.add(cell)
            except CatalogError as exc:
                diags.extend(exc.diagnostics)

        if library_dirs:
            for lib_dir in subdirs:
                if lib_dir not in library_dirs:
                    diags.append(
                        f"{lib_dir.name}: expected library.yaml (two-level catalog layout: "
                        "every catalog-root subdirectory must be a library)"
                    )
                    continue
                lib_name, lib_diags = _load_library_manifest(lib_dir)
                diags.extend(lib_diags)
                if lib_diags or lib_name is None:
                    continue
                for entry in sorted(p for p in lib_dir.iterdir() if p.is_dir()):
                    _load_cell_dir(entry, lib_name, f"{lib_name}/{entry.name}")
        else:
            for entry in subdirs:
                _load_cell_dir(entry, None, entry.name)

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

    def get(self, ref: str) -> CellPackage:
        """Resolve *ref* to a :class:`CellPackage`.

        *ref* may be a full key (``library/name@version``, or bare
        ``name@version`` for a cell loaded outside any library), which is
        matched exactly; or a bare ``name@version`` used as shorthand across
        libraries, which resolves iff exactly one loaded cell has that
        ``bare_key`` — otherwise :class:`CatalogError` reports the
        ambiguity (or absence).
        """
        if ref in self.cells:
            return self.cells[ref]
        matches = [c for c in self.cells.values() if c.bare_key == ref]
        if not matches:
            raise CatalogError([f"no cell matches {ref!r}"])
        if len(matches) > 1:
            raise CatalogError(
                [
                    f"ambiguous cell reference {ref!r}: matches "
                    + ", ".join(sorted(m.key for m in matches))
                ]
            )
        return matches[0]

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
