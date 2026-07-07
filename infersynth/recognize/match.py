"""STEP 2 — golden-partition subgraph match (anchored backtracking iso).

A cell's ``golden_netlist.txt`` is a small typed graph: nodes are the cell's
components (typed by reference class — ``R``/``C``/``U`` …), and each net is a
hyperedge over ``(ref, pin)`` endpoints. Recognizing an instance of the cell
in a design is finding an injective map ``phi: golden_ref -> design_ref`` that
preserves this structure: two golden pins share a golden net iff their images
share a design net.

**Anchoring.** The map is *seeded* on the MPN-matched component (STEP 1): the
design anchor's reference is forced onto a golden ref its MPN binds. From that
seed the remaining golden refs are placed by backtracking, expanded in a
deterministic BFS order over shared nets — so the search stays local to the
anchor's neighborhood and, for these small cells, tractable.

**Node typing + pin roles.** A golden ref maps only to a design ref of the
same class. Pin roles matter: an op-amp's pin 3 (``+``) and pin 2 (``-``) are
fixed, so ICs map pins by identity. Two-pin passives (``R``/``C``/``L``) are
electrically symmetric, so each is tried in both pin orientations — the
orientation is part of the match and is checked for consistency.

**Internal vs port nets.** A golden net whose name is *not* a declared cell
port is internal (private to the cell): its design image must carry *exactly*
the mapped pins — no foreign component may touch it. This is what makes a
near-miss topology (an extra part hung on an internal node, a missing part)
fail to match. A port net's image may carry extra pins (the cell's outside
world), so only containment of the mapped pins is required. The exactness rule
is checked at completion and drives continued backtracking, so a valid
alternative assignment is still found when the first is a near-miss.

**Determinism.** Candidate design refs and pin orientations are tried in
sorted order; the first complete, validated assignment is returned. No clocks,
no randomness (SELECTION.md §8).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from infersynth.gates.netlist import parse_golden_netlist

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage
    from infersynth.recognize.netlist import DesignNetlist

__all__ = ["GoldenGraph", "CellMatch", "match_cell", "load_golden_graph"]

# Reference classes whose two pins are electrically interchangeable.
_SYMMETRIC_CLASSES = frozenset({"R", "C", "L"})


def _ref_class(ref: str) -> str:
    i = 0
    while i < len(ref) and ref[i].isalpha():
        i += 1
    return ref[:i] if i else ref


@dataclass(frozen=True)
class GoldenGraph:
    """The typed graph of a cell's ``golden_netlist.txt``.

    ``nets`` maps a golden net name to its frozenset of ``(ref, pin)``.
    ``refs`` is every component ref. ``pins_of`` gives each ref's pin set.
    ``pin_net`` maps each golden ``(ref, pin)`` to its net name. ``internal``
    is the set of net names that are NOT declared cell ports.
    """

    cell_key: str
    nets: dict[str, frozenset[tuple[str, str]]]
    refs: tuple[str, ...]
    pins_of: dict[str, tuple[str, ...]]
    pin_net: dict[tuple[str, str], str]
    internal: frozenset[str]
    ports: frozenset[str]


def load_golden_graph(cell: CellPackage) -> GoldenGraph | None:
    """Build a :class:`GoldenGraph` from *cell*'s ``golden_netlist.txt``.

    ``None`` when the cell declares no ``verification.golden_netlist`` or the
    file is absent — such a cell is un-recognizable (reported as a catalog gap).
    """
    golden_name = (cell.verification or {}).get("golden_netlist")
    if not golden_name:
        return None
    path = cell.path / golden_name
    if not path.is_file():
        return None
    partition = parse_golden_netlist(path)
    nets: dict[str, frozenset[tuple[str, str]]] = {}
    pins_of: dict[str, set[str]] = {}
    pin_net: dict[tuple[str, str], str] = {}
    for net_name, pins in partition.items():
        clean = frozenset((r, p) for r, p in pins if not r.startswith("#"))
        if not clean:
            continue
        nets[net_name] = clean
        for r, p in clean:
            pins_of.setdefault(r, set()).add(p)
            pin_net[(r, p)] = net_name
    if not nets:
        return None
    port_names = frozenset(cell.ports or {})
    internal = frozenset(n for n in nets if n.lstrip("/") not in port_names)
    return GoldenGraph(
        cell_key=cell.key,
        nets=nets,
        refs=tuple(sorted(pins_of)),
        pins_of={r: tuple(sorted(ps)) for r, ps in pins_of.items()},
        pin_net=pin_net,
        internal=internal,
        ports=port_names,
    )


@dataclass(frozen=True)
class CellMatch:
    """A recognized instance of a cell in the design.

    ``phi`` maps golden ref -> design ref. ``pin_map`` maps golden ref -> a
    ``{golden_pin: design_pin}`` dict (identity for ICs, possibly swapped for
    symmetric passives). ``net_map`` maps golden net name -> design net name.
    ``design_refs`` is the sorted set of design refs this instance claims.
    """

    cell_key: str
    phi: dict[str, str]
    pin_map: dict[str, dict[str, str]] = field(default_factory=dict)
    net_map: dict[str, str] = field(default_factory=dict)

    @property
    def design_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self.phi.values()))


def _pin_orientations(cls: str, pins: tuple[str, ...]) -> list[dict[str, str]]:
    """The pin-permutation choices for a component of *cls* with golden *pins*.

    Identity always; plus the swap for a two-pin symmetric passive. Ordered
    identity-first for determinism.
    """
    identity = {p: p for p in pins}
    if cls in _SYMMETRIC_CLASSES and len(pins) == 2:
        a, b = pins
        return [identity, {a: b, b: a}]
    return [identity]


class _Matcher:
    """One anchored backtracking search of *golden* into *design*."""

    def __init__(self, golden: GoldenGraph, design: DesignNetlist) -> None:
        self.g = golden
        self.d = design
        self.phi: dict[str, str] = {}
        self.pin_map: dict[str, dict[str, str]] = {}
        self.net_map: dict[str, str] = {}
        self.used_design: set[str] = set()
        self.used_net: set[str] = set()

    def _order(self, seed: str) -> list[str]:
        """Golden refs in BFS order from *seed* over shared nets (sorted ties)."""
        adj: dict[str, set[str]] = {r: set() for r in self.g.refs}
        for pins in self.g.nets.values():
            members = sorted({r for r, _ in pins})
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    adj[a].add(b)
                    adj[b].add(a)
        order: list[str] = []
        seen = {seed}
        queue: deque[str] = deque([seed])
        while queue:
            cur = queue.popleft()
            order.append(cur)
            for nxt in sorted(adj[cur]):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for r in self.g.refs:  # defensive: refs unreachable via shared nets
            if r not in seen:
                order.append(r)
        return order

    def solve(self, seed_golden: str, seed_design: str) -> CellMatch | None:
        if seed_golden not in self.g.refs:
            return None
        if _ref_class(seed_golden) != self.d.components[seed_design].cls:
            return None
        order = self._order(seed_golden)
        if not self._place(order, 0, seed_golden, seed_design):
            return None
        return CellMatch(
            cell_key=self.g.cell_key,
            phi=dict(self.phi),
            pin_map={k: dict(v) for k, v in self.pin_map.items()},
            net_map=dict(self.net_map),
        )

    def _candidates(self, gref: str, forced: str | None) -> list[str]:
        if forced is not None:
            return [forced] if forced not in self.used_design else []
        cls = _ref_class(gref)
        return [r for r in self.d.components_of_class(cls) if r not in self.used_design]

    def _place(self, order: list[str], idx: int, seed_golden: str, seed_design: str) -> bool:
        if idx == len(order):
            return self._finalize()
        gref = order[idx]
        forced = seed_design if gref == seed_golden else None
        gpins = self.g.pins_of[gref]
        for dref in self._candidates(gref, forced):
            for orient in _pin_orientations(_ref_class(gref), gpins):
                trial_nets = self._try(gref, dref, orient)
                if trial_nets is None:
                    continue
                self.phi[gref] = dref
                self.pin_map[gref] = dict(orient)
                self.used_design.add(dref)
                for gnet, dnet in trial_nets:
                    self.net_map[gnet] = dnet
                    self.used_net.add(dnet)
                if self._place(order, idx + 1, seed_golden, seed_design):
                    return True
                del self.phi[gref]
                del self.pin_map[gref]
                self.used_design.discard(dref)
                for gnet, dnet in trial_nets:
                    self.net_map.pop(gnet, None)
                    self.used_net.discard(dnet)
        return False

    def _try(self, gref: str, dref: str, orient: dict[str, str]) -> list[tuple[str, str]] | None:
        """Net-consistency of placing *gref*->*dref* with *orient*; returns the
        newly-bound (golden net, design net) pairs, or ``None`` on conflict.

        Enforces a consistent bijection golden-net <-> design-net: each golden
        net maps to one design net (and vice-versa), agreeing with any binding
        already committed (``net_map``/``used_net``) and within this placement.
        """
        new: dict[str, str] = {}  # golden net -> design net proposed here
        for gp, dp in orient.items():
            dnet = self.d.pin_net.get((dref, dp))
            if dnet is None:  # design pin isn't connected to any net
                return None
            gnet = self.g.pin_net[(gref, gp)]
            target = self.net_map.get(gnet, new.get(gnet))
            if target is not None:
                if target != dnet:
                    return None
                continue
            # gnet is unbound: dnet must not already belong to a different gnet
            if dnet in self.used_net or dnet in new.values():
                return None
            new[gnet] = dnet
        return list(new.items())

    def _finalize(self) -> bool:
        """Internal-net exactness / port-net containment on a complete map.

        This is where a near-miss (extra part on an internal node, or a missing
        part) is rejected."""
        for gnet, dnet in self.net_map.items():
            expected = {(self.phi[r], self.pin_map[r][p]) for r, p in self.g.nets[gnet]}
            actual = set(self.d.net_pins.get(dnet, frozenset()))
            if gnet in self.g.internal:
                if actual != expected:
                    return False
            elif not expected <= actual:
                return False
        return True


def match_cell(
    cell: CellPackage,
    golden: GoldenGraph,
    design: DesignNetlist,
    seed_golden: str,
    seed_design: str,
) -> CellMatch | None:
    """Try to recognize an instance of *cell* anchored at design ref
    *seed_design* mapped onto golden ref *seed_golden*.

    Returns the first complete, validated :class:`CellMatch` in deterministic
    order, or ``None`` if the anchor's neighborhood is not isomorphic to the
    cell's golden partition.
    """
    return _Matcher(golden, design).solve(seed_golden, seed_design)
