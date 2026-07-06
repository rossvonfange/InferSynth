"""Deterministic fixed-timestep dataflow kernel (the sim-gate v0 bootstrap).

DESIGN.md §4 names SystemC-AMS *by emission* as the end-state simulation tier;
this module is the documented **graceful-bootstrap tier below it** — a tiny,
zero-dependency, pure-Python engine that runs a cell's behavioral model against
its testbench so the ``simulation`` gate can be REAL for cells that carry
behavioral models, today, with no C++ toolchain.

Shape: this is a **TDF-style (timed dataflow)** kernel, deliberately NOT the
discrete-event MMIO kernel of ``fai-recon/fai_recon/pysysc`` (RECON_HARVEST §1).
recon's kernel schedules digital register writes on a min-heap ns clock; ours
advances a fixed analog timestep and propagates float samples through a block
DAG. We adopt recon's *style* — a frozen contract the models are written
against, and a documented swap seam — but the engine is our own.

Concepts
--------
* **Signal**: a named ``float`` stream. The kernel records one sample per step.
* **Block**: declares ``inputs`` / ``outputs`` (port-name tuples) and a pure
  ``step(t, dt, inputs: dict[str, float]) -> dict[str, float]``. Stateful blocks
  (integrators, filters) keep state on ``self``; our v0 op-amp blocks and
  sources are stateless, which makes determinism free.
* **Binding**: each block port maps to a signal name (identity by default). This
  is the netlist — the AMS analogue of binding a port to a node.

The kernel topologically orders blocks (Kahn, **sorted by block name** at every
tie so the order is a pure function of the graph), runs ``n_steps`` at fixed
``dt``, and returns ``{signal: [sample, ...]}``. A block that reads a signal
another block writes runs *after* it within each step; the graph must be a DAG
(feedback is modeled inside a block — the ideal op-amp already folds its
feedback into the gain — or broken with an explicit delay element, exactly as
TDF requires).

Swap seam to SystemC-AMS (mirrors recon's Python-kernel → Accellera-kernel swap
table)
--------------------------------------------------------------------------------
This kernel is the localized swap point. When the SystemC-AMS emitter lands
(DESIGN.md §4 end-state), the *contract* the behavioral models are written
against does not move — only how each piece is realized underneath:

    this v0 (pure Python)          SystemC-AMS (emitted, end-state)
    ---------------------          --------------------------------
    Block.step(t, dt, inputs)  ->  sca_tdf::sc_module::processing()
    inputs: dict[str, float]   ->  sca_tdf::sca_in<double> port reads
    return {port: value}       ->  sca_tdf::sca_out<double> writes
    Simulation(dt, n_steps)    ->  sca_tdf module timestep + sc_start()
    Binding port->signal       ->  sca_tdf::sca_signal<double> bind()
    traces dict                ->  sca_util::sca_trace() / .dat

A cell's ``model/behavior.py`` (which only *uses* this contract) is unchanged
across the swap; only this module is replaced. That is the same discipline
recon documents for its TLM ``ctx`` contract (RECON_HARVEST §1).

Repeatability (SELECTION.md §8, BINDING): no wall-clock, no randomness, no set
iteration in ordered positions. Every ordering is name-sorted; ``import random``
and ``import time`` never appear in this package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "Block",
    "SimulationError",
    "BoundBlock",
    "Simulation",
    "topological_order",
]


class SimulationError(ValueError):
    """Raised on a malformed dataflow graph (double driver, cycle, bad binding)."""


@runtime_checkable
class Block(Protocol):
    """The behavioral-block contract every model and source implements.

    * ``name`` — unique within a :class:`Simulation`; the deterministic
      topological tie-break key.
    * ``inputs`` / ``outputs`` — port-name tuples. ``step`` receives exactly the
      ``inputs`` ports and MUST return exactly the ``outputs`` ports.
    * ``step`` — pure w.r.t. its arguments and ``self`` state; no wall-clock, no
      randomness. Stateful blocks keep state on ``self`` and mutate it here.
    """

    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> Mapping[str, float]: ...


@dataclass(frozen=True)
class BoundBlock:
    """A block plus its port->signal binding (identity-filled for every port)."""

    block: Block
    #: port name -> signal name, defined for every input and output port
    bindings: dict[str, str]

    @property
    def in_signals(self) -> tuple[str, ...]:
        return tuple(self.bindings[p] for p in self.block.inputs)

    @property
    def out_signals(self) -> tuple[str, ...]:
        return tuple(self.bindings[p] for p in self.block.outputs)


def topological_order(blocks: list[BoundBlock]) -> list[BoundBlock]:
    """Return *blocks* in dependency order (producers before consumers).

    Kahn's algorithm with a **name-sorted** ready set at every step, so the
    result is a pure function of the graph (SELECTION.md §8). Raises
    :class:`SimulationError` on a double-driven signal or a cycle.
    """
    # signal -> the single block that drives it (double-drive is an error)
    driver: dict[str, str] = {}
    by_name: dict[str, BoundBlock] = {}
    for bb in sorted(blocks, key=lambda b: b.block.name):
        if bb.block.name in by_name:
            raise SimulationError(f"duplicate block name {bb.block.name!r}")
        by_name[bb.block.name] = bb
        for sig in bb.out_signals:
            if sig in driver:
                raise SimulationError(
                    f"signal {sig!r} is driven by both {driver[sig]!r} and "
                    f"{bb.block.name!r} (a signal may have at most one driver)"
                )
            driver[sig] = bb.block.name

    # edges: block -> blocks that consume its outputs
    remaining: dict[str, int] = {}  # block name -> unmet dependency count
    consumers: dict[str, list[str]] = {n: [] for n in by_name}
    for name, bb in by_name.items():
        deps = {driver[s] for s in bb.in_signals if s in driver and driver[s] != name}
        remaining[name] = len(deps)
        for d in deps:
            consumers[d].append(name)

    ready = sorted(n for n, c in remaining.items() if c == 0)
    order: list[BoundBlock] = []
    while ready:
        name = ready.pop(0)
        order.append(by_name[name])
        newly_ready: list[str] = []
        for c in consumers[name]:
            remaining[c] -= 1
            if remaining[c] == 0:
                newly_ready.append(c)
        # re-sort the frontier so ties always break by name, never by discovery
        ready = sorted(ready + newly_ready)

    if len(order) != len(by_name):
        stuck = sorted(n for n, c in remaining.items() if c > 0)
        raise SimulationError(
            f"dataflow graph has a cycle among blocks {stuck} "
            "(feedback must be folded into a block or broken by a delay element)"
        )
    return order


@dataclass
class Simulation:
    """A fixed-timestep dataflow run: add blocks, then :meth:`run`."""

    dt: float
    n_steps: int
    _blocks: list[BoundBlock] = field(default_factory=list)

    def add(self, block: Block, bindings: Mapping[str, str] | None = None) -> None:
        """Register *block*, mapping each port to a signal name.

        ``bindings`` maps port name -> signal name; any port absent from
        ``bindings`` binds to a signal of its own name (identity). Unknown port
        names in ``bindings`` are an error.
        """
        bindings = dict(bindings or {})
        ports = set(block.inputs) | set(block.outputs)
        unknown = sorted(set(bindings) - ports)
        if unknown:
            raise SimulationError(
                f"block {block.name!r}: binding names unknown port(s) {unknown} "
                f"(ports: {sorted(ports)})"
            )
        resolved = {p: bindings.get(p, p) for p in ports}
        self._blocks.append(BoundBlock(block=block, bindings=resolved))

    def run(self) -> dict[str, list[float]]:
        """Run ``n_steps`` steps at ``dt`` and return ``{signal: [samples]}``.

        Time for step *i* is ``i * dt`` (starts at 0.0). Within a step, blocks
        run in topological order so every consumer sees the current-step output
        of its producers; undriven signals hold 0.0.
        """
        if self.n_steps < 0:
            raise SimulationError(f"n_steps must be >= 0, got {self.n_steps}")
        order = topological_order(self._blocks)

        signals: set[str] = set()
        for bb in self._blocks:
            signals.update(bb.in_signals)
            signals.update(bb.out_signals)
        current: dict[str, float] = dict.fromkeys(sorted(signals), 0.0)
        traces: dict[str, list[float]] = {s: [] for s in sorted(signals)}

        for i in range(self.n_steps):
            t = i * self.dt
            for bb in order:
                inputs = {p: current[bb.bindings[p]] for p in bb.block.inputs}
                out = bb.block.step(t, self.dt, inputs)
                missing = set(bb.block.outputs) - set(out)
                if missing:
                    raise SimulationError(
                        f"block {bb.block.name!r} step() did not return output "
                        f"port(s) {sorted(missing)}"
                    )
                for p in bb.block.outputs:
                    current[bb.bindings[p]] = float(out[p])
            for s in traces:
                traces[s].append(current[s])
        return traces
