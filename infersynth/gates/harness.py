"""Harness generator for the cell-CI gate (BUILD_PLAN WP4).

The oracle can only run ERC on a *complete* design, but a cell fragment is
intentionally incomplete: its ports are hierarchical labels with no drivers.
The harness wraps one cell instance in the smallest legal parent that makes
ERC meaningful — and it does so by CONSUMING the WP3 emitter (``new_design`` +
``instantiate``) and then TEXT-APPENDING the harness scaffolding. The *checks*
(``gates/erc.py``, ``gates/netlist.py``) share no code with the writer.

Harness scaffolding, appended to the emitted root:

1. a ``(sheet ...)`` **pin** per cell port (matching the fragment's
   hierarchical labels, so the hierarchy is complete → no ``hier_label_mismatch``),
2. a coincident ``(global_label ...)`` per port so every parent-side sheet pin
   is named and connected (→ no ``pin_not_connected``),
3. a coincident ``power:PWR_FLAG`` per **power** port and per **in**-direction
   port, marking the net as intentionally driven (→ no ``power_pin_not_driven``
   / ``pin_not_driven``).

The sheet box is enlarged to fit all pins on its border (the emitter's fixed
25.4 mm box only holds ~9 pins). Driver symbols get KiCad's ``#``-prefixed
virtual refs, which ``gates/netlist.py`` filters, so the exported partition is
exactly the cell's pins and stays golden-equivalent.

Parameter binding for the harness (WP4 rule): for each idiom param, use the
first ``allowed`` value if the param is enumerated (e.g. ``channels=4``), else
its declared ``default``, else the **midpoint of its range** (gain params lack
defaults). A param with none of allowed/default/range raises :class:`HarnessError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from infersynth.compile_kicad import emit
from infersynth.compile_kicad.wiring_text import (
    PIN_STRIDE as _PIN_STRIDE,
)
from infersynth.compile_kicad.wiring_text import (
    PWR_FLAG_LIB as _PWR_FLAG_LIB,
)
from infersynth.compile_kicad.wiring_text import (
    SHAPE as _SHAPE,
)
from infersynth.compile_kicad.wiring_text import (
    global_label as _global_label,
)
from infersynth.compile_kicad.wiring_text import (
    pwr_flag as _pwr_flag,
)
from infersynth.compile_kicad.wiring_text import (
    sheet_pin as _sheet_pin,
)

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["HarnessError", "HarnessResult", "harness_params", "generate_harness"]


class HarnessError(ValueError):
    """Raised when a harness cannot be generated for a cell."""


@dataclass(frozen=True)
class HarnessResult:
    """A generated harness design."""

    root: Path  # the harness root .kicad_sch to run ERC / netlist on
    params: dict[str, object]  # the params the DUT was bound with
    instname: str


def harness_params(cell: CellPackage) -> dict[str, object]:
    """Choose one value per idiom param for the harness (WP4 rule).

    Priority: first ``allowed`` value → declared ``default`` → midpoint of
    ``range``. A param with none raises :class:`HarnessError`.
    """
    params: dict[str, object] = {}
    for pname, schema in cell.idiom_params.items():
        allowed = schema.get("allowed")
        if allowed:
            params[pname] = allowed[0]
            continue
        if schema.get("default") is not None:
            params[pname] = schema["default"]
            continue
        prange = schema.get("range")
        if prange and prange[0] is not None and prange[1] is not None:
            mid = (prange[0] + prange[1]) / 2
            params[pname] = int(mid) if schema.get("type") == "int" else mid
            continue
        raise HarnessError(
            f"cell {cell.key}: idiom param {pname!r} has no allowed/default/range — "
            "cannot pick a harness value"
        )
    return params


def generate_harness(
    cell: CellPackage, design_dir: str | Path, instname: str = "dut"
) -> HarnessResult:
    """Emit a single-cell harness design into *design_dir*; return its root.

    Consumes the WP3 emitter to instantiate the cell, then appends the harness
    scaffolding (sheet pins + labels + PWR_FLAGs) as text surgery on the root.
    """
    design_dir = Path(design_dir)
    params = harness_params(cell)
    root = emit.new_design(design_dir, "harness")
    emit.instantiate(cell, params, instname, design_dir, root)

    text = root.read_text()

    # 1. inject the PWR_FLAG library symbol into the (empty) lib_symbols.
    if "\t(lib_symbols)" not in text:  # pragma: no cover - emitter contract
        raise HarnessError("emitted root has no empty (lib_symbols) to populate")
    text = text.replace("\t(lib_symbols)", f"\t(lib_symbols\n{_PWR_FLAG_LIB}\n\t)", 1)

    # locate the single (sheet ...) block's origin.
    m = re.search(r"\(sheet\n\t\t\(at ([\d.]+) ([\d.]+)\)", text)
    if m is None:  # pragma: no cover - emitter always writes one sheet
        raise HarnessError("emitted root has no (sheet ...) block to wrap")
    sx, sy = float(m.group(1)), float(m.group(2))

    # 2. enlarge the sheet box so all pins fit on its left border.
    nports = len(cell.ports)
    sheet_h = max(emit._SHEET_H, (nports + 1) * _PIN_STRIDE)
    text = text.replace(
        f"(size {emit._SHEET_W:g} {emit._SHEET_H:g})",
        f"(size {emit._SHEET_W:g} {sheet_h:g})",
        1,
    )

    # 3. build sheet pins + parent-side labels + power/in drivers.
    pins: list[str] = []
    scaffolding: list[str] = []
    y = sy + _PIN_STRIDE
    n_flag = 0
    for pname, spec in cell.ports.items():
        shape = _SHAPE[spec["direction"]]
        pins.append(_sheet_pin(pname, shape, sx, y))
        scaffolding.append(_global_label(pname, shape, sx, y))
        if spec["kind"] == "power" or spec["direction"] == "in":
            n_flag += 1
            scaffolding.append(_pwr_flag(n_flag, sx, y))
        y += _PIN_STRIDE

    text = text.replace("\t\t(instances\n", "".join(pins) + "\t\t(instances\n", 1)
    text = text.replace("\t(sheet_instances\n", "".join(scaffolding) + "\t(sheet_instances\n", 1)

    root.write_text(text)
    return HarnessResult(root=root, params=params, instname=instname)
