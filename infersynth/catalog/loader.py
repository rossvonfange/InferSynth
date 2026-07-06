"""Cell package loader + validator (DESIGN.md section 5, post-collapse layout).

A cell package is a directory:

    <cell-dir>/
        cell.yaml           # sections below
        fragment.kicad_sch  # existence-checked only for now
        model/              # SystemC-AMS model (existence-checked)
        testbench/          # stimulus + expected results (existence-checked)

``cell.yaml`` sections (schema v1, BUILD_PLAN WP1):

* ``manifest``: name, version, description, provenance, license
* ``idioms``: keywords (list[str]), params (name -> {type, range|allowed, ...}),
  disambiguation (freeform; presence waives idiom collisions, see catalog.py)
* ``ports``: name -> {direction: in|out|inout|passive,
  kind: electrical|power|digital}
* ``bindings``: fragment ref -> arithmetic expression string over idiom params
  (grammar: infersynth.bind.expr). A binding value may also be a mapping
  ``{expr: <string>, format: <hint>}`` when the bound value needs a
  presentational format hint (e.g. ``format: capacitance`` for a farads-valued
  binding, vs the ohms-style default) — see ``CellPackage.binding_formats``.
  ``format`` is metadata only; it does not change binder semantics.
* ``verification``: golden_netlist (filename relative to the cell dir;
  must exist)
* ``selection``: freeform mapping (stub — the v2 scoring engine's input)
* ``depth``: level (L0|L1|L2) + layout_assumptions (required for L1/L2)

Strictness: by default (``strict=True``) unknown top-level sections are
validation errors; ``strict=False`` keeps the old tolerance and ignores them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from infersynth.ir import PortDirection, PortKind

__all__ = ["CellPackage", "CellPackageError", "load_cell"]

DEPTH_LEVELS = ("L0", "L1", "L2")
_MANIFEST_KEYS = ("name", "version", "description", "provenance", "license")
_PARAM_TYPES = ("int", "float", "str", "bool")
_PORT_DIRECTIONS = tuple(d.value for d in PortDirection)
_PORT_KINDS = tuple(k.value for k in PortKind)
_KNOWN_SECTIONS = (
    "manifest",
    "idioms",
    "ports",
    "bindings",
    "verification",
    "selection",
    "depth",
)


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
    #: port name -> {direction, kind} (values normalized, defaults applied)
    ports: dict[str, dict[str, str]] = field(default_factory=dict)
    #: fragment ref -> binding expression string
    bindings: dict[str, str] = field(default_factory=dict)
    #: fragment ref -> format hint (only for refs whose binding declared one)
    binding_formats: dict[str, str] = field(default_factory=dict)
    #: verification metadata (golden_netlist, ...)
    verification: dict[str, Any] = field(default_factory=dict)

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


def _validate_ports(ports: Any, diags: list[str]) -> dict[str, dict[str, str]]:
    """Validate the ``ports`` section; return normalized name -> {direction, kind}."""
    normalized: dict[str, dict[str, str]] = {}
    if ports is None:
        return normalized
    if not isinstance(ports, dict):
        diags.append("cell.yaml: ports must be a mapping of port name -> {direction, kind}")
        return normalized
    for pname, spec in sorted(ports.items()):
        where = f"cell.yaml: ports.{pname}"
        if not isinstance(spec, dict):
            diags.append(f"{where} must be a mapping with 'direction' and 'kind'")
            continue
        unknown = sorted(set(spec) - {"direction", "kind"})
        if unknown:
            diags.append(f"{where}: unknown key(s) {unknown}")
        direction = spec.get("direction", PortDirection.PASSIVE.value)
        if direction not in _PORT_DIRECTIONS:
            diags.append(
                f"{where}.direction: illegal direction {direction!r} "
                f"(expected one of {_PORT_DIRECTIONS})"
            )
            continue
        kind = spec.get("kind", PortKind.ELECTRICAL.value)
        if kind not in _PORT_KINDS:
            diags.append(f"{where}.kind: illegal kind {kind!r} (expected one of {_PORT_KINDS})")
            continue
        normalized[str(pname)] = {"direction": direction, "kind": kind}
    return normalized


_BINDING_MAPPING_KEYS = {"expr", "format"}


def _validate_bindings(
    bindings: Any, idioms: dict[str, Any], diags: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate the ``bindings`` section (WP1 cross-checks, + format-hint extension).

    Every binding value is either a bare expression string, or a mapping
    ``{expr: <string>, format: <hint>}`` (the format hint is presentational
    metadata for downstream value formatting, e.g. ``capacitance`` vs the
    ohms-style default; it does not affect evaluation). Every expression must
    parse under the restricted grammar and every free name in it must be a
    declared idiom param. Returns ``(bindings, binding_formats)`` — the first
    maps ref -> expression string (format-hint stripped), the second maps
    ref -> format hint for the refs that declared one.
    """
    from infersynth.bind.expr import BindingError, free_names

    normalized: dict[str, str] = {}
    formats: dict[str, str] = {}
    if bindings is None:
        return normalized, formats
    if not isinstance(bindings, dict):
        diags.append("cell.yaml: bindings must be a mapping of ref -> expression string")
        return normalized, formats
    declared = set((idioms.get("params") or {}) if isinstance(idioms, dict) else {})
    for ref, value in sorted(bindings.items()):
        where = f"cell.yaml: bindings.{ref}"
        fmt: str | None = None
        if isinstance(value, dict):
            unknown = sorted(set(value) - _BINDING_MAPPING_KEYS)
            if unknown:
                diags.append(f"{where}: unknown key(s) {unknown} in binding mapping")
            expr = value.get("expr")
            if not isinstance(expr, str):
                diags.append(
                    f"{where}: mapping form requires a string 'expr', "
                    f"got {type(expr).__name__}"
                )
                continue
            if "format" in value:
                if not isinstance(value["format"], str) or not value["format"]:
                    diags.append(f"{where}.format must be a non-empty string")
                else:
                    fmt = value["format"]
        elif isinstance(value, str):
            expr = value
        else:
            diags.append(f"{where} must be an expression string, got {type(value).__name__}")
            continue
        try:
            names = free_names(expr)
        except BindingError as exc:
            diags.append(f"{where}: {exc}")
            continue
        for name in sorted(names - declared):
            diags.append(
                f"{where}: expression references {name!r}, "
                "which is not a declared idiom param"
            )
        normalized[str(ref)] = expr
        if fmt is not None:
            formats[str(ref)] = fmt
    return normalized, formats


