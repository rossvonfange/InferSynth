"""MPN-level part binding (SEED_PLAN acceptance criterion 4).

A cell's ``selection.candidates`` (see :mod:`infersynth.catalog.loader` for the
schema and its validation) declares real, orderable parts and — via each
candidate's ``maps`` set — which fragment refs it binds. This module is the
deterministic v0 *binder*: given a loaded cell, it resolves every fragment ref
(the ``golden_netlist.txt`` ref universe, minus KiCad's ``#``-virtual refs) to
a concrete :class:`PartBinding` ``{mpn, manufacturer, footprint}``.

Pick rule (v0, deterministic): a ref binds to the **first** candidate (in
declared order) whose ``maps`` covers it. Lockfile-driven scoring across
competing candidates is a documented future hook (SELECTION §6 costs/lockfile)
— the ordered candidate list is the seam it will slot into.

Coverage is never silent: :func:`bind_parts` returns a :class:`BindResult`
whose ``unbound`` tuple lists every ref no candidate covers, so a partial
binding is a loud, structured result rather than a missing footprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["PartBinding", "BindResult", "bind_parts", "fragment_refs"]

# Instance-symbol Reference property (same shape the emitter renumbers). Used
# by the fragment-scan fallback when a cell ships no golden_netlist.txt.
_REF_PROP_RE = re.compile(r'\(property "Reference" "([^"]*)"')
_FIRST_INSTANCE_SYMBOL = "(lib_id"


@dataclass(frozen=True)
class PartBinding:
    """One fragment ref resolved to a concrete, orderable part."""

    mpn: str
    manufacturer: str
    footprint: str


@dataclass(frozen=True)
class BindResult:
    """Result of binding a cell's fragment refs to parts.

    ``bindings`` maps each covered ref -> :class:`PartBinding`; ``unbound`` is
    the sorted tuple of refs no candidate covered (empty iff complete).
    """

    bindings: dict[str, PartBinding] = field(default_factory=dict)
    unbound: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unbound

    @property
    def refs(self) -> tuple[str, ...]:
        """Every fragment ref considered (bound + unbound), sorted."""
        return tuple(sorted({*self.bindings, *self.unbound}))


def _scan_fragment_refs(fragment_path: Path) -> set[str]:
    """Fallback ref universe: the instance-symbol Reference props of a fragment,
    dropping KiCad ``#``-virtual refs. Only used when no golden_netlist.txt is
    available (the golden partition is the preferred, authored source)."""
    try:
        text = fragment_path.read_text()
    except OSError:
        return set()
    idx = text.find(_FIRST_INSTANCE_SYMBOL)
    if idx == -1:
        return set()
    return {r for r in _REF_PROP_RE.findall(text[idx:]) if not r.startswith("#")}


def fragment_refs(cell: CellPackage) -> set[str]:
    """The fragment ref universe of *cell* (minus ``#``-virtual refs).

    Prefers the cell's ``golden_netlist.txt`` partition (the authored source of
    truth, SEED_PLAN); falls back to scanning ``fragment.kicad_sch`` when a
    cell ships no golden netlist.
    """
    golden = cell.verification.get("golden_netlist") if cell.verification else None
    if golden:
        path = cell.path / golden
        if path.is_file():
            from infersynth.gates.netlist import parse_golden_netlist

            partition = parse_golden_netlist(path)
            return {
                ref
                for pins in partition.values()
                for ref, _pin in pins
                if not ref.startswith("#")
            }
    return _scan_fragment_refs(cell.path / "fragment.kicad_sch")


def _candidate_binding(cand: dict) -> PartBinding:
    return PartBinding(
        mpn=str(cand.get("mpn", "")),
        manufacturer=str(cand.get("manufacturer", "")),
        footprint=str(cand.get("footprint", "")),
    )


def bind_parts(cell: CellPackage) -> BindResult:
    """Bind every fragment ref of *cell* to a part (v0 first-covering pick).

    Deterministic: refs are resolved in sorted order, each to the first
    candidate (declared order) whose ``maps`` covers it. Refs no candidate
    covers land in :attr:`BindResult.unbound` — never dropped silently.
    """
    refs = fragment_refs(cell)
    candidates = (cell.selection or {}).get("candidates") or []
    bindings: dict[str, PartBinding] = {}
    for ref in sorted(refs):
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            maps = cand.get("maps") or {}
            if isinstance(maps, dict) and maps.get(ref):
                bindings[ref] = _candidate_binding(cand)
                break
    unbound = tuple(sorted(refs - set(bindings)))
    return BindResult(bindings=bindings, unbound=unbound)
