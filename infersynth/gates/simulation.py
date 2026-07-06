"""The behavioral simulation gate (sim-gate v0): real, for cells with models.

This replaces the ``simulation`` SKIPPED stub for any cell that carries a
behavioral model. A cell qualifies when it ships both ``model/behavior.py``
(``make_behavior(params) -> Block``) and ``testbench/tb.py`` (``PARAMS`` +
``make_testbench(params) -> Testbench``). The gate:

1. loads both modules by file path (importlib, namespaced per cell — no
   ``sys.path`` pollution);
2. resolves the testbench's ``PARAMS`` operating point through ``bind_cell``
   (validates against idiom constraints, applies defaults) and derives the
   resolved idiom params the model is written against;
3. builds the DUT block, checks its ports match cell.yaml exactly;
4. runs each testbench scenario deterministically on the v0 kernel and turns
   every check into a diagnostic.

Cells without ``model/behavior.py`` or ``testbench/tb.py`` (structural-only
cells: connectors, decoupling) return SKIPPED, loudly — the honest signal that
this cell is unverified by simulation, per DESIGN.md §4.

DESIGN.md §4: the end-state simulation tier is emitted SystemC-AMS; this is the
graceful bootstrap below it. SELECTION.md §8: the run is deterministic — fixed
dt, name-sorted iteration, no wall-clock, no randomness.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

from infersynth.bind import BindingError, bind_cell
from infersynth.catalog.loader import CellPackage
from infersynth.gates.runner import GateResult
from infersynth.sim import Simulation
from infersynth.sim.testbench import Testbench

__all__ = ["simulation_cell_gate", "run_cell_simulation"]

_GATE = "simulation"
# Loud, matches the existing stub wording register (DESIGN.md §4: never silent).
_SKIP_NO_MODEL = (
    "not verified by simulation: cell ships no {missing} "
    "(structural-only cell — behavioral simulation is not applicable)"
)


def _load_module(path: Path, mod_name: str) -> ModuleType:
    """Import *path* as *mod_name* without touching ``sys.path``."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _ns(cell: CellPackage, leaf: str) -> str:
    """A collision-free module name for one cell's behavior/tb file."""
    safe = re.sub(r"[^0-9A-Za-z]+", "_", cell.key)
    return f"infersynth._sim_cells.{safe}.{leaf}"


def _resolved_idiom_params(cell: CellPackage, raw: dict) -> dict:
    """Merge *raw* over idiom-param defaults (post-``bind_cell`` resolved view)."""
    resolved: dict[str, object] = {}
    for pname, schema in cell.idiom_params.items():
        if pname in raw:
            resolved[pname] = raw[pname]
        elif schema.get("default") is not None:
            resolved[pname] = schema.get("default")
    return resolved


def run_cell_simulation(cell: CellPackage) -> list[tuple[str, bool, str]]:
    """Run every testbench scenario for *cell*; return ``(label, ok, detail)`` rows.

    Deterministic: modules are (re)loaded fresh, a fresh DUT and
    :class:`Simulation` are built per run. Raises on a structural error
    (bad params, port mismatch, malformed graph); check *failures* are returned
    as rows, not raised.
    """
    behavior_mod = _load_module(cell.path / "model" / "behavior.py", _ns(cell, "behavior"))
    tb_mod = _load_module(cell.path / "testbench" / "tb.py", _ns(cell, "tb"))

    raw = dict(getattr(tb_mod, "PARAMS", {}) or {})
    # Validate the operating point through the binder (raises BindingError).
    bind_cell(cell, raw)
    resolved = _resolved_idiom_params(cell, raw)

    testbench: Testbench = tb_mod.make_testbench(resolved)

    # DUT ports must match cell.yaml ports exactly.
    probe = behavior_mod.make_behavior(resolved)
    dut_ports = set(probe.inputs) | set(probe.outputs)
    cell_ports = set(cell.ports)
    if dut_ports != cell_ports:
        raise ValueError(
            f"behavior ports {sorted(dut_ports)} do not match cell.yaml ports "
            f"{sorted(cell_ports)}"
        )

    rows: list[tuple[str, bool, str]] = []
    for run in testbench.runs:
        sim = Simulation(
            dt=run.dt if run.dt is not None else testbench.dt,
            n_steps=run.n_steps if run.n_steps is not None else testbench.n_steps,
        )
        for stim in run.stimulus:
            sim.add(stim.block, stim.bindings)
        dut = behavior_mod.make_behavior(resolved)  # fresh DUT per run
        sim.add(dut, dict(testbench.dut_bindings))
        traces = sim.run()
        for check in run.checks:
            ok, detail = check.evaluate(traces)
            rows.append((f"{run.name}/{check.name}", ok, detail))
    return rows


def simulation_cell_gate(cell: CellPackage) -> GateResult:
    """The cell-level simulation gate — PASS/FAIL/SKIPPED for one cell."""
    if not (cell.path / "model" / "behavior.py").is_file():
        return GateResult.skipped(_GATE, _SKIP_NO_MODEL.format(missing="model/behavior.py"))
    if not (cell.path / "testbench" / "tb.py").is_file():
        return GateResult.skipped(_GATE, _SKIP_NO_MODEL.format(missing="testbench/tb.py"))

    try:
        rows = run_cell_simulation(cell)
    except BindingError as exc:
        return GateResult.failed(_GATE, f"testbench params rejected by binder: {exc}")
    except Exception as exc:  # a crashing sim is a failing gate
        return GateResult.failed(_GATE, f"{type(exc).__name__}: {exc}")

    if not rows:
        return GateResult.skipped(_GATE, "testbench declared no checks")

    diagnostics = tuple(
        f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}" for label, ok, detail in rows
    )
    if all(ok for _, ok, _ in rows):
        return GateResult.passed(_GATE, *diagnostics)
    return GateResult.failed(_GATE, *diagnostics)
