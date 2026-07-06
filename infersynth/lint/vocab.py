"""Controlled vocabulary, GENERATED from the catalog (DESIGN.md section 6).

The FRD language's semantics layer: each catalog cell contributes its idiom
keywords and parameter schemas. The vocabulary is never hand-written — it
cannot drift from what synthesis can do, and it grows exactly when the
catalog grows.

``resolve(requirement, vocab)`` matches a requirement against the vocabulary:
case-insensitive keyword-phrase matching plus simple number-with-unit
extraction (V, A, mA, Hz, kHz, MHz, ppm, °C, %, ohm/Ω with k/M suffixes) for
in-range parameter checking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from infersynth.catalog import Catalog
from infersynth.lint.model import Requirement

__all__ = ["Vocabulary", "VocabEntry", "Quantity", "Resolution", "extract_quantities", "resolve"]


@dataclass(frozen=True)
class VocabEntry:
    """One cell's contribution to the FRD language."""

    cell_key: str  # name@version
    cell_name: str
    keywords: tuple[str, ...]
    params: dict[str, dict[str, Any]]  # name -> schema (type/range/allowed/default)
    disambiguation: str | None


@dataclass
class Vocabulary:
    """The generated controlled vocabulary of an installed catalog."""

    entries: list[VocabEntry] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> Vocabulary:
        entries = []
        for key in sorted(catalog.cells):
            cell = catalog.cells[key]
            entries.append(
                VocabEntry(
                    cell_key=cell.key,
                    cell_name=cell.name,
                    keywords=tuple(cell.keywords),
                    params=cell.idiom_params,
                    disambiguation=(
                        str(cell.disambiguation) if cell.disambiguation else None
                    ),
                )
            )
        return cls(entries)

    def keywords(self) -> list[str]:
        """All idiom keywords — the LSP completion dictionary (UX.md)."""
        return sorted({kw for e in self.entries for kw in e.keywords})


# --- number-with-unit extraction -------------------------------------------

#: unit spelling -> (canonical dimension, multiplier to base unit)
_UNITS: dict[str, tuple[str, float]] = {
    "v": ("V", 1.0),
    "mv": ("V", 1e-3),
    "kv": ("V", 1e3),
    "a": ("A", 1.0),
    "ma": ("A", 1e-3),
    "ua": ("A", 1e-6),
    "µa": ("A", 1e-6),
    "hz": ("Hz", 1.0),
    "khz": ("Hz", 1e3),
    "mhz": ("Hz", 1e6),
    "ghz": ("Hz", 1e9),
    "ppm": ("ppm", 1.0),
    "°c": ("degC", 1.0),
    "%": ("%", 1.0),
    "ohm": ("ohm", 1.0),
    "ohms": ("ohm", 1.0),
    "ω": ("ohm", 1.0),
    "kω": ("ohm", 1e3),
    "mω": ("ohm", 1e6),
    "kohm": ("ohm", 1e3),
    "mohm": ("ohm", 1e6),
    "k": ("ohm", 1e3),  # bare k/M suffixes read as resistances ("100k")
    "m": ("ohm", 1e6),
}

_NUMBER_UNIT_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>°C|µA|[kM]?Ω|[kM]?ohms?|[mk]?V|[mµu]?A|[kMG]?Hz|ppm|%|[kM]\b)",
)

#: param-name suffix -> canonical dimension (how a schema declares its unit)
_PARAM_SUFFIX_DIM = {
    "_ohms": "ohm",
    "_ohm": "ohm",
    "_v": "V",
    "_volts": "V",
    "_a": "A",
    "_amps": "A",
    "_hz": "Hz",
    "_ppm": "ppm",
    "_pct": "%",
    "_degc": "degC",
}


@dataclass(frozen=True)
class Quantity:
    """A number-with-unit found in requirement text."""

    value: float  # in the dimension's base unit
    dimension: str  # V | A | Hz | ppm | degC | % | ohm
    raw: str


