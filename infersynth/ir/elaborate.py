"""Elaboration: flatten a hierarchical Design into instances + a net partition.

``elaborate(design)`` walks the instance tree, validates connectivity and
parameter bindings, and produces a flat :class:`ElaboratedDesign`:

* instances with fully resolved parameters, keyed by hierarchical path
  (``"amp1/stage2"``), and
* a net -> {(instance_path, port_name)} partition, the structure the gates
  (DESIGN.md section 7 gate 3) and the KiCad compiler consume.

All output ordering is deterministic (sorted; no dict-order dependence).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from infersynth.ir.core import (
    BOUNDARY,
    Cell,
    Design,
    PortDirection,
)

__all__ = ["elaborate", "ElaboratedDesign", "ElaboratedInstance", "ElaborationError"]

#: A pin in the flat model: (hierarchical instance path, port name).
#: The empty path "" denotes the top-level Design's own boundary ports (IOBs).
Pin = tuple[str, str]


class ElaborationError(ValueError):
    """Raised when elaboration finds one or more design errors."""

    def __init__(self, diagnostics: list[str]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__(
            "elaboration failed with {} error(s):\n{}".format(
                len(diagnostics), "\n".join(f"  - {d}" for d in diagnostics)
            )
        )


@dataclass(frozen=True)
class ElaboratedInstance:
    """A flattened cell instance with fully resolved parameters."""

    path: str
    cell: Cell
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ElaboratedDesign:
    """The flat elaborated model."""

    name: str
    #: path -> ElaboratedInstance, in sorted path order.
    instances: Mapping[str, ElaboratedInstance]
    #: net name -> sorted tuple of pins, in sorted net-name order.
    nets: Mapping[str, tuple[Pin, ...]]
    #: net name -> clock/timing domain, for annotated nets only.
    domains: Mapping[str, str]

    def partition(self) -> dict[str, frozenset[Pin]]:
        """The net -> {pin} partition consumed by the equivalence gate."""
        return {name: frozenset(pins) for name, pins in self.nets.items()}


class _UnionFind:
    def __init__(self) -> None:
        self._parent: list[int] = []

    def make(self) -> int:
        self._parent.append(len(self._parent))
        return len(self._parent) - 1

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


class _Flattener:
    def __init__(self) -> None:
        self.uf = _UnionFind()
        self.node_names: dict[int, str] = {}  # node -> hierarchical net name
        self.node_domains: dict[int, list[tuple[str, str]]] = {}  # node -> [(name, domain)]
        self.pins: list[tuple[int, Pin, PortDirection]] = []
        self.instances: dict[str, ElaboratedInstance] = {}
        self.diagnostics: list[str] = []

    def error(self, msg: str) -> None:
        self.diagnostics.append(msg)

    def new_node(self, name: str, domain: str | None) -> int:
        node = self.uf.make()
        self.node_names[node] = name
        if domain is not None:
            self.node_domains[node] = [(name, domain)]
        return node

    def flatten(self, design: Design, prefix: str, boundary: dict[str, int],
                stack: tuple[int, ...]) -> None:
        """Flatten *design* at hierarchical *prefix*.

        *boundary* maps this design's boundary port names to parent net nodes
        (only for non-top designs; the top design's boundary ports become
        real pins with path "").
        """
        if id(design) in stack:
            self.error(f"recursive instantiation of design {design.name!r} at {prefix or '<top>'}")
            return
        stack = stack + (id(design),)
        here = f"{prefix}/" if prefix else ""
        is_top = prefix == "" and boundary is _TOP_BOUNDARY

        # One flat node per local net; merge with parent via boundary refs.
        local_nodes: dict[str, int] = {}
        # (inst_name, port_name) -> list of local net names, to detect double-connects.
        endpoint_nets: dict[tuple[str, str], list[str]] = {}
        for net_name in sorted(design.nets):
            net = design.nets[net_name]
            node = self.new_node(f"{here}{net_name}", net.domain)
            local_nodes[net_name] = node
            for ep in sorted(net.endpoints):
                endpoint_nets.setdefault(ep, []).append(net_name)
                inst_name, port_name = ep
                if inst_name == BOUNDARY:
                    if is_top:
                        port = design.ports[port_name]
                        self.pins.append((node, ("", port_name), port.direction))
                    elif port_name in boundary:
                        self.uf.union(boundary[port_name], node)
                    # else: parent left this boundary port unconnected;
                    # required-ness is checked by the parent frame below.

        if is_top:
            bound = {p for (i, p) in endpoint_nets if i == BOUNDARY}
            for port_name in sorted(design.ports):
                if design.ports[port_name].required and port_name not in bound:
                    self.error(
                        f"unconnected required boundary port {port_name!r} "
                        f"on top design {design.name!r}"
                    )

        for ep, net_names in sorted(endpoint_nets.items()):
            if len(net_names) > 1:
                self.error(
                    f"{here or '<top> '}{ep[0] or 'boundary'}.{ep[1]} is connected to "
                    f"multiple nets: {sorted(net_names)}"
                )

        # Walk instances in sorted order.
        for inst_name in sorted(design.instances):
            inst = design.instances[inst_name]
            path = f"{here}{inst_name}"
            connected = {p for (i, p) in endpoint_nets if i == inst_name}

            for port_name in sorted(inst.target.ports):
                port = inst.target.ports[port_name]
                if port.required and port_name not in connected:
                    self.error(
                        f"unconnected required port {path}.{port_name} "
                        f"({inst.target.name!r})"
                    )

            if isinstance(inst.target, Cell):
                resolved, param_diags = inst.target.resolve_params(inst.params)
                for diag in param_diags:
                    self.error(f"instance {path}: {diag}")
                self.instances[path] = ElaboratedInstance(
                    path=path, cell=inst.target, params=dict(sorted(resolved.items()))
                )
                for port_name in sorted(connected):
                    port = inst.target.ports[port_name]
                    for net_name in endpoint_nets[(inst_name, port_name)]:
                        self.pins.append(
                            (local_nodes[net_name], (path, port_name), port.direction)
                        )
            else:  # sub-Design: pass boundary nodes down and recurse.
                child_boundary: dict[str, int] = {}
                for port_name in sorted(connected):
                    for net_name in endpoint_nets[(inst_name, port_name)]:
                        child_boundary[port_name] = local_nodes[net_name]
                self.flatten(inst.target, path, child_boundary, stack)


_TOP_BOUNDARY: dict[str, int] = {}


def elaborate(design: Design) -> ElaboratedDesign:
    """Flatten and validate *design*; raise :class:`ElaborationError` on any error."""
    fl = _Flattener()
    fl.flatten(design, "", _TOP_BOUNDARY, ())

    # Group pins by union-find root.
    groups: dict[int, list[tuple[Pin, PortDirection]]] = {}
    for node, pin, direction in fl.pins:
        groups.setdefault(fl.uf.find(node), []).append((pin, direction))

    # Net naming: shallowest hierarchical name wins, ties lexicographic.
    def name_key(name: str) -> tuple[int, str]:
        return (name.count("/"), name)

    root_names: dict[int, str] = {}
    for node, name in fl.node_names.items():
        root = fl.uf.find(node)
        if root not in root_names or name_key(name) < name_key(root_names[root]):
            root_names[root] = name

    # Domain annotations: merged nets must agree.
    root_domains: dict[int, str] = {}
    for node, domain_annotations in fl.node_domains.items():
        root = fl.uf.find(node)
        for name, domain in domain_annotations:
            if root in root_domains and root_domains[root] != domain:
                fl.error(
                    f"net {root_names[root]!r}: conflicting timing-domain annotations "
                    f"({root_domains[root]!r} vs {domain!r} from {name!r})"
                )
            else:
                root_domains[root] = domain

    nets: dict[str, tuple[Pin, ...]] = {}
    domains: dict[str, str] = {}
    for root in sorted(groups, key=lambda r: root_names[r]):
        pins = sorted({pin for pin, _ in groups[root]})
        net_name = root_names[root]
        nets[net_name] = tuple(pins)

        # Direction check: at most one driver per net. Drivers are cell OUT
        # pins and top-level boundary IN pins (external inputs drive inward).
        drivers = sorted(
            pin
            for pin, direction in groups[root]
            if (direction == PortDirection.OUT and pin[0] != "")
            or (direction == PortDirection.IN and pin[0] == "")
        )
        if len(drivers) > 1:
            fl.error(
                f"net {net_name!r}: direction conflict, multiple drivers: "
                + ", ".join(f"{p[0] or '<boundary>'}.{p[1]}" for p in drivers)
            )
        if root in root_domains:
            domains[net_name] = root_domains[root]

    if fl.diagnostics:
        raise ElaborationError(sorted(fl.diagnostics))

    return ElaboratedDesign(
        name=design.name,
        instances=dict(sorted(fl.instances.items())),
        nets=nets,
        domains=dict(sorted(domains.items())),
    )
