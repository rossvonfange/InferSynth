"""Rail resolver (NETFLOW.md tier 1, "Rail resolver").

Every power-kind port across all instantiated cells binds to a *rail net* by
its exact port NAME — ``VCC`` ports join the ``VCC`` rail, ``GND`` ports the
``GND`` rail, and so on (the clock-tree analogy, DESIGN §2). One rail net per
distinct name.

**v0 identity rule (exact-name grouping).** Two power ports share a rail iff
they share a name. Voltage-aware rail identity — recognizing that two ``VIN``
ports at 3.3 V and 12 V are *different* rails, or that ``+5V`` and ``VCC`` may
be the *same* rail — is future work; v0 groups on the name string alone and
documents the assumption loudly here.

**v0 source rule.** A rail needs a source or it is an ``undriven_rail``
diagnostic (loud, never a guess — "nothing sources VCC" is a catalog/decision
gap). A member port counts as a source when either:

* its direction is ``out`` (a cell that *drives* a power rail out — e.g.
  ``power-input-conditioning``'s ``VOUT``), or
* its direction is ``passive`` AND its owning cell is a connector
  (``functions:`` contains ``connectivity``) — decided from the actual catalog
  data: connector cells (``function=connectivity``) model an external supply
  entering through passive power pins (``conn-power-2pin``'s ``VIN``/``GND``),
  so their passive power ports are legitimate rail sources.

A rail with only ``in``/``passive`` (non-connector) consumers and no such
source is undriven: it is still wired (rails always wire — the pins belong on a
common net) but it receives no ``PWR_FLAG`` at emission, so ERC's
``power_pin_not_driven`` stays visible — the same honest signal as the plan
diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.ir import PortDirection, PortKind

__all__ = ["RailPlan", "resolve_rails"]

_POWER = PortKind.POWER.value
_CONNECTIVITY = "connectivity"


@dataclass(frozen=True)
class RailPlan:
    """Rail nets grouped by port name, with source/undriven bookkeeping."""

    #: rail name -> sorted ((instname, port), ...) membership
    nets: dict[str, tuple[tuple[str, str], ...]]
    #: rail names that carry at least one recognized source
    driven: frozenset[str]
    #: loud ``undriven_rail`` diagnostics (one per undriven rail)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


def _is_source(cell, spec) -> bool:
    direction = spec.get("direction", PortDirection.PASSIVE.value)
    if direction == PortDirection.OUT.value:
        return True
    if direction == PortDirection.PASSIVE.value and _CONNECTIVITY in cell.functions:
        return True
    return False


def resolve_rails(instances, catalog: Catalog) -> RailPlan:
    """Group every power-kind port by name into rail nets (v0 exact-name).

    *instances* is any sequence exposing ``instname`` and ``cell_key``.
    Returns a :class:`RailPlan`; rails with no source yield an
    ``undriven_rail`` diagnostic and are absent from ``driven``.
    """
    members: dict[str, list[tuple[str, str]]] = {}
    sourced: set[str] = set()
    for inst in instances:
        cell = catalog.cells.get(inst.cell_key)
        if cell is None:
            continue
        for pname, spec in cell.ports.items():
            if spec.get("kind") != _POWER:
                continue
            members.setdefault(pname, []).append((inst.instname, pname))
            if _is_source(cell, spec):
                sourced.add(pname)

    nets = {name: tuple(sorted(m)) for name, m in members.items()}
    diagnostics = tuple(
        f"undriven_rail: rail {name!r} has {len(nets[name])} consumer port(s) "
        "but no source (no out-direction driver and no connector passive power "
        "port) — nothing sources it (catalog/decision gap; declare a source cell "
        "or connect a supply)"
        for name in sorted(nets)
        if name not in sourced
    )
    return RailPlan(nets=nets, driven=frozenset(sourced), diagnostics=diagnostics)
