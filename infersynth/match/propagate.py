"""Bidirectional endpoint propagation (RECON_HARVEST.md §3).

recon traces a signal chain from *both* known endpoints and scores the
constrained middle; dualized for synthesis, the FRD's declared **inputs** and
required **outputs** are the two known endpoints, and the matcher enumerates
the constrained candidate chains that close the gap between them
(RECON_HARVEST §3: "propagate forward from the FRD's declared inputs and
backward from required outputs until the cell chain closes; enumerate
constrained candidate chains").

Model (v0, structural — no sim in the loop yet):

* A requirement declares an :class:`EndpointSpec`: the signal *kinds* available
  at its input boundary and required at its output boundary
  (:class:`~infersynth.ir.PortKind`, power rails excluded — a rail is not a
  signal-chain endpoint).
* A candidate cell is a **two-port signal element**: it *consumes* a kind if it
  has an ``in``/``passive`` electrical-or-digital port of that kind, and
  *produces* a kind if it has an ``out``/``passive`` port of that kind.
* Cells chain when an upstream cell produces a kind a downstream cell consumes.
* A chain **closes** when its first cell consumes one of the endpoint input
  kinds and its last cell produces one of the endpoint output kinds.

Enumeration is a deterministic DFS in sorted cell-key order, bounded by a
count-based (never wall-clock, SELECTION §8) expansion budget; cells never
repeat within a chain (no cycles). When a requirement declares no endpoints,
each in-scope candidate is a trivial single-cell chain that is closed by
definition (nothing constrains the middle — "all compete").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.ir import PortDirection, PortKind
from infersynth.match.provenance import Candidate, CandidateChain, surfaced_by_idiom

__all__ = ["EndpointSpec", "propagate_chains"]

_SIGNAL_KINDS = frozenset({PortKind.ELECTRICAL.value, PortKind.DIGITAL.value})
_CONSUMING = frozenset({PortDirection.IN.value, PortDirection.PASSIVE.value})
_PRODUCING = frozenset({PortDirection.OUT.value, PortDirection.PASSIVE.value})


@dataclass(frozen=True)
class EndpointSpec:
    """A requirement's declared signal endpoints (RECON_HARVEST §3).

    ``inputs``/``outputs`` are signal-kind names (``"electrical"``,
    ``"digital"``). Empty ``inputs`` *and* empty ``outputs`` means "no declared
    endpoints" — the trivial single-cell-chain regime.
    """

    inputs: tuple[str, ...] = (PortKind.ELECTRICAL.value,)
    outputs: tuple[str, ...] = (PortKind.ELECTRICAL.value,)

    @property
    def declared(self) -> bool:
        return bool(self.inputs) and bool(self.outputs)


@dataclass(frozen=True)
class _CellIO:
    """A candidate cell reduced to the kinds it consumes / produces."""

    key: str
    consumes: frozenset[str] = field(default_factory=frozenset)
    produces: frozenset[str] = field(default_factory=frozenset)


def _cell_io(key: str, catalog: Catalog) -> _CellIO:
    cell = catalog.cells.get(key)
    if cell is None:
        return _CellIO(key=key)
    consumes: set[str] = set()
    produces: set[str] = set()
    for spec in cell.ports.values():
        kind = spec.get("kind", PortKind.ELECTRICAL.value)
        if kind not in _SIGNAL_KINDS:
            continue
        direction = spec.get("direction", PortDirection.PASSIVE.value)
        if direction in _CONSUMING:
            consumes.add(kind)
        if direction in _PRODUCING:
            produces.add(kind)
    return _CellIO(key=key, consumes=frozenset(consumes), produces=frozenset(produces))


def propagate_chains(
    requirement_id: str,
    candidates: tuple[Candidate, ...],
    catalog: Catalog,
    endpoints: EndpointSpec | None,
    budget: int,
) -> tuple[CandidateChain, ...]:
    """Enumerate the closed candidate chains bridging *requirement*'s endpoints.

    *candidates* are the in-scope idiom candidates. Returns every closed chain
    found within *budget* expansions, sorted (length, then cell-key tuple).
    """
    keys = sorted({c.cell_key for c in candidates})
    if not keys:
        return ()

    # No declared endpoints: each candidate is its own trivial closed chain.
    if endpoints is None or not endpoints.declared:
        return tuple(
            CandidateChain.make(
                requirement_id,
                (key,),
                closed=True,
                surfaced_by=(surfaced_by_idiom(),),
            )
            for key in keys
        )

    io = {key: _cell_io(key, catalog) for key in keys}
    in_kinds = frozenset(endpoints.inputs)
    out_kinds = frozenset(endpoints.outputs)

    closed: list[CandidateChain] = []
    budget_left = [budget]

    def _extend(path: tuple[str, ...], last_produces: frozenset[str]) -> None:
        if budget_left[0] <= 0:
            return
        # Record a closure if the last cell drives a required output kind.
        if last_produces & out_kinds:
            closed.append(
                CandidateChain.make(
                    requirement_id,
                    path,
                    closed=True,
                    surfaced_by=tuple(surfaced_by_idiom() for _ in path),
                )
            )
        for nxt in keys:  # sorted -> deterministic expansion order
            if nxt in path:
                continue
            nxt_io = io[nxt]
            if not (last_produces & nxt_io.consumes):
                continue
            if budget_left[0] <= 0:
                return
            budget_left[0] -= 1
            _extend(path + (nxt,), nxt_io.produces)

    for key in keys:  # forward frontier: cells that accept an input kind
        cell_io = io[key]
        if not (cell_io.consumes & in_kinds):
            continue
        if budget_left[0] <= 0:
            break
        budget_left[0] -= 1
        _extend((key,), cell_io.produces)

    return tuple(sorted(set(closed)))
