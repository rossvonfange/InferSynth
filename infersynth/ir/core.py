"""IR core: the Python-embedded structural DSL (DESIGN.md section 4).

A :class:`Cell` declares typed ports, parameters, and hooks for idiom
vocabulary and clock/timing-domain annotations. A :class:`Design` is a tree of
cell instances (a Design may also instantiate sub-Designs) plus nets connecting
instance ports, with boundary ports playing the IOB/connector role.

Users never write this IR directly; the intake stage constructs it. It is
importable, unit-testable code with no external parser.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "PortDirection",
    "PortKind",
    "Port",
    "Param",
    "Cell",
    "Instance",
    "Net",
    "Design",
    "BOUNDARY",
    "IRError",
]


class IRError(ValueError):
    """Raised for structurally invalid IR construction."""


class PortDirection(str, Enum):
    IN = "in"
    OUT = "out"
    INOUT = "inout"
    #: Undirected electrical terminal (resistor leg, connector pin, power pin).
    PASSIVE = "passive"


class PortKind(str, Enum):
    ELECTRICAL = "electrical"
    POWER = "power"
    DIGITAL = "digital"


#: Sentinel instance name for a Design's own boundary ports in net endpoints.
BOUNDARY = ""


@dataclass(frozen=True)
class Port:
    """A typed connection point on a Cell or on a Design boundary."""

    name: str
    direction: PortDirection = PortDirection.PASSIVE
    kind: PortKind = PortKind.ELECTRICAL
    #: Required ports must be connected at elaboration; optional ones may float.
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise IRError("port name must be non-empty")


_PARAM_TYPES: dict[str, type | tuple[type, ...]] = {
    "int": int,
    "float": (int, float),  # ints are acceptable floats
    "str": str,
    "bool": bool,
}


@dataclass(frozen=True)
class Param:
    """A typed cell parameter with optional default and range/allowed constraint."""

    name: str
    type: str = "float"
    default: Any = None
    #: Inclusive (min, max) bound for numeric params; either end may be None.
    range: tuple[Any, Any] | None = None
    #: Explicit allowed-value set (any type).
    allowed: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        if self.type not in _PARAM_TYPES:
            raise IRError(
                f"param {self.name!r}: unknown type {self.type!r} "
                f"(expected one of {sorted(_PARAM_TYPES)})"
            )
        if self.range is not None and self.allowed is not None:
            raise IRError(f"param {self.name!r}: 'range' and 'allowed' are mutually exclusive")
        if self.range is not None and self.type not in ("int", "float"):
            raise IRError(f"param {self.name!r}: 'range' requires a numeric type")
        if self.default is not None:
            problem = self.check(self.default)
            if problem is not None:
                raise IRError(f"param {self.name!r}: default {self.default!r} invalid: {problem}")

    def check(self, value: Any) -> str | None:
        """Return None if *value* satisfies this parameter, else a diagnostic string."""
        expected = _PARAM_TYPES[self.type]
        # bool is a subclass of int; do not accept True for an int/float param.
        if isinstance(value, bool) and self.type != "bool":
            return f"expected {self.type}, got bool {value!r}"
        if not isinstance(value, expected):
            return f"expected {self.type}, got {type(value).__name__} {value!r}"
        if self.range is not None:
            lo, hi = self.range
            if lo is not None and value < lo:
                return f"value {value!r} below minimum {lo!r}"
            if hi is not None and value > hi:
                return f"value {value!r} above maximum {hi!r}"
        if self.allowed is not None and value not in self.allowed:
            return f"value {value!r} not in allowed set {list(self.allowed)!r}"
        return None


class Cell:
    """A catalog primitive: typed ports, parameters, idiom and timing hooks.

    ``idioms`` and ``timing`` are freeform-mapping *hooks*: the IR carries them
    opaquely for the catalog matcher (DESIGN section 6) and the future
    constraint emitter (DESIGN section 2, clock domains) respectively.
    """

    def __init__(
        self,
        name: str,
        ports: Sequence[Port] = (),
        params: Sequence[Param] = (),
        idioms: Mapping[str, Any] | None = None,
        timing: Mapping[str, Any] | None = None,
    ) -> None:
        if not name:
            raise IRError("cell name must be non-empty")
        self.name = name
        self.ports: dict[str, Port] = {}
        for port in ports:
            if port.name in self.ports:
                raise IRError(f"cell {name!r}: duplicate port {port.name!r}")
            self.ports[port.name] = port
        self.params: dict[str, Param] = {}
        for param in params:
            if param.name in self.params:
                raise IRError(f"cell {name!r}: duplicate param {param.name!r}")
            self.params[param.name] = param
        self.idioms: dict[str, Any] = dict(idioms or {})
        self.timing: dict[str, Any] = dict(timing or {})

    def resolve_params(self, bindings: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge *bindings* over defaults; return (resolved, diagnostics)."""
        diagnostics: list[str] = []
        resolved: dict[str, Any] = {}
        for pname in sorted(set(bindings) - set(self.params)):
            diagnostics.append(f"unknown parameter {pname!r} (cell {self.name!r})")
        for pname in sorted(self.params):
            param = self.params[pname]
            if pname in bindings:
                value = bindings[pname]
                problem = param.check(value)
                if problem is not None:
                    diagnostics.append(f"parameter {pname!r}: {problem} (cell {self.name!r})")
                    continue
                resolved[pname] = value
            elif param.default is not None:
                resolved[pname] = param.default
            else:
                diagnostics.append(
                    f"parameter {pname!r} has no binding and no default (cell {self.name!r})"
                )
        return resolved, diagnostics

    def __repr__(self) -> str:
        return f"Cell({self.name!r})"


