"""Declared-feeds consumption (NETFLOW.md tier 2, "Declared").

A :class:`~infersynth.spec.FeedEdge` is requirement-level dataflow the author
declared (spec ``feeds:`` or an FRD ``[feeds:]`` pragma). This resolver turns
each edge into port-level nets between the SRC requirement's instantiated cells
and the DST requirement's, under NETFLOW.md's house rule — infer only where
unique, otherwise emit a diagnostic and a :class:`WiringResolutionRequest`
naming the options; never guess.

Resolution order for one edge ``src -> dst`` (``dst_port`` optional qualifier):

1. **Undecided endpoints.** A requirement with no instantiated cell (undecided,
   skipped) makes the edge unresolvable — loud ``feeds_undecided`` diagnostic.
2. **Bundle mates.** When the SRC's tail cell and the DST's head cell both
   declare interface groups (NETFLOW.md "Interfaces") that
   :func:`~infersynth.catalog.interfaces.mates`, use the bundle pairing — each
   mated role becomes one net (``f_<src>_<dst>_<role>``). Exactly one mating
   pair is required; two or more is ambiguous (a request).
3. **Unique pairing.** Otherwise the fallback rule: exactly one *producing*
   signal port on the SRC tail cell (``out`` or ``passive`` electrical/digital)
   and exactly one *consuming* signal port on the DST head cell (``in`` or
   ``passive``). ``dst_port`` narrows the DST candidate set — by port name, or
   by an interface role naming a mapped port. Any residual multiplicity is an
   ambiguity (a request); zero on either side is unresolvable (a diagnostic).

The SRC end anchors on the requirement's chain **tail** (the sink-end cell that
produces the requirement's output); the DST end anchors on its chain **head**
(the source-end cell that consumes the requirement's input) — mirroring the
single signal that crosses a requirement boundary. Feeds MAY close cycles
(NETFLOW.md DAG law: declared back-edges are legal); nothing here rejects one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from infersynth.catalog import Catalog
from infersynth.catalog.interfaces import mates
from infersynth.ir import PortDirection, PortKind
from infersynth.netflow.decisions import WiringOption, WiringResolutionRequest
from infersynth.spec import FeedEdge

__all__ = ["FeedNet", "FeedsResolution", "resolve_feeds"]

_SIGNAL_KINDS = frozenset({PortKind.ELECTRICAL.value, PortKind.DIGITAL.value})
_PRODUCING = frozenset({PortDirection.OUT.value, PortDirection.PASSIVE.value})
_CONSUMING = frozenset({PortDirection.IN.value, PortDirection.PASSIVE.value})


@dataclass(frozen=True)
class FeedNet:
    """One port-level net a feed edge resolved to."""

    name: str
    members: tuple[tuple[str, str], ...]  # sorted ((instname, port), ...)


@dataclass(frozen=True)
class FeedsResolution:
    """Everything one pass over the declared feeds produced."""

    nets: tuple[FeedNet, ...] = ()
    diagnostics: tuple[str, ...] = ()
    requests: tuple[WiringResolutionRequest, ...] = ()
    #: edges that produced at least one net
    resolved: tuple[FeedEdge, ...] = ()
    #: (edge, reason) for edges that produced no net
    unresolved: tuple[tuple[FeedEdge, str], ...] = ()


def _sanitize(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    return name or "req"


def _signal_ports(cell, roles: frozenset[str]) -> list[str]:
    return sorted(
        pname
        for pname, spec in cell.ports.items()
        if spec.get("kind") in _SIGNAL_KINDS
        and spec.get("direction", PortDirection.PASSIVE.value) in roles
    )


def _ordered_instances(instances) -> dict[str, list]:
    """Group instances by requirement id, preserving list (== chain) order."""
    by_req: dict[str, list] = {}
    for inst in instances:
        by_req.setdefault(inst.requirement_id, []).append(inst)
    return by_req


def _narrow_dst_ports(cell, candidates: list[str], dst_port: str) -> tuple[list[str], str | None]:
    """Apply a ``dst_port`` qualifier (port name OR interface role).

    Returns ``(narrowed_candidates, error)``. ``error`` is set when the
    qualifier names nothing on this cell.
    """
    if dst_port in candidates:
        return [dst_port], None
    # interface-role qualifier: dst_port names a role in one of the cell's groups
    for group in cell.interfaces.values():
        port = group.map.get(dst_port)
        if port is not None:
            return ([port] if port in candidates else []), (
                None
                if port in candidates
                else f"dst_port role {dst_port!r} maps to port {port!r} which is not a "
                "consuming signal port on the destination"
            )
    if dst_port in cell.ports:
        return [], (
            f"dst_port {dst_port!r} is a port on the destination but not a consuming "
            "signal port (in/passive electrical or digital)"
        )
    return [], f"dst_port {dst_port!r} is neither a port nor an interface role on the destination"


def _bundle_nets(edge: FeedEdge, src_inst, dst_inst, catalog: Catalog):
    """Try the interface-bundle path; return (nets, request) or (None, None).

    ``(None, None)`` means "no interface groups to try" (fall through to unique
    pairing). A returned request means the bundle path was applicable but
    ambiguous (two+ mating pairs).
    """
    src_cell = catalog.cells.get(src_inst.cell_key)
    dst_cell = catalog.cells.get(dst_inst.cell_key)
    if not (src_cell and dst_cell and src_cell.interfaces and dst_cell.interfaces):
        return None, None
    mated: list[tuple[str, str, object]] = []  # (src_group, dst_group, MateResult)
    for sg_name in sorted(src_cell.interfaces):
        for dg_name in sorted(dst_cell.interfaces):
            res = mates(
                src_cell.interfaces[sg_name], dst_cell.interfaces[dg_name], catalog.interfaces
            )
            if res.mates:
                mated.append((sg_name, dg_name, res))
    if not mated:
        return None, None
    if len(mated) > 1:
        options = tuple(
            WiringOption(source=f"{src_inst.instname}.{sg}", sink=f"{dst_inst.instname}.{dg}")
            for sg, dg, _ in mated
        )
        req = WiringResolutionRequest(
            kind="feeds-ambiguous",
            edge=f"{edge.src} -> {edge.dst}",
            options=options,
            message=(
                f"feeds {edge.src} -> {edge.dst}: {len(mated)} interface-group pairs mate "
                f"({', '.join(f'{sg}/{dg}' for sg, dg, _ in mated)}) — cannot pick a bundle; "
                "qualify the feed (dst_port) or split the groups"
            ),
        )
        return (), req
    sg, dg, res = mated[0]
    # A WirePair names ports from the initiator/peripheral perspective — map each
    # back to the correct instance (src may be either role).
    if src_cell.interfaces[sg].role == "initiator":
        init_inst, periph_inst = src_inst, dst_inst
    else:
        init_inst, periph_inst = dst_inst, src_inst
    nets: list[FeedNet] = []
    base = f"f_{_sanitize(edge.src)}_{_sanitize(edge.dst)}"
    for pair in res.pairs:
        members = tuple(
            sorted(
                (
                    (init_inst.instname, pair.initiator_port),
                    (periph_inst.instname, pair.peripheral_port),
                )
            )
        )
        nets.append(FeedNet(name=f"{base}_{pair.role}", members=members))
    return nets, None


def resolve_feeds(
    feeds: tuple[FeedEdge, ...],
    instances,
    catalog: Catalog,
) -> FeedsResolution:
    """Resolve every declared :class:`FeedEdge` into port-level nets.

    *instances* is any sequence exposing ``instname``/``cell_key``/
    ``requirement_id`` in chain order within each requirement (as
    :func:`infersynth.synthesize.synthesize` appends them). Deterministic:
    edges are processed in ``(src, dst, dst_port)`` order, nets/members sorted.
    """
    by_req = _ordered_instances(instances)
    nets: list[FeedNet] = []
    diagnostics: list[str] = []
    requests: list[WiringResolutionRequest] = []
    resolved: list[FeedEdge] = []
    unresolved: list[tuple[FeedEdge, str]] = []

    for edge in sorted(feeds, key=lambda e: (e.src, e.dst, e.dst_port or "")):
        src_insts = by_req.get(edge.src)
        dst_insts = by_req.get(edge.dst)
        if not src_insts or not dst_insts:
            missing = edge.src if not src_insts else edge.dst
            reason = (
                f"requirement {missing!r} instantiated no cell (undecided/skipped) — "
                "cannot resolve the feed"
            )
            diagnostics.append(f"feeds_undecided: {edge.src} -> {edge.dst}: {reason}")
            unresolved.append((edge, reason))
            continue

        src_inst = src_insts[-1]  # chain tail: produces the requirement's output
        dst_inst = dst_insts[0]  # chain head: consumes the requirement's input

        # (a) interface-bundle path.
        bundle_nets, bundle_req = _bundle_nets(edge, src_inst, dst_inst, catalog)
        if bundle_req is not None:
            requests.append(bundle_req)
            diagnostics.append(f"feeds_ambiguous: {bundle_req.message}")
            unresolved.append((edge, "ambiguous interface-group mating"))
            continue
        if bundle_nets is not None:
            nets.extend(bundle_nets)
            resolved.append(edge)
            continue

        # (b)/(c) unique-pairing path, dst narrowed by dst_port.
        src_cell = catalog.cells.get(src_inst.cell_key)
        dst_cell = catalog.cells.get(dst_inst.cell_key)
        if src_cell is None or dst_cell is None:  # pragma: no cover - defensive
            reason = "instantiated cell not found in catalog"
            diagnostics.append(f"feeds_undecided: {edge.src} -> {edge.dst}: {reason}")
            unresolved.append((edge, reason))
            continue
        outs = _signal_ports(src_cell, _PRODUCING)
        ins = _signal_ports(dst_cell, _CONSUMING)
        if edge.dst_port is not None:
            ins, qual_err = _narrow_dst_ports(dst_cell, ins, edge.dst_port)
            if qual_err is not None:
                diagnostics.append(f"feeds_bad_qualifier: {edge.src} -> {edge.dst}: {qual_err}")
                unresolved.append((edge, qual_err))
                continue

        if len(outs) == 1 and len(ins) == 1:
            members = tuple(sorted(((src_inst.instname, outs[0]), (dst_inst.instname, ins[0]))))
            nets.append(
                FeedNet(name=f"f_{_sanitize(edge.src)}_{_sanitize(edge.dst)}", members=members)
            )
            resolved.append(edge)
            continue

        # ambiguous or unresolvable.
        if not outs or not ins:
            reason = (
                f"no unique pairing — src {src_inst.instname} produces {outs or ['(none)']}, "
                f"dst {dst_inst.instname} consumes {ins or ['(none)']}"
            )
            diagnostics.append(f"feeds_unresolvable: {edge.src} -> {edge.dst}: {reason}")
            unresolved.append((edge, reason))
            continue
        options = tuple(
            WiringOption(source=f"{src_inst.instname}.{o}", sink=f"{dst_inst.instname}.{i}")
            for o in outs
            for i in ins
        )
        qual = f" (qualified dst_port={edge.dst_port!r})" if edge.dst_port else ""
        req = WiringResolutionRequest(
            kind="feeds-ambiguous",
            edge=f"{edge.src} -> {edge.dst}",
            options=options,
            message=(
                f"feeds {edge.src} -> {edge.dst}{qual}: pairing is not unique — src produces "
                f"{outs}, dst consumes {ins}; qualify with dst_port or add an interface group"
            ),
        )
        requests.append(req)
        diagnostics.append(f"feeds_ambiguous: {req.message}")
        unresolved.append((edge, "ambiguous port pairing"))

    return FeedsResolution(
        nets=tuple(nets),
        diagnostics=tuple(diagnostics),
        requests=tuple(requests),
        resolved=tuple(resolved),
        unresolved=tuple(unresolved),
    )