def extract_quantities(text: str) -> list[Quantity]:
    out = []
    for m in _NUMBER_UNIT_RE.finditer(text):
        unit = m.group("unit")
        # bare k/M suffixes ("100k", "4M7"-less "4M") read as resistances;
        # they only match at a word boundary so "kbps"/"MHz" are unaffected.
        dim, mult = _UNITS[unit.lower()]
        out.append(Quantity(float(m.group("value")) * mult, dim, m.group(0)))
    return out


# --- resolution -------------------------------------------------------------


@dataclass
class ParamBinding:
    """A parameter value extracted from requirement text, range-checked."""

    name: str
    value: float
    problem: str | None  # None = in range; else the range-violation text


@dataclass
class Resolution:
    """One vocabulary entry matched by a requirement."""

    entry: VocabEntry
    matched_keywords: tuple[str, ...]
    params: list[ParamBinding] = field(default_factory=list)


def _param_dimension(name: str, schema: dict[str, Any]) -> str | None:
    unit = schema.get("unit")
    if isinstance(unit, str) and unit.lower() in _UNITS:
        return _UNITS[unit.lower()][0]
    lname = name.lower()
    for suffix, dim in _PARAM_SUFFIX_DIM.items():
        if lname.endswith(suffix):
            return dim
    return None


def _check_range(schema: dict[str, Any], value: float) -> str | None:
    prange = schema.get("range")
    if prange is not None:
        lo, hi = prange
        if lo is not None and value < lo:
            return f"value {value:g} below minimum {lo:g}"
        if hi is not None and value > hi:
            return f"value {value:g} above maximum {hi:g}"
    allowed = schema.get("allowed")
    if allowed is not None and value not in allowed:
        return f"value {value:g} not in allowed set {list(allowed)!r}"
    return None


def _extract_params(text: str, entry: VocabEntry) -> list[ParamBinding]:
    """Bind numbers in *text* to *entry*'s params.

    Two extraction routes: (a) the bare param word followed by a number
    ("gain of 100", "gain = 12"); (b) a number-with-unit whose dimension
    matches the param's declared/suffix-implied unit ("1k" -> rg_ohms).
    """
    bindings: list[ParamBinding] = []
    quantities = extract_quantities(text)
    for pname in sorted(entry.params):
        schema = entry.params[pname]
        value: float | None = None
        # (a) "<param-word> [of|=|:] <number>"
        word = pname.lower().split("_")[0]
        m = re.search(
            rf"\b{re.escape(word)}\b\W{{0,3}}(?:of|=|:)?\s*(\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            value = float(m.group(1))
        else:
            # (b) unit-dimension match
            dim = _param_dimension(pname, schema)
            if dim is not None:
                dimmed = [q for q in quantities if q.dimension == dim]
                if len(dimmed) == 1:
                    value = dimmed[0].value
        if value is not None:
            bindings.append(ParamBinding(pname, value, _check_range(schema, value)))
    return bindings


def resolve(requirement: Requirement, vocab: Vocabulary) -> list[Resolution]:
    """Match one requirement against the vocabulary (keyword-phrase based)."""
    text = re.sub(r"\s+", " ", requirement.text).lower()
    matches: list[Resolution] = []
    for entry in vocab.entries:
        hit = tuple(kw for kw in entry.keywords if kw.lower() in text)
        if hit:
            matches.append(
                Resolution(
                    entry=entry,
                    matched_keywords=hit,
                    params=_extract_params(requirement.text, entry),
                )
            )
    return matches


def pick_winner(matches: list[Resolution]) -> Resolution | None:
    """Disambiguation rule: entries that declare an ``idioms.disambiguation``
    rule are never inferred directly (the quad-pack convention, see
    catalog/opamp-gain-x4-noninverting). A single match wins outright; among
    several, a *sole* rule-free entry wins; otherwise it is ambiguous (None).
    """
    if len(matches) == 1:
        return matches[0]
    primaries = [m for m in matches if m.entry.disambiguation is None]
    if len(primaries) == 1:
        return primaries[0]
    return None
