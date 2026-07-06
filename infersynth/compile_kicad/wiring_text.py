"""Shared KiCad text-surgery primitives for wiring emission (NETFLOW stage 1).

The cell-CI *harness* generator (``infersynth.gates.harness``) and the design
*wiring* emitter (``infersynth.compile_kicad.wiring``) both make a design
ERC-complete by the SAME text surgery: splice ``(pin ...)`` blocks onto a
parent ``(sheet ...)``, drop coincident ``(global_label ...)`` at parent level,
and place ``power:PWR_FLAG`` symbols to mark intentionally-driven nets. Those
primitives live here so the two writers share one implementation.

Writer/oracle law (UX.md): this is *writer* code. It is imported by the
harness and the wiring emitter (both writers) and never by the *checks*
(``gates/erc.py`` / ``gates/netlist.py``) — an emission bug can never hide
itself in the checker.
"""

from __future__ import annotations

import uuid

__all__ = [
    "SHAPE",
    "PIN_STRIDE",
    "PWR_FLAG_LIB",
    "new_uuid",
    "global_label",
    "sheet_pin",
    "pwr_flag",
    "inject_pwr_flag_lib",
]

# Sheet-pin / global-label shape per port direction (KiCad label shapes).
SHAPE = {
    "in": "input",
    "out": "output",
    "inout": "bidirectional",
    "passive": "passive",
}

#: mm between sheet pins along a sheet border (2 * 50-mil grid).
PIN_STRIDE = 2.54

# The stock ``power:PWR_FLAG`` library symbol, lifted verbatim from KiCad's
# power.kicad_sym (name unqualified there; qualified at inject time) and stored
# at the 1-tab base indent it has in that file. Injected into a root's
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
PWR_FLAG_LIB = "\n".join("\t" + line for line in _PWR_FLAG_LIB_QUALIFIED.splitlines())


def new_uuid() -> str:
    return str(uuid.uuid4())


def global_label(name: str, shape: str, x: float, y: float) -> str:
    return (
        f'\t(global_label "{name}"\n'
        f"\t\t(shape {shape})\n"
        f"\t\t(at {x:g} {y:g} 180)\n"
        f"\t\t(fields_autoplaced yes)\n"
        f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n"
        f"\t\t\t(justify right)\n\t\t)\n"
        f'\t\t(uuid "{new_uuid()}")\n'
        f"\t)\n"
    )


def sheet_pin(name: str, shape: str, x: float, y: float) -> str:
    return (
        f'\t\t(pin "{name}" {shape}\n'
        f"\t\t\t(at {x:g} {y:g} 180)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        f"\t\t\t\t(justify right)\n\t\t\t)\n"
        f'\t\t\t(uuid "{new_uuid()}")\n'
        f"\t\t)\n"
    )


def pwr_flag(index: int, x: float, y: float) -> str:
    return (
        f"\t(symbol\n"
        f'\t\t(lib_id "power:PWR_FLAG")\n'
        f"\t\t(at {x:g} {y:g} 0)\n"
        f"\t\t(unit 1)\n"
        f"\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n"
        f"\t\t(fields_autoplaced yes)\n"
        f'\t\t(uuid "{new_uuid()}")\n'
        f'\t\t(property "Reference" "#FLG{index:02d}"\n'
        f"\t\t\t(at {x:g} {y - 2:g} 0)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(hide yes)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        f'\t\t(property "Value" "PWR_FLAG"\n'
        f"\t\t\t(at {x:g} {y + 2:g} 0)\n\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        f'\t\t(pin "1"\n\t\t\t(uuid "{new_uuid()}")\n\t\t)\n'
        f"\t)\n"
    )


def inject_pwr_flag_lib(text: str) -> str:
    """Populate a root's empty ``(lib_symbols)`` with ``power:PWR_FLAG``.

    Idempotent: if the PWR_FLAG symbol is already present, returns *text*
    unchanged. Raises ``ValueError`` if there is no empty ``(lib_symbols)`` to
    populate (the emitter always writes one).
    """
    if 'symbol "power:PWR_FLAG"' in text:
        return text
    if "\t(lib_symbols)" not in text:
        raise ValueError("root has no empty (lib_symbols) to populate")
    return text.replace("\t(lib_symbols)", f"\t(lib_symbols\n{PWR_FLAG_LIB}\n\t)", 1)
