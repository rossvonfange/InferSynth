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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from infersynth.compile_kicad import emit

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["HarnessError", "HarnessResult", "harness_params", "generate_harness"]

# Sheet-pin / global-label shape per port direction (KiCad label shapes).
_SHAPE = {
    "in": "input",
    "out": "output",
    "inout": "bidirectional",
    "passive": "passive",
}

_PIN_STRIDE = 2.54  # mm between sheet pins along the sheet border (2 * 50-mil grid)

# The stock ``power:PWR_FLAG`` library symbol, lifted verbatim from KiCad's
# power.kicad_sym (name unqualified there; qualified at inject time) and stored
# at the 1-tab base indent it has in that file. Injected into the root's
# ``(lib_symbols)`` (one extra tab) so a PWR_FLAG instance resolves offline.
_PWR_FLAG_LIB_RAW = """\
\t(symbol "PWR_FLAG"
\t\t(power global)
\t\t(pin_numbers
\t\t\t(hide yes)
\t\t)
\t\t(pin_names
\t\t\t(offset 0)
\t\t\t(hide yes)
\t\t)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "#FLG"
\t\t\t(at 0 1.905 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "PWR_FLAG"
\t\t\t(at 0 3.81 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Description" "Special symbol for telling ERC where power comes from"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "ki_keywords" "flag power"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(symbol "PWR_FLAG_0_0"
\t\t\t(pin power_out line
\t\t\t\t(at 0 0 90)
\t\t\t\t(length 0)
\t\t\t\t(name ""
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(symbol "PWR_FLAG_0_1"
\t\t\t(polyline
\t\t\t\t(pts
\t\t\t\t\t(xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)
\t\t\t\t)
\t\t\t\t(stroke
\t\t\t\t\t(width 0)
\t\t\t\t\t(type default)
\t\t\t\t)
\t\t\t\t(fill
\t\t\t\t\t(type none)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(embedded_fonts no)
\t)"""

# Ready-to-inject: library-qualified name, one extra tab (sits in lib_symbols).
_PWR_FLAG_LIB_QUALIFIED = _PWR_FLAG_LIB_RAW.replace(
    '(symbol "PWR_FLAG"', '(symbol "power:PWR_FLAG"', 1
)
_PWR_FLAG_LIB = "\n".join("\t" + line for line in _PWR_FLAG_LIB_QUALIFIED.splitlines())


class HarnessError(ValueError):
    """Raised when a harness cannot be generated for a cell."""


@dataclass(frozen=True)
class HarnessResult:
    """A generated harness design."""

    root: Path  # the harness root .kicad_sch to run ERC / netlist on
    params: dict[str, object]  # the params the DUT was bound with
    instname: str


def _new_uuid() -> str:
    return str(uuid.uuid4())


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


def _global_label(name: str, shape: str, x: float, y: float) -> str:
    return (
        f'\t(global_label "{name}"\n'
        f"\t\t(shape {shape})\n"
        f"\t\t(at {x:g} {y:g} 180)\n"
        f"\t\t(fields_autoplaced yes)\n"
        f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n"
        f"\t\t\t(justify right)\n\t\t)\n"
        f'\t\t(uuid "{_new_uuid()}")\n'
        f"\t)\n"
    )


def _sheet_pin(name: str, shape: str, x: float, y: float) -> str:
    return (
        f'\t\t(pin "{name}" {shape}\n'
        f"\t\t\t(at {x:g} {y:g} 180)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        f"\t\t\t\t(justify right)\n\t\t\t)\n"
        f'\t\t\t(uuid "{_new_uuid()}")\n'
        f"\t\t)\n"
    )


def _pwr_flag(index: int, x: float, y: float) -> str:
    return (
        f"\t(symbol\n"
        f'\t\t(lib_id "power:PWR_FLAG")\n'
        f"\t\t(at {x:g} {y:g} 0)\n"
        f"\t\t(unit 1)\n"
        f"\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n"
        f"\t\t(fields_autoplaced yes)\n"
        f'\t\t(uuid "{_new_uuid()}")\n'
        f'\t\t(property "Reference" "#FLG{index:02d}"\n'
        f"\t\t\t(at {x:g} {y - 2:g} 0)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(hide yes)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        f'\t\t(property "Value" "PWR_FLAG"\n'
        f"\t\t\t(at {x:g} {y + 2:g} 0)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        f'\t\t(pin "1"\n\t\t\t(uuid "{_new_uuid()}")\n\t\t)\n'
        f"\t)\n"
    )


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
