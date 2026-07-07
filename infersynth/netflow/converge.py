"""Design-scope convergence (NETFLOW.md tier 1, "Design-scope convergence").

The conservative UNIQUE-inference pass at whole-design scope: grow forward from
the design's source-side ports and backward from its sink-side ports, through
instantiated cells' typed signal ports, and infer a net **only where the whole
design admits exactly one consistent cross-requirement assignment** for a port
pair. Anything that meets two or more ways is ensemble variance — an ask, not a
guess: a :class:`~infersynth.netflow.decisions.WiringResolutionRequest`
resolved between runs into a declared ``feeds`` entry (NETFLOW.md).

**Anchoring rule (chosen + documented).** The anchors are the design's
connectivity boundary and its cells' typed faces:

* a port **sources** flow when its direction is ``out`` (a true driver) OR it
  is a ``passive`` signal port — the case that occurs in the catalog is a
  connector cell (``functions`` contains ``connectivity``), whose passive
  electrical pins model an external endpoint that may equally drive or receive;
* a port **sinks** flow when its direction is ``in`` OR it is ``passive``.

A passive port is therefore BOTH a source and a sink candidate. That is
deliberate and is exactly why, with the current catalog, most cross-requirement
convergence comes back ambiguous (a connector pin could plausibly drive or be
driven — the design has not said which): the honest outcome is a request, not a
fabricated net. A design converges to a unique inference only when, for some
signal kind, there is exactly one free source port and exactly one free sink
port, they are distinct, and they live in different requirements.

**No-revisit / acyclic discipline (DAG law).** Each inference consumes its two
endpoint ports (they leave both the source and sink pools), so the inferred
flow is a spanning growth that never returns to a claimed port — inferred flow
is a DAG by construction (NETFLOW.md). Only DECLARED feeds may close cycles.
Ports already wired by rails / intra-chain / feeds are excluded up front, so
convergence only ever proposes nets for the genuine residual.
"""

from __future__ import annotations

from dataclasses import dataclass

from infersynth.catalog import Catalog
from infersynth.ir import PortDirection, PortKind
from infersynth.netflow.decisions import WiringOption, WiringResolutionRequest

__all__ = ["ConvergedNet", "ConvergeResolution", "converge_design"]

_SIGNAL_KINDS = frozenset({PortKind.ELECTRICAL.value, PortKind.DIGITAL.value})
_SOURCING = frozenset({PortDirection.OUT.value, PortDirection.PASSIVE.value})
_SINKING = frozenset({PortDirection.IN.value, PortDirection.PASSIVE.value})


@dataclass(frozen=True)
class ConvergedNet:
    """One net inferred by whole-design convergence (a unique cross-req pair)."""

    name: str
    members: tuple[tuple[str, str], ...]  # sorted ((instname, port), ...)


@dataclass(frozen=True)
class ConvergeResolution:
    """The convergence pass output."""

    nets: tuple[ConvergedNet, ...] = ()
    diagnostics: tuple[str, ...] = ()
    requests: tuple[WiringResolutionRequest, ...] = ()


def _endpoint_pools(instances, catalog, already_wired):
    """Per signal kind, the free source and sink ports across the design."""
    sources: dict[str, list[tuple[str, str, str]]] = {}  # kind -> (inst, port, req)
    sinks: dict[str, list[tuple[str, str, str]]] = {}
    for inst in instances:
        cell = catalog.cells.get(inst.cell_key)
        if cell is None:
            continue
        for pname, spec in cell.ports.items():
            kind = spec.get("kind")
            if kind not in _SIGNAL_KINDS:
                continue
            if (inst.instname, pname) in already_wired:
                continue
            direction = spec.get("direction", PortDirection.PASSIVE.value)
            entry = (inst.instname, pname, inst.requirement_id)
            if direction in _SOURCING:
                sources.setdefault(kind, []).append(entry)
            if direction in _SINKING:
                sinks.setdefault(kind, []).append(entry)
    for pool in (sources, sinks):
        for kind in pool:
            pool[kind].sort()
    return sources, sinks


def converge_design(
    instances,
    catalog: Catalog,
    already_wired: set[tuple[str, str]] | None = None,
) -> ConvergeResolution:
    """Infer whole-design nets where flow is uniquely determined; else ask.

    *already_wired* is the set of ``(instname, port)`` already claimed by rails
    / intra-chain / feeds — those ports are excluded from convergence. Returns a
    :class:`ConvergeResolution`; deterministic (kinds and ports sorted).
    """
    already = already_wired or set()
    sources, sinks = _endpoint_pools(instances, catalog, already)

    nets: list[ConvergedNet] = []
    diagnostics: list[str] = []
    requests: list[WiringResolutionRequest] = []
    used: set[tuple[str, str]] = set()
    idx = 0

    for kind in sorted(set(sources) | set(sinks)):
        src = [s for s in sources.get(kind, []) if (s[0], s[1]) not in used]
        snk = [s for s in sinks.get(kind, []) if (s[0], s[1]) not in used]
        # candidate cross-requirement assignments (distinct port, distinct req).
        pairs = [
            (p, c)
            for p in src
            for c in snk
            if (p[0], p[1]) != (c[0], c[1]) and p[2] != c[2]
        ]
        if len(pairs) == 1:
            (pi, pp, _), (ci, cp, _) = pairs[0]
            members = tuple(sorted(((pi, pp), (ci, cp))))
            nets.append(ConvergedNet(name=f"c_{idx}", members=members))
            idx += 1
            used.update(members)
        elif len(pairs) >= 2:
            options = tuple(
                WiringOption(source=f"{p[0]}.{p[1]}", sink=f"{c[0]}.{c[1]}") for p, c in pairs
            )
            msg = (
                f"kind {kind}: {len(pairs)} admissible cross-requirement assignment(s) — "
                "whole-design flow does not converge to one (ensemble variance); declare a "
                "feed to resolve. Options: "
                + ", ".join(f"{o.source}->{o.sink}" for o in options)
            )
            requests.append(
                WiringResolutionRequest(
                    kind="converge-ambiguous", edge=f"kind {kind}", options=options, message=msg
                )
            )
            diagnostics.append(f"converge_ambiguous: {msg}")

    return ConvergeResolution(
        nets=tuple(nets), diagnostics=tuple(diagnostics), requests=tuple(requests)
    )
