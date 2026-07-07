"""Wiring emitter — apply a :class:`WiringPlan` to a synthesized design.

The wiring writer is the harness generator generalized (NETFLOW.md "Emission"):
for every port that participates in an inferred net it splices a ``(pin ...)``
onto that instance's parent ``(sheet ...)`` block, drops a coincident parent
``(global_label ...)`` *named by the NET* (so pins on the same net across
different sheets share the label and connect by name), and places one
``power:PWR_FLAG`` per driven rail. Ports in no net get no sheet pin — their
hierarchical labels stay orphaned inside the child (the expected residual for
unwired ports).

Same text surgery as the harness, sharing :mod:`infersynth.compile_kicad.
wiring_text`. Writer/oracle law (UX.md): this module imports nothing from
:mod:`infersynth.gates`; the ERC/netlist *checks* verify the result
independently.
"""

from __future__ import annotations

import re
from pathlib import Path

from infersynth.catalog import Catalog
from infersynth.compile_kicad import emit
from infersynth.compile_kicad.wiring_text import (
    PIN_STRIDE,
    SHAPE,
    global_label,
    inject_pwr_flag_lib,
    pwr_flag,
    sheet_pin,
)
from infersynth.ir import PortDirection
from infersynth.netflow.plan import WiringPlan

__all__ = ["emit_wiring"]

_SHEET_RE = re.compile(r"\t\(sheet\n.*?\n\t\)\n", re.DOTALL)
_SHEETNAME_RE = re.compile(r'\(property "Sheetname" "([^"]*)"')
_AT_RE = re.compile(r"\(at ([\d.]+) ([\d.]+)\)")
_SIZE_RE = re.compile(rf"\(size {emit._SHEET_W:g} {emit._SHEET_H:g}\)")


def emit_wiring(root: Path, plan: WiringPlan, instances, catalog: Catalog) -> None:
    """Splice *plan*'s nets onto the design rooted at *root* (in place).

    *instances* exposes ``instname``/``cell_key`` (to resolve each port's
    direction → label shape). Only ports named by a net are wired; a driven
    rail also gets one ``PWR_FLAG`` at its first member.
    """
    root = Path(root)
    text = root.read_text()

    # instname -> cell (for port-direction -> label shape lookup).
    cells_by_inst = {
        inst.instname: catalog.cells.get(inst.cell_key)
        for inst in instances
    }

    # Per instname: list of (port, net_name, shape). Plus PWR_FLAG anchors:
    # (instname, port) for the first member of each driven rail.
    per_inst: dict[str, list[tuple[str, str, str]]] = {}
    flag_anchors: set[tuple[str, str]] = set()
    any_flag = False
    for net in plan.nets:
        if net.kind == "rail" and net.needs_flag and net.members:
            flag_anchors.add(net.members[0])
            any_flag = True
        for instname, port in net.members:
            cell = cells_by_inst.get(instname)
            direction = PortDirection.PASSIVE.value
            if cell is not None and port in cell.ports:
                direction = cell.ports[port].get("direction", direction)
            per_inst.setdefault(instname, []).append((port, net.name, SHAPE[direction]))

    if not per_inst:
        return  # nothing to wire (no rails, no signal nets)

    if any_flag:
        text = inject_pwr_flag_lib(text)

    scaffolding: list[str] = []  # parent-level global_labels + pwr_flags
    n_flag = 0

    def _rewrite_sheet(match: re.Match[str]) -> str:
        nonlocal n_flag
        block = match.group(0)
        name_m = _SHEETNAME_RE.search(block)
        if name_m is None:
            return block
        instname = name_m.group(1)
        wired = per_inst.get(instname)
        if not wired:
            return block
        at_m = _AT_RE.search(block)
        if at_m is None:  # pragma: no cover - emitter always writes (at x y)
            return block
        sx, sy = float(at_m.group(1)), float(at_m.group(2))

        # enlarge the sheet box so all pins fit on its left border.
        sheet_h = max(emit._SHEET_H, (len(wired) + 1) * PIN_STRIDE)
        block = _SIZE_RE.sub(f"(size {emit._SHEET_W:g} {sheet_h:g})", block, count=1)

        pins: list[str] = []
        y = sy + PIN_STRIDE
        for port, net_name, shape in sorted(wired):
            pins.append(sheet_pin(port, shape, sx, y))
            scaffolding.append(global_label(net_name, shape, sx, y))
            if (instname, port) in flag_anchors:
                n_flag += 1
                scaffolding.append(pwr_flag(n_flag, sx, y))
            y += PIN_STRIDE

        return block.replace("\t\t(instances\n", "".join(pins) + "\t\t(instances\n", 1)

    text = _SHEET_RE.sub(_rewrite_sheet, text)
    text = text.replace(
        "\t(sheet_instances\n", "".join(scaffolding) + "\t(sheet_instances\n", 1
    )
    root.write_text(text)
