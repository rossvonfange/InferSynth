"""The AMS simulation gate (``ams-simulation``): emitted SystemC-AMS, run real.

Sits alongside the v0 ``simulation`` gate (docs/SIM.md, DESIGN.md §7 gate 1) and
realizes the DESIGN.md §4 end-state tier — emitted SystemC-AMS compiled + run by
a real toolchain — for any cell that carries a ``model/ams/<cell>.h``. Three
loud, distinct outcomes (never a silent pass — DESIGN.md §4):

* cell ships no ``model/ams/`` → SKIPPED (falls back to the v0 ``simulation``
  tier; distinct wording from the toolchain skip);
* SystemC-AMS toolchain absent → SKIPPED (behavioral coverage still comes from
  the v0 ``simulation`` gate on this machine);
* toolchain present + cell has an AMS model → emit → compile → run → parse the
  CSV trace → reuse ``infersynth/sim/checks.py`` math → PASS/FAIL.

The emitter is a pure function of the cell; only the compile/run path needs the
toolchain, so emission and check wiring are exercised everywhere while the real
run stays behind the ``@pytest.mark.sysc_ams`` skip.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from infersynth.catalog.loader import CellPackage
from infersynth.emit_sysc_ams import (
    AmsKernelError,
    SystemCAmsKernel,
    ams_header_path,
    detect_toolchain,
    emit_cell,
)
from infersynth.gates.runner import GateResult

__all__ = ["ams_simulation_cell_gate", "AMS_GATE"]

AMS_GATE = "ams-simulation"

_SKIP_NO_MODEL = (
    "not verified by AMS simulation: cell ships no model/ams/ "
    "(no SystemC-AMS model to emit — behavioral coverage falls back to the v0 "
    "`simulation` gate)"
)
_SKIP_NO_TOOLCHAIN = (
    "AMS simulation SKIPPED: SystemC-AMS toolchain absent ({why}) — the emitted "
    "testbench cannot be compiled here; run the v0 `simulation` gate for "
    "behavioral coverage on this machine"
)


def ams_simulation_cell_gate(cell: CellPackage) -> GateResult:
    """PASS/FAIL/SKIPPED for one cell's emitted-SystemC-AMS testbench."""
    if ams_header_path(cell) is None:
        return GateResult.skipped(AMS_GATE, _SKIP_NO_MODEL)

    toolchain = detect_toolchain()
    if not toolchain.available:
        return GateResult.skipped(
            AMS_GATE, _SKIP_NO_TOOLCHAIN.format(why=toolchain.why_unavailable())
        )

    emitted = emit_cell(cell)
    if emitted is None:  # pragma: no cover - guarded by ams_header_path above
        return GateResult.skipped(AMS_GATE, _SKIP_NO_MODEL)

    try:  # pragma: no cover - only runs where a real toolchain exists
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, contents in emitted.files.items():
                (work / name).write_text(contents)
            kernel = SystemCAmsKernel(toolchain=toolchain)
            exe = kernel.compile(work, target="sim")
            csv_path = work / emitted.output_csv
            kernel.run(exe, csv_path)
            traces = kernel.parse(csv_path)
    except AmsKernelError as exc:  # pragma: no cover - needs toolchain
        return GateResult.failed(AMS_GATE, f"AMS toolchain error: {exc}")
    except Exception as exc:  # pragma: no cover - needs toolchain
        return GateResult.failed(AMS_GATE, f"{type(exc).__name__}: {exc}")

    rows = [  # pragma: no cover - needs toolchain
        (check.name, *check.evaluate(traces)) for check in emitted.checks
    ]
    if not rows:  # pragma: no cover - needs toolchain
        return GateResult.skipped(AMS_GATE, "emitted testbench declared no checks")
    diagnostics = tuple(  # pragma: no cover - needs toolchain
        f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}" for name, ok, detail in rows
    )
    if all(ok for _, ok, _ in rows):  # pragma: no cover - needs toolchain
        return GateResult.passed(AMS_GATE, *diagnostics)
    return GateResult.failed(AMS_GATE, *diagnostics)  # pragma: no cover - needs toolchain
