"""Cell package format + catalog loader/validator (DESIGN.md section 5)."""

from infersynth.catalog.catalog import Catalog, CatalogError, IdiomCollision
from infersynth.catalog.interfaces import (
    InterfaceDef,
    InterfaceGroup,
    InterfacesError,
    MateResult,
    RoleDef,
    WirePair,
    load_interfaces,
    mates,
)
from infersynth.catalog.loader import CellPackage, CellPackageError, load_cell
from infersynth.catalog.taxonomy import TaxonomyError, load_taxonomy

__all__ = [
    "Catalog",
    "CatalogError",
    "CellPackage",
    "CellPackageError",
    "IdiomCollision",
    "InterfaceDef",
    "InterfaceGroup",
    "InterfacesError",
    "MateResult",
    "RoleDef",
    "TaxonomyError",
    "WirePair",
    "load_cell",
    "load_interfaces",
    "load_taxonomy",
    "mates",
]
