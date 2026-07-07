"""fabric.yaml schema + loader (docs/FABRIC.md, deliverable 1).

A fabric is a catalog artifact beside cells: a routed KiCad board plus a
``fabric.yaml`` declaring **sites**. Each site references a cell (or a small
set of alternative cells sharing the footprint pattern), the footprint
**refs** it occupies on the board, the parameters the routed topology
**bakes** (``params_fixed``), and the value-stuffing dims chosen at stuff time
(``params_stuffable``). A ``tie_off`` block declares how an *unstuffed* site is
rendered inert (v0: ``dnp-all``).

``fabric.yaml`` sketch (FABRIC.md)::

    fabric:
      name: demo-fabric
      board: fabric.kicad_pcb
      sites:
        - id: amp0
          cell: core/opamp-gain-noninverting   # or cells: [alternatives]
          refs: [U101, R101, R102]
          params_fixed: {rg_ohms: 1000.0}
          params_stuffable: [gain]
      tie_off:
        amp0: {policy: dnp-all, note: "inputs pulled by R103 (always stuffed)"}

Validation (deliverable 1): refs unique across sites; every cell exists in the
catalog (the loader takes the catalog); ``tie_off`` policy enum (``dnp-all``
in v0); ``board`` file exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from infersynth.catalog import Catalog, CatalogError

__all__ = [
    "Fabric",
    "Site",
    "TieOff",
    "FabricError",
    "load_fabric",
    "derive_ref_map",
    "prefix_num",
]

#: unstuffed-site inertness policies (FABRIC.md); v0 ships ``dnp-all`` only.
TIE_OFF_POLICIES = ("dnp-all",)

_FABRIC_KEYS = ("name", "board", "sites", "tie_off")
_SITE_KEYS = (
    "id",
    "cell",
    "cells",
    "refs",
    "params_fixed",
    "params_stuffable",
    "ref_map",
)
_TIE_OFF_KEYS = ("policy", "note")


class FabricError(ValueError):
    """Raised when a ``fabric.yaml`` fails validation (collects all diagnostics)."""

    def __init__(self, diagnostics: list[str]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__(
            "fabric validation failed with {} error(s):\n{}".format(
                len(diagnostics), "\n".join(f"  - {d}" for d in diagnostics)
            )
        )


@dataclass(frozen=True)
class Site:
    """One fabric site: a routed footprint slot a winning cell can populate.

    ``cells`` is the resolved set of canonical catalog keys this site accepts
    (a single ``cell:`` or a list of ``cells:``). ``refs`` is the site's
    footprint refs on the board (the DNP set when the site is left unstuffed).
    ``ref_map`` maps the cell's *fragment* refs to board refs; when omitted it
    is derived from ``refs`` by refdes-prefix grouping (see
    :func:`derive_ref_map`).
    """

    id: str
    cells: tuple[str, ...]
    refs: tuple[str, ...]
    params_fixed: dict[str, float] = field(default_factory=dict)
    params_stuffable: tuple[str, ...] = ()
    ref_map: dict[str, str] | None = None


@dataclass(frozen=True)
class TieOff:
    """An unstuffed-site inertness declaration (FABRIC.md)."""

    site_id: str
    policy: str
    note: str = ""


@dataclass(frozen=True)
class Fabric:
    """A loaded, validated fabric: its board path plus its declared sites."""

    name: str
    dir: Path
    board_path: Path
    sites: tuple[Site, ...]
    tie_off: dict[str, TieOff] = field(default_factory=dict)

    @property
    def site_by_id(self) -> dict[str, Site]:
        return {s.id: s for s in self.sites}


def prefix_num(ref: str) -> tuple[str, int]:
    """Split a refdes into ``(alpha-prefix, numeric-suffix)`` (``R101`` -> ``("R", 101)``).

    Producer-facing (a fabric *producer* such as Loom groups fragment refs by
    prefix to lay out board refdes centuries); public alongside
    :func:`derive_ref_map`, which recovers the fragment→board mapping.
    """
    i = len(ref)
    while i > 0 and ref[i - 1].isdigit():
        i -= 1
    prefix = ref[:i]
    suffix = ref[i:]
    return prefix, (int(suffix) if suffix.isdigit() else 0)


#: Backward-compatible private alias (pre-promotion name). Prefer :func:`prefix_num`.
_prefix_num = prefix_num


def derive_ref_map(fragment_refs: list[str], board_refs: list[str]) -> dict[str, str]:
    """Map cell fragment refs to board refs by refdes-prefix grouping.

    Deterministic: within each shared alpha prefix, fragment refs and board
    refs are sorted by numeric suffix and zipped (``R1``->``R101``,
    ``R2``->``R102``). Only prefixes present in *fragment_refs* are mapped
    (board-only refs — e.g. an op-amp ``U101`` with no value binding — are left
    unmapped; they are DNP-only). Raises ``ValueError`` when a shared prefix
    has mismatched counts (the mapping would be ambiguous).
    """
    by_prefix_frag: dict[str, list[str]] = {}
    for r in fragment_refs:
        by_prefix_frag.setdefault(prefix_num(r)[0], []).append(r)
    by_prefix_board: dict[str, list[str]] = {}
    for r in board_refs:
        by_prefix_board.setdefault(prefix_num(r)[0], []).append(r)
    out: dict[str, str] = {}
    for prefix in sorted(by_prefix_frag):
        frags = sorted(by_prefix_frag[prefix], key=prefix_num)
        boards = sorted(by_prefix_board.get(prefix, []), key=prefix_num)
        if len(frags) != len(boards):
            raise ValueError(
                f"cannot map fragment refs {frags} to board refs {boards} for "
                f"prefix {prefix!r}: counts differ ({len(frags)} vs {len(boards)})"
            )
        for f, b in zip(frags, boards, strict=True):
            out[f] = b
    return out


def _resolve_cell_key(catalog: Catalog, ref: str) -> str:
    """Resolve a fabric ``cell:`` ref to a canonical catalog key.

    Accepts a full key (``library/name@version``), a bare ``name@version``, or
    — as the FABRIC.md sketch writes them — a *version-less* ``library/name`` or
    bare ``name``, resolving iff exactly one cell matches. Raises
    :class:`CatalogError` (absent / ambiguous) otherwise.
    """
    try:
        return catalog.get(ref).key
    except CatalogError:
        pass
    matches = sorted(
        {
            c.key
            for c in catalog.cells.values()
            if ref in (f"{c.library}/{c.name}" if c.library else c.name, c.name)
        }
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CatalogError([f"no cell matches {ref!r}"])
    raise CatalogError(
        [f"ambiguous cell reference {ref!r}: matches " + ", ".join(matches)]
    )


def _validate_site(raw: Any, index: int, catalog: Catalog, diags: list[str]) -> Site | None:
    where = f"fabric.sites[{index}]"
    if not isinstance(raw, dict):
        diags.append(f"{where} must be a mapping")
        return None
    unknown = sorted(set(raw) - set(_SITE_KEYS))
    if unknown:
        diags.append(f"{where}: unknown key(s) {unknown} (known: {list(_SITE_KEYS)})")

    site_id = raw.get("id")
    if not isinstance(site_id, str) or not site_id:
        diags.append(f"{where}.id is required and must be a non-empty string")
        site_id = None

    # cell / cells (exactly one)
    cell = raw.get("cell")
    cells = raw.get("cells")
    resolved: tuple[str, ...] = ()
    if (cell is None) == (cells is None):
        diags.append(f"{where}: exactly one of 'cell' or 'cells' is required")
    else:
        refs_in = [cell] if cell is not None else cells
        if not (isinstance(refs_in, list) if cells is not None else True):
            diags.append(f"{where}.cells must be a list of cell keys")
        else:
            keys = [cell] if cell is not None else list(cells)
            resolved_list: list[str] = []
            for k in keys:
                if not isinstance(k, str) or not k:
                    diags.append(f"{where}: cell reference {k!r} must be a non-empty string")
                    continue
                try:
                    resolved_list.append(_resolve_cell_key(catalog, k))
                except CatalogError as exc:
                    diags.append(f"{where}: {exc.diagnostics[0]}")
            resolved = tuple(resolved_list)

    refs = raw.get("refs")
    ref_tuple: tuple[str, ...] = ()
    if not (isinstance(refs, list) and refs and all(isinstance(r, str) and r for r in refs)):
        diags.append(f"{where}.refs must be a non-empty list of ref strings")
    else:
        if len(set(refs)) != len(refs):
            diags.append(f"{where}.refs has duplicate refs within the site")
        ref_tuple = tuple(refs)

    params_fixed: dict[str, float] = {}
    pf = raw.get("params_fixed")
    if pf is not None:
        if not isinstance(pf, dict):
            diags.append(f"{where}.params_fixed must be a mapping of param -> value")
        else:
            for pname, pval in pf.items():
                if isinstance(pval, bool) or not isinstance(pval, (int, float)):
                    diags.append(f"{where}.params_fixed.{pname} must be numeric")
                else:
                    params_fixed[str(pname)] = float(pval)

    params_stuffable: tuple[str, ...] = ()
    ps = raw.get("params_stuffable")
    if ps is not None:
        if not (isinstance(ps, list) and all(isinstance(p, str) and p for p in ps)):
            diags.append(f"{where}.params_stuffable must be a list of param-name strings")
        else:
            params_stuffable = tuple(ps)

    ref_map = raw.get("ref_map")
    ref_map_val: dict[str, str] | None = None
    if ref_map is not None:
        if not (
            isinstance(ref_map, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in ref_map.items())
        ):
            diags.append(f"{where}.ref_map must be a mapping of fragment-ref -> board-ref")
        else:
            ref_map_val = {str(k): str(v) for k, v in ref_map.items()}

    if site_id is None:
        return None
    return Site(
        id=site_id,
        cells=resolved,
        refs=ref_tuple,
        params_fixed=params_fixed,
        params_stuffable=params_stuffable,
        ref_map=ref_map_val,
    )


def _validate_tie_off(
    raw: Any, site_ids: set[str], diags: list[str]
) -> dict[str, TieOff]:
    out: dict[str, TieOff] = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        diags.append("fabric.tie_off must be a mapping of site-id -> {policy, note}")
        return out
    for sid, spec in raw.items():
        where = f"fabric.tie_off.{sid}"
        if sid not in site_ids:
            diags.append(f"{where}: no such site declared in fabric.sites")
        if not isinstance(spec, dict):
            diags.append(f"{where} must be a mapping with a 'policy'")
            continue
        unknown = sorted(set(spec) - set(_TIE_OFF_KEYS))
        if unknown:
            diags.append(f"{where}: unknown key(s) {unknown}")
        policy = spec.get("policy")
        if policy not in TIE_OFF_POLICIES:
            diags.append(
                f"{where}.policy must be one of {TIE_OFF_POLICIES}, got {policy!r}"
            )
            continue
        note = spec.get("note", "")
        if note is not None and not isinstance(note, str):
            diags.append(f"{where}.note must be a string")
            note = ""
        out[str(sid)] = TieOff(site_id=str(sid), policy=str(policy), note=str(note or ""))
    return out


def load_fabric(
    fabric_yaml: str | Path, catalog: Catalog, *, require_board: bool = True
) -> Fabric:
    """Load + validate a ``fabric.yaml`` against *catalog* (deliverable 1).

    *fabric_yaml* is the path to the ``fabric.yaml`` file (its directory is the
    fabric root; ``board`` resolves relative to it). Raises :class:`FabricError`
    collecting every diagnostic: refs unique across sites, cells exist in the
    catalog, ``tie_off`` policy enum, and the board file exists.

    ``require_board`` (default True preserves today's behavior) gates only the
    board-file *existence* check: pass ``require_board=False`` to lint a fabric
    manifest **before its board is routed** (a producer such as Loom emits the
    ``fabric:`` block naming where the board will live, then validates structure
    pre-routing). The returned :class:`Fabric` still carries ``board_path`` (the
    path the board *will* occupy); nothing else in the loader depends on the
    board file, so a ``require_board=False`` load is otherwise identical.
    """
    path = Path(fabric_yaml)
    if not path.is_file():
        raise FabricError([f"{path}: fabric.yaml not found"])
    fabric_dir = path.parent

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise FabricError([f"{path}: invalid YAML: {exc}"]) from exc
    if not isinstance(data, dict) or "fabric" not in data:
        raise FabricError([f"{path}: top-level 'fabric' mapping is required"])
    fab = data["fabric"]
    if not isinstance(fab, dict):
        raise FabricError([f"{path}: 'fabric' must be a mapping"])

    diags: list[str] = []
    unknown = sorted(set(fab) - set(_FABRIC_KEYS))
    if unknown:
        diags.append(f"fabric: unknown key(s) {unknown} (known: {list(_FABRIC_KEYS)})")

    name = fab.get("name")
    if not isinstance(name, str) or not name:
        diags.append("fabric.name is required and must be a non-empty string")

    board = fab.get("board")
    board_path = fabric_dir / str(board) if isinstance(board, str) and board else None
    if not isinstance(board, str) or not board:
        diags.append("fabric.board is required and must be a non-empty filename")
    elif require_board and not board_path.is_file():  # type: ignore[union-attr]
        diags.append(f"fabric.board file {board!r} does not exist ({board_path})")

    raw_sites = fab.get("sites")
    sites: list[Site] = []
    if not isinstance(raw_sites, list) or not raw_sites:
        diags.append("fabric.sites is required and must be a non-empty list")
    else:
        for i, raw in enumerate(raw_sites):
            site = _validate_site(raw, i, catalog, diags)
            if site is not None:
                sites.append(site)

    # ids unique
    seen_ids: set[str] = set()
    for s in sites:
        if s.id in seen_ids:
            diags.append(f"fabric.sites: duplicate site id {s.id!r}")
        seen_ids.add(s.id)

    # refs unique across sites
    ref_owner: dict[str, str] = {}
    for s in sites:
        for r in s.refs:
            if r in ref_owner:
                diags.append(
                    f"fabric: ref {r!r} is used by both site {ref_owner[r]!r} "
                    f"and site {s.id!r} (refs must be unique across sites)"
                )
            else:
                ref_owner[r] = s.id

    tie_off = _validate_tie_off(fab.get("tie_off"), seen_ids, diags)

    if diags:
        raise FabricError(diags)

    return Fabric(
        name=str(name),
        dir=fabric_dir,
        board_path=board_path,  # type: ignore[arg-type]
        sites=tuple(sites),
        tie_off=tie_off,
    )
