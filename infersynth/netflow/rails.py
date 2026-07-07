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
    #: rails whose source is a real out-direction driver (regulator VOUT etc.)
    #: — these need NO PWR_FLAG (one driver per net; playbook rule); rails
    #: driven only by a connector's passive port DO need the flag.
    hard_driven: frozenset[str] = frozenset()
    #: loud ``undriven_rail`` diagnostics (one per undriven rail)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


def _is_source(cell, spec) -> bool:
    direction = spec.get("direction", PortDirection.PASSIVE.value)
    if direction == PortDirection.OUT.value:
        return True
    if direction == PortDirection.PASSIVE.value and _CONNECTIVITY in cell.functions:
        return True
    return False


def resolve_rails(
    instances, catalog: Catalog, rail_aliases: dict[str, str] | None = None
) -> RailPlan:
    """Group every power-kind port by name into rail nets (v0 exact-name).

    *instances* is any sequence exposing ``instname`` and ``cell_key``.
    ``rail_aliases`` (spec ``rail_aliases:``, NETFLOW tier 2) maps a rail NAME
    onto another — e.g. ``{VCC: VOUT, VEE: GND}`` merges every ``VCC`` port
    (an explicitly-named ELECTRICAL port is adopted into the target rail too —
    a declared tie such as ``SHIELD: GND``; adoption is never inferred)
    into the ``VOUT`` rail net (alias chains follow; cycles guarded). Members
    keep their own port names; the net takes the canonical rail name.
    Returns a :class:`RailPlan`; rails with no source yield an
    ``undriven_rail`` diagnostic and are absent from ``driven``.
    """
    aliases = dict(rail_aliases or {})

    def _canon(name: str) -> str:
        seen = {name}
        while name in aliases:
            name = aliases[name]
            if name in seen:  # cycle guard: fall back to the last resolved name
                break
            seen.add(name)
        return name

    members: dict[str, list[tuple[str, str]]] = {}
    sourced: set[str] = set()
    hard: set[str] = set()
    for inst in instances:
        cell = catalog.cells.get(inst.cell_key)
        if cell is None:
            continue
        for pname, spec in cell.ports.items():
            if spec.get("kind") != _POWER:
                # an ELECTRICAL port is adopted into a rail only when the spec
                # names it explicitly in rail_aliases (declared tie — e.g.
                # EXC_N: GND, SHIELD: GND); never inferred.
                if pname not in aliases:
                    continue
            rail = _canon(pname)
            members.setdefault(rail, []).append((inst.instname, pname))
            if _is_source(cell, spec):
                sourced.add(rail)
                if spec.get("direction") == PortDirection.OUT.value:
                    hard.add(rail)

    nets = {name: tuple(sorted(m)) for name, m in members.items()}
    diagnostics = tuple(
        f"undriven_rail: rail {name!r} has {len(nets[name])} consumer port(s) "
        "but no source (no out-direction driver and no connector passive power "
        "port) — nothing sources it (catalog/decision gap; declare a source cell "
        "or connect a supply)"
        for name in sorted(nets)
        if name not in sourced
    )
    return RailPlan(
        nets=nets,
        driven=frozenset(sourced),
        hard_driven=frozenset(hard),
        diagnostics=diagnostics,
    )
