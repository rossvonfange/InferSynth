"""Design-level behavioral composition (design-simulation gate, SEED_PLAN §1 crit 3).

The per-cell ``simulation`` gate (``infersynth/gates/simulation.py``) proves ONE
cell's behavior against ITS testbench. This module lifts the same v0 dataflow
kernel to the **whole synthesized design**: it wires every instantiated cell's
``model/behavior.py`` block together *by the WiringPlan's nets* and runs the
composed graph end-to-end, so the design's behavioral chain (bridge → in-amp →
filter → buffer → driver, for BridgeSense-1) is verified as a unit — not just
cell by cell.

Composition rules (documented, deterministic — SELECTION.md §8)
----------------------------------------------------------------
* **Nets are electrical nodes.** A :class:`~infersynth.netflow.plan.WiringPlan`
  may split one physical node across several 2-member nets (e.g. a virtual-
  ground ``VGND`` output feeding three sinks becomes three ``f_MID_*`` nets, all
  sharing the ``(mid, VGND)`` endpoint). We union endpoints (union-find) so
  every net sharing an endpoint collapses to ONE simulation signal. The signal
  name is the lexicographically smallest net name in the component — which is a
  rail's own UPPERCASE name for rail components (rails sort before the
  lowercase ``f_``/``n_``/``c_`` signal-net names), so rails keep their names.
* **A cell with ``model/behavior.py`` becomes one Block per instance,**
  ``make_behavior(resolved_params)``, each port bound to its net's signal.
* **A cell WITHOUT ``model/behavior.py`` is a structural PASSTHROUGH** (a
  connector or a bypass cap has no transfer function): its ports are just shared
  signals on their nets, contributing no block. Recorded loudly in
  :attr:`DesignSimBuild.passthrough`. A non-structural cell missing a behavior
  (an active cell that *ought* to model but does not) is instead recorded in
  :attr:`DesignSimBuild.missing_behavior` — the gate SKIPs on those, naming them
  (never a silent partial sim, DESIGN.md §4).
* **RAIL nets become DC sources** at the voltages of the required ``rails``
  mapping (a rail with no declared voltage is an ERROR naming it). A behavior
  OUTPUT that lands on a rail net (e.g. the regulator's ``VOUT``) is redirected
  to a private sink signal so the declared rail voltage is the SINGLE driver
  (the kernel forbids two drivers per signal); the cell still runs, its rail
  output simply superseded by the testbench's ideal rail.
* **Unwired ports float.** A port in NO net binds to a private per-port signal
  (holds 0.0, drives nothing) and is listed loudly in
  :attr:`DesignSimBuild.floating_ports`.

The build is a pure function of (result, catalog, rails, dt, n_steps): two runs
produce byte-identical traces (no wall-clock, no randomness; every iteration is
name-sorted, inheriting the kernel's determinism).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import ModuleType

from infersynth.catalog import Catalog, CellPackage
from infersynth.sim.kernel import Simulation
from infersynth.sim.sources import DCSource

__all__ = ["DesignSimBuild", "DesignSimError", "build_design_sim"]

#: Cell technologies that are inherently structural (no signal transfer): a
#: connector's electromechanical mating interface, a passive bypass/decoupling
#: part. A missing behavior on one of these is EXPECTED (passthrough), not a gap.
_STRUCTURAL_TECHNOLOGIES = frozenset({"electromechanical", "passive"})


class DesignSimError(ValueError):
    """Raised on a malformed design-sim request (undeclared rail, etc.)."""


def _load_make_behavior(cell: CellPackage):
    """Import a cell's ``model/behavior.py`` and return its ``make_behavior``."""
    path = cell.path / "model" / "behavior.py"
    safe = re.sub(r"[^0-9A-Za-z]+", "_", cell.key)
    mod_name = f"infersynth._sim_design.{safe}.behavior"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module.make_behavior


def _is_structural(cell: CellPackage) -> bool:
    """True when a behavior-less *cell* is an expected structural passthrough.

    Structural = an electromechanical connector or a purely passive part, or a
    cell whose every port is ``direction: passive`` (no active in/out transfer).
    """
    tech = (cell.manifest or {}).get("technology")
    if tech in _STRUCTURAL_TECHNOLOGIES:
        return True
    ports = cell.ports or {}
    return bool(ports) and all(
        spec.get("direction") == "passive" for spec in ports.values()
    )


class _UnionFind:
    """Endpoint union-find: collapse nets that share an ``(inst, port)`` node."""

    def __init__(self) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic: the smaller endpoint becomes the root.
            hi, lo = (ra, rb) if ra > rb else (rb, ra)
            self._parent[hi] = lo


@dataclass(frozen=True)
class DesignSimBuild:
    """The composed design simulation plus its structural bookkeeping."""

    simulation: Simulation
    #: net name -> the composed signal name it collapsed into
    signal_of_net: dict[str, str]
    #: (instname, port) -> composed signal name
    signal_of_port: dict[tuple[str, str], str]
    #: declared rail voltages actually applied (rail signal name -> volts)
    rails: dict[str, float]
    #: instnames simulated as structural passthrough (missing behavior, expected)
    passthrough: tuple[str, ...]
    #: instnames missing a behavior that is NOT structural (gate SKIP trigger)
    missing_behavior: tuple[str, ...]
    #: behavior INPUT ports wired to no net (float at 0.0) — loud
    floating_ports: tuple[tuple[str, str], ...]
    #: instnames contributing a behavior block
    behaviors: tuple[str, ...]
    #: signals with a design-internal driver (rail sources + behavior outputs)
    driven_signals: frozenset[str] = field(default_factory=frozenset)

    def resolve_net(self, net_name: str) -> str:
        """Map a WiringPlan net name to its composed signal name (identity if
        the name is already a composed signal, e.g. a rail name)."""
        if net_name in self.signal_of_net:
            return self.signal_of_net[net_name]
        if net_name in set(self.signal_of_net.values()) or net_name in self.rails:
            return net_name
        raise DesignSimError(
            f"unknown net {net_name!r} (known nets: {sorted(self.signal_of_net)})"
        )