@dataclass(frozen=True)
class Instance:
    """An instantiation of a Cell or sub-Design inside a Design."""

    name: str
    target: Cell | Design
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Net:
    """A named net: a set of (instance_name, port_name) endpoints.

    ``BOUNDARY`` ("") as instance name refers to the owning Design's own
    boundary port. ``domain`` is the clock/timing-domain annotation hook.
    """

    name: str
    endpoints: set[tuple[str, str]] = field(default_factory=set)
    domain: str | None = None


class Design:
    """A hierarchical design: instances, nets, and IOB/connector boundary ports."""

    def __init__(self, name: str, ports: Sequence[Port] = ()) -> None:
        if not name:
            raise IRError("design name must be non-empty")
        self.name = name
        self.ports: dict[str, Port] = {}
        for port in ports:
            if port.name in self.ports:
                raise IRError(f"design {name!r}: duplicate boundary port {port.name!r}")
            self.ports[port.name] = port
        self.instances: dict[str, Instance] = {}
        self.nets: dict[str, Net] = {}

    def add_instance(
        self,
        name: str,
        target: Cell | Design,
        params: Mapping[str, Any] | None = None,
    ) -> Instance:
        if not name or "/" in name:
            raise IRError(f"invalid instance name {name!r} ('/' is the hierarchy separator)")
        if name in self.instances:
            raise IRError(f"design {self.name!r}: duplicate instance {name!r}")
        if isinstance(target, Design) and params:
            raise IRError(
                f"design {self.name!r}: instance {name!r}: sub-Design instances "
                "do not take parameter bindings"
            )
        if not isinstance(target, (Cell, Design)):
            raise IRError(f"instance target must be a Cell or Design, got {type(target).__name__}")
        inst = Instance(name=name, target=target, params=dict(params or {}))
        self.instances[name] = inst
        return inst

    def connect(
        self,
        net_name: str,
        *endpoints: tuple[str, str],
        domain: str | None = None,
    ) -> Net:
        """Attach endpoints to net *net_name*, creating the net if needed.

        Each endpoint is ``(instance_name, port_name)``; use ``BOUNDARY`` as
        the instance name for this Design's own boundary ports.
        """
        if not net_name:
            raise IRError("net name must be non-empty")
        net = self.nets.get(net_name)
        if net is None:
            net = Net(name=net_name)
            self.nets[net_name] = net
        if domain is not None:
            net.domain = domain
        for inst_name, port_name in endpoints:
            if inst_name == BOUNDARY:
                if port_name not in self.ports:
                    raise IRError(
                        f"design {self.name!r}: no boundary port {port_name!r} "
                        f"(net {net_name!r})"
                    )
            else:
                inst = self.instances.get(inst_name)
                if inst is None:
                    raise IRError(
                        f"design {self.name!r}: net {net_name!r} references unknown "
                        f"instance {inst_name!r}"
                    )
                if port_name not in inst.target.ports:
                    raise IRError(
                        f"design {self.name!r}: instance {inst_name!r} "
                        f"({inst.target.name!r}) has no port {port_name!r} (net {net_name!r})"
                    )
            net.endpoints.add((inst_name, port_name))
        return net

    def __repr__(self) -> str:
        return f"Design({self.name!r}, instances={len(self.instances)}, nets={len(self.nets)})"
