"""BOM emission (SEED_PLAN acceptance criterion 4: "every part a real,
orderable MPN").

A synthesized design's child sheets carry the stamped truth: each instance
symbol has a ``Footprint`` set by the emitter and hidden ``MPN`` /
``Manufacturer`` properties (see :mod:`infersynth.compile_kicad.emit`). This
module reads that back — it does NOT re-run the binder or need the catalog —
and rolls it up into a grouped BOM.

Grouping key is ``(mpn, value, footprint)``: value-parametric parts (resistors
of different values sharing one MPN) land on distinct lines; identical parts
across sheets merge. A part whose symbol carries no ``MPN`` is *unbound* and is
listed loudly in a trailing section rather than dropped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["BomLine", "Bom", "build_bom", "bom_to_csv"]

_FIRST_INSTANCE_SYMBOL = "(lib_id"
_REF_RE = re.compile(r'\(property "Reference" "([^"]*)"')
_VALUE_RE = re.compile(r'\(property "Value" "([^"]*)"')
_FOOTPRINT_RE = re.compile(r'\(property "Footprint" "([^"]*)"')
_MPN_RE = re.compile(r'\(property "MPN" "([^"]*)"')
_MFR_RE = re.compile(r'\(property "Manufacturer" "([^"]*)"')


@dataclass(frozen=True)
class BomLine:
    """One grouped BOM line: all refs sharing an ``(mpn, value, footprint)``."""

    refs: tuple[str, ...]
    value: str
    mpn: str
    manufacturer: str
    footprint: str

    @property
    def qty(self) -> int:
        return len(self.refs)


@dataclass(frozen=True)
class Bom:
    """A design's rolled-up BOM plus its loud unbound tail."""

    lines: tuple[BomLine, ...] = ()
    unbound: tuple[str, ...] = ()

    @property
    def total_parts(self) -> int:
        return sum(line.qty for line in self.lines) + len(self.unbound)

    @property
    def bound_parts(self) -> int:
        return sum(line.qty for line in self.lines)

    @property
    def summary(self) -> str:
        return (
            f"{self.total_parts} part(s), {self.bound_parts} bound, "
            f"{len(self.unbound)} unbound, {len(self.lines)} line item(s)"
        )


def _iter_instance_symbols(text: str):
    """Yield each instance-symbol segment of a child schematic (everything past
    the first ``(lib_id`` split per symbol). The ``(lib_symbols ...)`` head is
    skipped — it carries library-default properties, not placed parts."""
    idx = text.find(_FIRST_INSTANCE_SYMBOL)
    if idx == -1:
        return
    tail = text[idx:]
    starts = [m.start() for m in re.finditer(re.escape(_FIRST_INSTANCE_SYMBOL), tail)]
    starts.append(len(tail))
    for i in range(len(starts) - 1):
        yield tail[starts[i] : starts[i + 1]]


def _first(pattern: re.Pattern[str], seg: str) -> str | None:
    m = pattern.search(seg)
    return m.group(1) if m else None


def build_bom(design_dir: str | Path) -> Bom:
    """Walk *design_dir*'s child ``*.kicad_sch`` sheets and roll up their
    stamped parts into a grouped :class:`Bom`.

    Every ``*.kicad_sch`` is scanned; the root sheet contributes nothing (it
    holds only ``(sheet ...)`` references, no placed symbols). Refs are
    deduplicated per sheet (a multi-unit symbol repeats one ref), and are
    globally unique across a renumbered design.
    """
    design_dir = Path(design_dir)
    # key (mpn, value, footprint) -> {refs, manufacturer}
    groups: dict[tuple[str, str, str], dict] = {}
    unbound: list[str] = []
    for sheet in sorted(design_dir.glob("*.kicad_sch")):
        text = sheet.read_text(encoding="utf-8")
        seen: set[str] = set()
        for seg in _iter_instance_symbols(text):
            ref = _first(_REF_RE, seg)
            if ref is None or ref.startswith("#") or ref in seen:
                continue
            seen.add(ref)
            mpn = _first(_MPN_RE, seg) or ""
            if not mpn:
                unbound.append(ref)
                continue
            value = _first(_VALUE_RE, seg) or ""
            footprint = _first(_FOOTPRINT_RE, seg) or ""
            manufacturer = _first(_MFR_RE, seg) or ""
            key = (mpn, value, footprint)
            entry = groups.setdefault(key, {"refs": [], "manufacturer": manufacturer})
            entry["refs"].append(ref)
    lines = tuple(
        BomLine(
            refs=tuple(sorted(entry["refs"], key=_ref_sort_key)),
            value=value,
            mpn=mpn,
            manufacturer=entry["manufacturer"],
            footprint=footprint,
        )
        for (mpn, value, footprint), entry in sorted(groups.items())
    )
    return Bom(lines=lines, unbound=tuple(sorted(unbound, key=_ref_sort_key)))


def _ref_sort_key(ref: str) -> tuple[str, int, str]:
    """Natural refdes ordering: prefix, then numeric suffix (R2 before R10)."""
    m = re.match(r"^([A-Za-z_#]+)(\d+)$", ref)
    if m:
        return (m.group(1), int(m.group(2)), "")
    return (ref, 0, ref)


def bom_to_csv(bom: Bom) -> str:
    """Render *bom* as CSV (Refs, Qty, Value, MPN, Manufacturer, Footprint) with
    a leading ``# summary`` line and a loud trailing UNBOUND section."""
    buf = io.StringIO()
    buf.write(f"# BOM summary: {bom.summary}\n")
    writer = csv.writer(buf)
    writer.writerow(["Refs", "Qty", "Value", "MPN", "Manufacturer", "Footprint"])
    for line in bom.lines:
        writer.writerow(
            [
                " ".join(line.refs),
                line.qty,
                line.value,
                line.mpn,
                line.manufacturer,
                line.footprint,
            ]
        )
    if bom.unbound:
        buf.write(f"# UNBOUND ({len(bom.unbound)}) — no MPN stamped; bind these parts:\n")
        for ref in bom.unbound:
            buf.write(f"# UNBOUND,{ref}\n")
    return buf.getvalue()
