"""Intra-chain wiring (NETFLOW.md tier 1, "Intra-chain wiring").

A decided winner's candidate chain that closed via propagation IS net intent:
``chain[i]``'s out-side signal ports wire to ``chain[i+1]``'s in-side signal
ports (NETFLOW.md). The chain was already computed by
:mod:`infersynth.match.propagate` and, before NETFLOW stage 1, discarded at
instantiation — here we consume it.

**v0 unique-pairing rule.** For each consecutive pair, a net is inferred ONLY
when the pairing is unambiguous: exactly one out-direction signal port on the
left cell and exactly one in-direction signal port on the right cell. Any other
count (multiple outs, multiple ins, or none) emits an ``ambiguous_pairing``
diagnostic naming the options and infers nothing — NETFLOW's house rule: infer
only where unique, ask rather than guess. (Multi-port disambiguation is future
work: qualified feeds / bundle-aware matching, NETFLOW tiers 2-3.)

Signal ports are the electrical/digital kinds; power ports are rails
(:mod:`infersynth.netflow.rails`), never signal-chain endpoints. ``out`` and
``passive`` produce; ``in`` and ``passive`` consume — mirroring
``propagate.py``'s producer/consumer model.
"""

from __future__ import annotations

from infersynth.catalog import Catalog
from infersynth.ir import PortDirection, PortKind

__all__ = ["intra_chain_nets"]

_SIGNAL_KINDS = frozenset({PortKind.ELECTRICAL.value, PortKind.DIGITAL.value})
_PRODUCING = frozenset({PortDirection.OUT.value, PortDirection.PASSIVE.value})
_CONSUMING = frozenset({PortDirection.IN.value, PortDirection.PASSIVE.value})


def _signal_ports(cell, roles: frozenset[str]) -> list[str]:
    return sorted(
        pname
        for pname, spec in cell.ports.items()
        if spec.get("kind") in _SIGNAL_KINDS
        and spec.get("direction", PortDirection.PASSIVE.value) in roles
    )


def intra_chain_nets(
    requirement_id: str,
    chain: tuple[tuple[str, str], ...],
    catalog: Catalog,
) -> tuple[list[tuple[tuple[str, str], tuple[str, str]]], list[str]]:
    """Infer signal nets for a chain's consecutive pairs.

    *chain* is the ordered ``((instname, cell_key), ...)`` of the winner chain
    (source end first). Returns ``(pairs, diagnostics)`` where each pair is
    ``((out_instname, out_port), (in_instname, in_port))`` for a uniquely
    resolved connection, and diagnostics name every ambiguous (skipped) pairing.
    """
    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []
    diagnostics: list[str] = []
    for (inst_a, key_a), (inst_b, key_b) in zip(chain, chain[1:], strict=False):
        cell_a = catalog.cells.get(key_a)
        cell_b = catalog.cells.get(key_b)
        if cell_a is None or cell_b is None:
            continue
        outs = _signal_ports(cell_a, _PRODUCING)
        ins = _signal_ports(cell_b, _CONSUMING)
        if len(outs) == 1 and len(ins) == 1:
            pairs.append(((inst_a, outs[0]), (inst_b, ins[0])))
        else:
            diagnostics.append(
                f"ambiguous_pairing: {requirement_id} chain link "
                f"{inst_a}({key_a}) -> {inst_b}({key_b}) is not unique — "
                f"out ports {outs or ['(none)']} vs in ports {ins or ['(none)']}; "
                "cannot infer a single net (declare a qualified feed or use "
                "bundle-aware matching)"
            )
    return pairs, diagnostics
