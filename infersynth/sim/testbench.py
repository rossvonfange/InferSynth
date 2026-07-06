"""Testbench convention types for the v0 sim gate.

A cell's ``testbench/tb.py`` exports ``make_testbench(params) -> Testbench``
(and a module-level ``PARAMS`` operating point — see docs/SIM.md). A
:class:`Testbench` describes the harness around the device-under-test (the DUT
being the cell's ``model/behavior.py`` block, inserted by the gate):

* ``dut_bindings`` — DUT port -> signal name, shared by every run.
* ``runs`` — one or more :class:`Run` scenarios (e.g. linear, then overdrive),
  each with its own stimulus blocks and checks. Runs are independent: the gate
  builds a fresh DUT and fresh :class:`~infersynth.sim.kernel.Simulation` per
  run, so a stateful DUT never leaks state between scenarios.

The gate owns time (``dt``, ``n_steps``); a run may override either.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from infersynth.sim.checks import Check
from infersynth.sim.kernel import Block

__all__ = ["Stimulus", "Run", "Testbench"]


@dataclass(frozen=True)
class Stimulus:
    """A stimulus block plus its port->signal binding (identity default)."""

    block: Block
    bindings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    """One scenario: named stimulus + checks (dt/n_steps default to the tb's)."""

    name: str
    stimulus: tuple[Stimulus, ...]
    checks: tuple[Check, ...]
    dt: float | None = None
    n_steps: int | None = None


@dataclass(frozen=True)
class Testbench:
    """A cell's behavioral testbench: DUT wiring, default timing, and runs."""

    dt: float
    n_steps: int
    #: DUT (behavior block) port name -> signal name, shared across runs
    dut_bindings: Mapping[str, str]
    runs: tuple[Run, ...]
    #: informational rail values (VCC/VEE/…); the sources still drive them
    rails: Mapping[str, float] = field(default_factory=dict)
