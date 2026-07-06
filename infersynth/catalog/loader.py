"""Cell package loader + validator (DESIGN.md section 5, post-collapse layout).

A cell package is a directory:

    <cell-dir>/
        cell.yaml           # sections: manifest, idioms, selection, depth
        fragment.kicad_sch  # existence-checked only for now
        model/              # SystemC-AMS model (existence-checked)
        testbench/          # stimulus + expected results (existence-checked)

``cell.yaml`` sections:

* ``manifest``: name, version, description, provenance, license
* ``idioms``: keywords (list[str]), params (name -> {type, range|allowed, ...}),
  disambiguation (freeform; presence waives idiom collisions, see catalog.py)
* ``selection``: freeform mapping (stub — the v2 scoring engine's input)
* ``depth``: level (L0|L1|L2) + layout_assumptions (required for L1/L2)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["CellPackage", "CellPackageError", "load_cell"]

DEPTH_LEVELS = ("L0", "L1", "L2")
_MANIFEST_KEYS = ("name", "version", "description", "provenance", "license")
_PARAM_TYPES = ("int", "float", "str", "bool")


class CellPackageError(ValueError):
    """Raised when a cell package fails validation."""

    def __init__(self, path: Path, diagnostics: list[str]) -> None:
        self.path = path
        self.diagnostics = list(diagnostics)
        super().__init__(
            "invalid cell package {}:\n{}".format(
                path, "\n".join(f"  - {d}" for d in diagnostics)
            )
        )


@dataclass(frozen=True)
class CellPackage:
    """A loaded, validated cell package."""

    path: Path
    name: str
    version: str
    manifest: dict[str, Any]
    idioms: dict[str, Any]
    selection: dict[str, Any]
    depth: dict[str, Any] = field(default_factory=lambda: {"level": "L0"})

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def keywords(self) -> tuple[str, ...]:
        return tuple(self.idioms.get("keywords", ()))

    @property
    def idiom_params(self) -> dict[str, dict[str, Any]]:
        return dict(self.idioms.get("params", {}) or {})

    @property
    def disambiguation(self) -> Any:
        return self.idioms.get("disambiguation")


def _check_mapping(data: Any, what: str, diags: list[str]) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        diags.append(f"{what} must be a mapping, got {type(data).__name__}")
        return {}
    return data


def _validate_idiom_params(params: Any, diags: list[str]) -> None:
    if params is None:
        return
    if not isinstance(params, dict):
        diags.append("idioms.params must be a mapping of param name -> schema")
        return
    for pname, schema in sorted(params.items()):
        where = f"idioms.params.{pname}"
        if not isinstance(schema, dict):
            diags.append(f"{where} must be a mapping")
            continue
        ptype = schema.get("type")
        if ptype is not None and ptype not in _PARAM_TYPES:
            diags.append(f"{where}.type: unknown type {ptype!r} (expected one of {_PARAM_TYPES})")
        prange = schema.get("range")
        if prange is not None:
            if not (isinstance(prange, (list, tuple)) and len(prange) == 2):
                diags.append(f"{where}.range must be a two-element [min, max] list")
            elif not all(v is None or isinstance(v, (int, float)) for v in prange):
                diags.append(f"{where}.range bounds must be numeric or null")
            elif (
                prange[0] is not None
                and prange[1] is not None
                and prange[0] > prange[1]
            ):
                diags.append(f"{where}.range: min {prange[0]!r} > max {prange[1]!r}")
        if prange is not None and schema.get("allowed") is not None:
            diags.append(f"{where}: 'range' and 'allowed' are mutually exclusive")


def load_cell(cell_dir: str | Path) -> CellPackage:
    """Load and validate one cell package directory.

    Raises :class:`CellPackageError` with all collected diagnostics on failure.
    """
    path = Path(cell_dir)
    diags: list[str] = []

    if not path.is_dir():
        raise CellPackageError(path, ["not a directory"])

    # --- required artifacts (existence-checked) ---
    yaml_path = path / "cell.yaml"
    if not yaml_path.is_file():
        raise CellPackageError(path, ["missing cell.yaml"])
    if not (path / "fragment.kicad_sch").is_file():
        diags.append("missing fragment.kicad_sch")
    if not (path / "model").is_dir():
        diags.append("missing model/ directory")
    if not (path / "testbench").is_dir():
        diags.append("missing testbench/ directory")

    # --- cell.yaml ---
    try:
        data = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        raise CellPackageError(path, diags + [f"cell.yaml: invalid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise CellPackageError(path, diags + ["cell.yaml must be a mapping"])

    for section in ("manifest", "idioms"):
        if section not in data:
            diags.append(f"cell.yaml: missing required section {section!r}")

    manifest = _check_mapping(data.get("manifest"), "cell.yaml: manifest", diags)
    for key in _MANIFEST_KEYS:
        if not manifest.get(key):
            diags.append(f"cell.yaml: manifest.{key} is required and must be non-empty")

    idioms = _check_mapping(data.get("idioms"), "cell.yaml: idioms", diags)
    keywords = idioms.get("keywords")
    if keywords is None:
        diags.append("cell.yaml: idioms.keywords is required")
    elif not (
        isinstance(keywords, list)
        and keywords
        and all(isinstance(k, str) and k for k in keywords)
    ):
        diags.append("cell.yaml: idioms.keywords must be a non-empty list of strings")
    _validate_idiom_params(idioms.get("params"), diags)

    selection = _check_mapping(data.get("selection"), "cell.yaml: selection", diags)

    depth = _check_mapping(data.get("depth"), "cell.yaml: depth", diags)
    level = depth.get("level", "L0")
    if level not in DEPTH_LEVELS:
        diags.append(f"cell.yaml: depth.level must be one of {DEPTH_LEVELS}, got {level!r}")
    elif level in ("L1", "L2") and not depth.get("layout_assumptions"):
        diags.append(
            f"cell.yaml: depth.level {level} requires declared depth.layout_assumptions "
            "(layer count, layer roles, clearance/width classes)"
        )
    depth = {**depth, "level": level}

    if diags:
        raise CellPackageError(path, sorted(diags))

    return CellPackage(
        path=path,
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        manifest=manifest,
        idioms=idioms,
        selection=selection,
        depth=depth,
    )
