"""Design-netlist model for the recognizer (STEP 0 — read the input).

The recognizer's input is a *design* netlist: the same ``kicadxml`` a KiCad
schematic exports (:func:`infersynth.gates.netlist.export_netlist`). That
module's :func:`~infersynth.gates.netlist.parse_kicadxml` already reduces the
``<nets>`` block to the ``net -> [(ref, pin)]`` partition the recognizer needs
— we REUSE it verbatim for connectivity. What it deliberately drops (it is a
gate helper, not a BOM reader) is per-component metadata: reference, value,
footprint, and the injected ``MPN`` property. This module adds exactly that
component read on top of the reused net parser, yielding a :class:`DesignNetlist`.

The MPN is read from the ``<property name="MPN" value="...">`` the InferSynth
emitter stamps (:func:`infersynth.compile_kicad.emit._stamp_segment`); a
vendor netlist that carries MPN in a ``<field name="MPN">`` or in the KiCad
``Value``/``<value>`` is also honored, so the recognizer is not InferSynth-only.

Deterministic: components are returned in sorted-ref order; the pin index is a
plain dict keyed by ``(ref, pin)``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from infersynth.gates.netlist import NetlistError, parse_kicadxml

__all__ = [
    "Component",
    "DesignNetlist",
    "load_design_netlist",
    "ref_class",
    "parse_value_str",
]

_REF_CLASS_RE = re.compile(r"^([A-Za-z]+)")

# SI multiplier suffixes seen in KiCad value fields. 'R'/'r' is the ohm marker
# (also usable as a decimal point, e.g. ``4R7`` == 4.7). 'K' == 'k'.
_SI_MULT: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,  # micro sign
    "m": 1e-3,
    "R": 1.0,
    "r": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

# ``<number><suffix>`` or the ``4k7`` / ``4R7`` decimal-in-suffix form.
_VALUE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([pnumµμRrkKMG]?)\s*([0-9]+)?\s*$")


def ref_class(ref: str) -> str:
    """The component class = the leading alpha run of a reference designator.

    ``"R101" -> "R"``, ``"U1" -> "U"``, ``"C7" -> "C"``. Used as the node type
    in subgraph matching (an ``R`` may only map to an ``R``).
    """
    m = _REF_CLASS_RE.match(ref)
    return m.group(1) if m else ref


def parse_value_str(text: str) -> float | None:
    """Parse a KiCad component value string to a float, or ``None``.

    Handles plain floats (incl. scientific, e.g. capacitor farads emitted as
    ``"1e-08"``), SI-suffixed resistances (``"99k" -> 99000``, ``"2.2M"``),
    and the suffix-as-decimal-point form (``"4k7" -> 4700``, ``"4R7" -> 4.7``).
    Non-numeric symbol values (e.g. an op-amp's ``"OPAMP_SINGLE"``) return
    ``None`` — those parts carry no invertible value.
    """
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    # Plain float first: catches ``1e-08``, ``470``, ``99000``, ``4.7``.
    try:
        return float(s)
    except ValueError:
        pass
    m = _VALUE_RE.match(s)
    if not m:
        return None
    mantissa, suffix, frac = m.group(1), m.group(2), m.group(3)
    if not suffix:
        return None
    mult = _SI_MULT.get(suffix)
    if mult is None:
        return None
    try:
        base = float(mantissa)
        if frac is not None:  # 4k7 -> 4.7 * 1e3
            base = float(f"{mantissa}.{frac}")
    except ValueError:
        return None
    return base * mult


@dataclass(frozen=True)
class Component:
    """One design component (a ``<comp>`` row), with the metadata the
    recognizer needs: identity, class, invertible value, and MPN anchor."""

    ref: str
    value_str: str
    footprint: str
    mpn: str  # "" when the component declares none
    cls: str = ""
    value: float | None = None

    @classmethod
    def build(cls, ref: str, value_str: str, footprint: str, mpn: str) -> Component:
        return cls(
            ref=ref,
            value_str=value_str,
            footprint=footprint,
            mpn=mpn,
            cls=ref_class(ref),
            value=parse_value_str(value_str),
        )


@dataclass(frozen=True)
class DesignNetlist:
    """A parsed design netlist: components + connectivity, with lookups.

    ``nets`` is the reused :func:`parse_kicadxml` partition ``net -> [(ref,
    pin)]``. ``pin_net`` inverts it to ``(ref, pin) -> net name`` (a pin lands
    on exactly one net). ``net_pins`` gives each net's full ``(ref, pin)`` set.
    """

    components: dict[str, Component]
    nets: dict[str, list[tuple[str, str]]]
    pin_net: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def net_pins(self) -> dict[str, frozenset[tuple[str, str]]]:
        return {name: frozenset(pins) for name, pins in self.nets.items()}

    def components_of_class(self, cls: str) -> list[str]:
        """Refs of every component of *cls*, sorted (deterministic candidate order)."""
        return sorted(r for r, c in self.components.items() if c.cls == cls)


def _mpn_of_comp(comp: ET.Element) -> str:
    """Extract an MPN from a ``<comp>`` element, trying (in order) the stamped
    ``<property name="MPN">``, a ``<field name="MPN">``, then empty."""
    for prop in comp.findall("property"):
        if prop.get("name") == "MPN":
            return (prop.get("value") or "").strip()
    for field_el in comp.findall("./fields/field"):
        if field_el.get("name") == "MPN":
            return (field_el.text or "").strip()
    return ""


def _parse_components(xml_path: Path) -> dict[str, Component]:
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise NetlistError(f"cannot parse netlist {xml_path}: {exc}") from exc
    out: dict[str, Component] = {}
    for comp in root.iter("comp"):
        ref = comp.get("ref") or ""
        if not ref or ref.startswith("#"):
            continue
        value = comp.findtext("value") or ""
        footprint = comp.findtext("footprint") or ""
        out[ref] = Component.build(ref, value.strip(), footprint.strip(), _mpn_of_comp(comp))
    return dict(sorted(out.items()))


def load_design_netlist(xml_path: str | Path) -> DesignNetlist:
    """Load a ``kicadxml`` design netlist into a :class:`DesignNetlist`.

    Nets come from the reused :func:`infersynth.gates.netlist.parse_kicadxml`
    (``#``-virtual refs already filtered); components are read here.
    """
    path = Path(xml_path)
    if not path.is_file():
        raise NetlistError(f"design netlist not found: {path}")
    nets = dict(parse_kicadxml(path))
    components = _parse_components(path)
    pin_net: dict[tuple[str, str], str] = {}
    for name, pins in nets.items():
        for ref, pin in pins:
            pin_net[(ref, pin)] = name
    return DesignNetlist(components=components, nets=nets, pin_net=pin_net)
