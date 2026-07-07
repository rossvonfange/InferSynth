"""WiringPlan — the deterministic net set a synthesized design should carry.

NETFLOW stage 1 assembles a :class:`WiringPlan` from two inferred tiers
(NETFLOW.md "Three tiers of intent"):

* **rails** (:mod:`infersynth.netflow.rails`): power-kind ports grouped by
  exact port NAME, one rail net per name;
* **intra-chain signal nets** (:mod:`infersynth.netflow.intra`): the net intent
  a decided winner's multi-cell chain already implies.

Determinism (SELECTION §8): nets sort by ``(kind, name)`` with rails before
signals, every net's members sort by ``(instname, port)``, and signal nets are
named ``n_<reqid>_<k>`` with ``k`` the sorted pair index — so two runs over the
same inputs produce a byte-identical plan.

A port in NO net gets no sheet pin at emission time; its hierarchical label
stays orphaned inside the child (expected for unwired ports — the residual
worklist). :attr:`WiringPlan.unwired_signal_ports` lists exactly those signal
ports; :attr:`WiringPlan.clean` is the gate-flip predicate (no diagnostics AND
no unwired signal ports).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.ir import PortKind
from infersynth.netflow.converge import converge_design
from infersynth.netflow.decisions import WiringResolutionRequest
from infersynth.netflow.feeds import resolve_feeds
from infersynth.netflow.intra import intra_chain_nets
from infersynth.netflow.rails import resolve_rails
from infersynth.spec import FeedEdge

__all__ = ["Net", "WiringPlan", "build_plan"]

_SIGNAL_KINDS = frozenset({PortKind.ELECTRICAL.value, PortKind.DIGITAL.value})


@dataclass(frozen=True, order=True)
class Net:
    """One named net: a set of ``(instname, port)`` endpoints.

    ``kind`` is ``"rail"`` or ``"signal"``. ``driven`` marks a net that carries
    a recognized source (a rail with a source port, or any signal net — its
    upstream ``out`` port drives it); an undriven rail is ``driven=False`` and
    also surfaced as a plan diagnostic. Ordered ``(kind, name)`` for a stable
    plan sort (rails sort before signals: ``"rail" < "signal"``).
    """

    kind: str
    name: str
    driven: bool = field(compare=False)
    members: tuple[tuple[str, str], ...] = field(compare=False)


@dataclass(frozen=True)
class WiringPlan:
    """The full inferred net set for one synthesized design + diagnostics."""

    nets: tuple[Net, ...]
    diagnostics: tuple[str, ...]
    unwired_signal_ports: tuple[tuple[str, str], ...]
    #: wiring decisions the engine refused to guess (feeds/convergence ambiguity)
    resolution_requests: tuple[WiringResolutionRequest, ...] = ()
    #: declared feed edges that produced at least one net
    feeds_resolved: tuple[FeedEdge, ...] = ()
    #: (edge, reason) for declared feed edges that resolved to no net
    feeds_unresolved: tuple[tuple[FeedEdge, str], ...] = ()

    @property
    def rails(self) -> tuple[Net, ...]:
        return tuple(n for n in self.nets if n.kind == "rail")

    @property
    def signals(self) -> tuple[Net, ...]:
        return tuple(n for n in self.nets if n.kind == "signal")

    @property
    def clean(self) -> bool:
        """True when the plan has no diagnostics and no unwired signal ports —
        the gate-flip predicate for attempting full-hierarchy ERC-zero.

        Feed- and convergence-ambiguity requests each mirror into
        ``diagnostics`` (and their unresolved ports stay in
        ``unwired_signal_ports``), so this predicate already accounts for the
        feeds/convergence net sources.
        """
        return not self.diagnostics and not self.unwired_signal_ports


def _sanitize(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    return name or "req"


def build_plan(
    instances,
    winner_chains: dict[str, tuple[str, ...]],
    catalog: Catalog,
    feeds: tuple[FeedEdge, ...] = (),
    converge: bool = True,
) -> WiringPlan:
    """Assemble a :class:`WiringPlan` from instantiated cells + winner chains.

    *instances* is any sequence of objects exposing ``instname``, ``cell_key``
    and ``requirement_id`` (e.g. :class:`~infersynth.synthesize.Instantiated
    Cell`). *winner_chains* maps a requirement id to its winning chain's cell
    keys IN ORDER — only chains with more than one cell need appear (single-cell
    winners imply no intra-chain net). Rails are resolved for *all* instances.

    Net sources are layered cheapest-first (NETFLOW.md tiers): rails, then
    intra-chain, then declared *feeds* (tier 2), then design-scope
    *convergence* over the residual (tier 1, run last so it only proposes nets
    for ports nothing else claimed). ``converge=False`` skips the convergence
    pass. Feeds nets are named ``f_<src>_<dst>[_<role>]``, converged ``c_<k>``.
    """
    instances = list(instances)
    diagnostics: list[str] = []
    requests: list[WiringResolutionRequest] = []

    # --- tier 1: rails (all instances) ---
    rail_plan = resolve_rails(instances, catalog)
    diagnostics.extend(rail_plan.diagnostics)
    rail_nets = [
        Net(kind="rail", name=name, driven=name in rail_plan.driven, members=members)
        for name, members in sorted(rail_plan.nets.items())
    ]

    # --- tier 1: intra-chain signal nets (winners with >1 cell) ---
    signal_nets: list[Net] = []
    wired_signal_ports: set[tuple[str, str]] = set()
    for rid in sorted(winner_chains):
        chain_keys = winner_chains[rid]
        if len(chain_keys) <= 1:
            continue
        seq = _chain_instnames(rid, chain_keys, instances)
        pairs, chain_diags = intra_chain_nets(rid, seq, catalog)
        diagnostics.extend(chain_diags)
        for k, ((ia, pa), (ib, pb)) in enumerate(pairs):
            members = tuple(sorted(((ia, pa), (ib, pb))))
            signal_nets.append(
                Net(
                    kind="signal",
                    name=f"n_{_sanitize(rid)}_{k}",
                    driven=True,  # an intra-chain net is driven by its out port
                    members=members,
                )
            )
            wired_signal_ports.update(members)

    # --- tier 2: declared feeds (may close cycles) ---
    feeds_res = resolve_feeds(feeds, instances, catalog)
    diagnostics.extend(feeds_res.diagnostics)
    requests.extend(feeds_res.requests)
    for fnet in feeds_res.nets:
        signal_nets.append(
            Net(kind="signal", name=fnet.name, driven=True, members=fnet.members)
        )
        wired_signal_ports.update(fnet.members)

    # --- tier 1: design-scope convergence over the residual ---
    if converge:
        conv_res = converge_design(instances, catalog, already_wired=set(wired_signal_ports))
        diagnostics.extend(conv_res.diagnostics)
        requests.extend(conv_res.requests)
        for cnet in conv_res.nets:
            signal_nets.append(
                Net(kind="signal", name=cnet.name, driven=True, members=cnet.members)
            )
            wired_signal_ports.update(cnet.members)

    # --- residual: signal ports touched by no signal net ---
    unwired: list[tuple[str, str]] = []
    for inst in instances:
        cell = catalog.cells.get(inst.cell_key)
        if cell is None:
            continue
        for pname, spec in cell.ports.items():
            if spec.get("kind") not in _SIGNAL_KINDS:
                continue
            if (inst.instname, pname) not in wired_signal_ports:
                unwired.append((inst.instname, pname))

    nets = tuple(sorted(rail_nets + signal_nets))
    return WiringPlan(
        nets=nets,
        diagnostics=tuple(diagnostics),
        unwired_signal_ports=tuple(sorted(unwired)),
        resolution_requests=tuple(requests),
        feeds_resolved=feeds_res.resolved,
        feeds_unresolved=feeds_res.unresolved,
    )


def _chain_instnames(
    rid: str, chain_keys: tuple[str, ...], instances
) -> tuple[tuple[str, str], ...]:
    """Map a requirement's ordered chain cell keys to (instname, cell_key).

    Instances are matched within the requirement, preserving chain order. A
    chain cell with no matching instance (e.g. skipped at instantiation) is
    dropped — intra-chain inference then simply sees a shorter chain.
    """
    by_key: dict[str, str] = {}
    for inst in instances:
        if inst.requirement_id == rid:
            by_key.setdefault(inst.cell_key, inst.instname)
    seq: list[tuple[str, str]] = []
    for key in chain_keys:
        instname = by_key.get(key)
        if instname is not None:
            seq.append((instname, key))
    return tuple(seq)