def build_design_sim(
    result_like,
    catalog: Catalog,
    rails: Mapping[str, float],
    dt: float,
    n_steps: int,
) -> DesignSimBuild:
    """Compose one :class:`~infersynth.sim.kernel.Simulation` for a whole design.

    *result_like* needs ``.instantiated`` (cells with ``cell_key``, ``instname``,
    resolved ``params``) and ``.wiring_plan`` (the net truth). *rails* maps every
    rail net name to a DC voltage (missing → :class:`DesignSimError`). The
    returned :class:`DesignSimBuild` carries the ready-to-run simulation (rail DC
    sources + behavior blocks already added) and the net↔signal maps a testbench
    uses to place stimuli and read checks.
    """
    plan = result_like.wiring_plan
    if plan is None:
        raise DesignSimError("result has no wiring_plan (synthesize with wiring=True)")
    instances = list(result_like.instantiated)

    # --- 1. union-find over endpoints: nets sharing a node are one signal ---
    uf = _UnionFind()
    net_members: dict[str, tuple[tuple[str, str], ...]] = {}
    for net in plan.nets:
        net_members[net.name] = net.members
        members = list(net.members)
        for m in members:
            uf.find(m)
        for other in members[1:]:
            uf.union(members[0], other)

    # canonical signal name per component = smallest net name touching it
    comp_net_names: dict[tuple[str, str], list[str]] = {}
    for name, members in net_members.items():
        if not members:
            continue
        root = uf.find(members[0])
        comp_net_names.setdefault(root, []).append(name)
    canonical: dict[tuple[str, str], str] = {
        root: min(names) for root, names in comp_net_names.items()
    }

    signal_of_net: dict[str, str] = {}
    for name, members in net_members.items():
        if members:
            signal_of_net[name] = canonical[uf.find(members[0])]

    signal_of_port: dict[tuple[str, str], str] = {}
    for net in plan.nets:
        sig = signal_of_net[net.name]
        for m in net.members:
            signal_of_port[m] = sig

    rail_signal_names = {
        signal_of_net[n.name] for n in plan.nets if n.kind == "rail"
    }

    # --- 2. validate declared rails: every rail net must carry a voltage ---
    missing = sorted(r for r in rail_signal_names if r not in rails)
    if missing:
        raise DesignSimError(
            f"rail net(s) {missing} have no declared voltage in the testbench "
            f"'rails' mapping (declared: {sorted(rails)})"
        )
    extra = sorted(set(rails) - rail_signal_names)
    if extra:
        raise DesignSimError(
            f"testbench 'rails' names {extra} that are not rail nets in the plan "
            f"(rail nets: {sorted(rail_signal_names)})"
        )

    sim = Simulation(dt=dt, n_steps=int(n_steps))

    # --- 3. rail DC sources (single driver per rail) ---
    driven: set[str] = set()
    for rail_name in sorted(rail_signal_names):
        sim.add(
            DCSource(name=f"__rail__{rail_name}", value=float(rails[rail_name])),
            {"out": rail_name},
        )
        driven.add(rail_name)

    # --- 4. one Block per behavior cell; passthrough / missing bookkeeping ---
    passthrough: list[str] = []
    missing_behavior: list[str] = []
    behaviors: list[str] = []
    floating: list[tuple[str, str]] = []
    for inst in sorted(instances, key=lambda i: i.instname):
        cell = catalog.cells.get(inst.cell_key)
        if cell is None:  # pragma: no cover - synthesize only names catalog cells
            missing_behavior.append(inst.instname)
            continue
        has_behavior = (cell.path / "model" / "behavior.py").is_file()
        if not has_behavior:
            (passthrough if _is_structural(cell) else missing_behavior).append(
                inst.instname
            )
            continue

        make_behavior = _load_make_behavior(cell)
        block = make_behavior(dict(inst.params))
        block.name = inst.instname  # unique, deterministic tie-break key
        bindings: dict[str, str] = {}
        for port in (*block.inputs, *block.outputs):
            sig = signal_of_port.get((inst.instname, port))
            if sig is None:
                # unwired port: private float signal (holds 0.0, drives nothing)
                sig = f"__float__{inst.instname}__{port}"
                if port in block.inputs:
                    floating.append((inst.instname, port))
            elif port in block.outputs and sig in rail_signal_names:
                # behavior output on a rail: rail DC source is the sole driver
                sig = f"__railsink__{inst.instname}__{port}"
            bindings[port] = sig
            if port in block.outputs:
                driven.add(sig)
        sim.add(block, bindings)
        behaviors.append(inst.instname)

    return DesignSimBuild(
        simulation=sim,
        signal_of_net=signal_of_net,
        signal_of_port=signal_of_port,
        rails={r: float(rails[r]) for r in sorted(rail_signal_names)},
        passthrough=tuple(sorted(passthrough)),
        missing_behavior=tuple(sorted(missing_behavior)),
        floating_ports=tuple(sorted(floating)),
        behaviors=tuple(behaviors),
        driven_signals=frozenset(driven),
    )