def _validate_verification(
    verification: Any, cell_dir: Path, diags: list[str]
) -> dict[str, Any]:
    """Validate the ``verification`` section; golden_netlist file must exist."""
    if verification is None:
        return {}
    if not isinstance(verification, dict):
        diags.append("cell.yaml: verification must be a mapping")
        return {}
    golden = verification.get("golden_netlist")
    if golden is not None:
        if not isinstance(golden, str) or not golden:
            diags.append(
                "cell.yaml: verification.golden_netlist must be a non-empty filename string"
            )
        elif not (cell_dir / golden).is_file():
            diags.append(
                f"cell.yaml: verification.golden_netlist file {golden!r} "
                "does not exist in the cell directory"
            )
    return verification


def load_cell(cell_dir: str | Path, strict: bool = True) -> CellPackage:
    """Load and validate one cell package directory.

    With ``strict=True`` (the default) unknown top-level cell.yaml sections
    are validation errors; ``strict=False`` keeps the old tolerance.
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

    if strict:
        for section in sorted(set(data) - set(_KNOWN_SECTIONS)):
            diags.append(
                f"cell.yaml: unknown top-level section {section!r} "
                f"(known sections: {_KNOWN_SECTIONS}; pass strict=False to tolerate)"
            )

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

    ports = _validate_ports(data.get("ports"), diags)
    bindings, binding_formats = _validate_bindings(data.get("bindings"), idioms, diags)
    verification = _validate_verification(data.get("verification"), path, diags)

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
        ports=ports,
        bindings=bindings,
        binding_formats=binding_formats,
        verification=verification,
    )
