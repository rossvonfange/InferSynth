"""Netlist partition equivalence (DESIGN.md section 7, gate 3) — implemented fully.

Compares two net -> {pin} partitions *modulo net naming*: the partitions are
equivalent iff they induce the same grouping of the same pin universe
(partition isomorphism on the pin sets). This is how the compiler proves the
generated schematic's netlist says exactly what the elaborated IR said.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from infersynth.gates.runner import GateResult

__all__ = ["compare_partitions", "partition_equivalence_gate", "PartitionDiff"]

Pin = tuple[str, str]
Partition = Mapping[str, Iterable[Pin]]


class PartitionDiff:
    """Result of comparing two partitions."""

    def __init__(self, equivalent: bool, diagnostics: list[str]) -> None:
        self.equivalent = equivalent
        self.diagnostics = diagnostics

    def __bool__(self) -> bool:
        return self.equivalent


def _normalize(partition: Partition, label: str, diags: list[str]) -> Counter:
    """Partition -> multiset of frozen pin-sets. Net names are discarded."""
    groups: Counter = Counter()
    seen: dict[Pin, str] = {}
    for net in sorted(partition):
        pins = frozenset(tuple(p) for p in partition[net])
        if not pins:
            continue  # an empty net contributes nothing to the partition
        for pin in sorted(pins):
            if pin in seen:
                diags.append(
                    f"{label}: pin {pin} appears in multiple nets "
                    f"({seen[pin]!r} and {net!r}) — not a partition"
                )
            seen[pin] = net
        groups[pins] += 1
    return groups


def _fmt_pins(pins: frozenset) -> str:
    return "{" + ", ".join(f"{i or '<boundary>'}.{p}" for i, p in sorted(pins)) + "}"


def compare_partitions(a: Partition, b: Partition,
                       label_a: str = "IR", label_b: str = "netlist") -> PartitionDiff:
    """Compare two net->pins partitions modulo net naming."""
    diags: list[str] = []
    groups_a = _normalize(a, label_a, diags)
    groups_b = _normalize(b, label_b, diags)

    pins_a = {pin for group in groups_a for pin in group}
    pins_b = {pin for group in groups_b for pin in group}
    for pin in sorted(pins_a - pins_b):
        diags.append(f"pin {pin[0]}.{pin[1]} present in {label_a} but missing from {label_b}")
    for pin in sorted(pins_b - pins_a):
        diags.append(f"pin {pin[0]}.{pin[1]} present in {label_b} but missing from {label_a}")

    only_a = groups_a - groups_b
    only_b = groups_b - groups_a
    for group in sorted(only_a, key=sorted):
        diags.append(f"net group only in {label_a}: {_fmt_pins(group)}")
    for group in sorted(only_b, key=sorted):
        diags.append(f"net group only in {label_b}: {_fmt_pins(group)}")

    return PartitionDiff(equivalent=not diags, diagnostics=diags)


def partition_equivalence_gate(context: Mapping[str, Any]) -> GateResult:
    """Gate wrapper. Context keys:

    * ``ir_partition``: net -> pins from the elaborated IR
      (:meth:`ElaboratedDesign.partition`)
    * ``netlist_partition``: net -> pins exported from the generated schematic
    """
    name = "netlist-partition-equivalence"
    ir = context.get("ir_partition")
    nl = context.get("netlist_partition")
    if ir is None or nl is None:
        missing = [k for k in ("ir_partition", "netlist_partition") if context.get(k) is None]
        return GateResult.skipped(name, f"missing context: {', '.join(missing)}")
    diff = compare_partitions(ir, nl)
    if diff.equivalent:
        return GateResult.passed(name, f"{len(dict(ir))} nets, partitions isomorphic")
    return GateResult.failed(name, *diff.diagnostics)
