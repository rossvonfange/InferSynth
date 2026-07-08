"""Ecosystem connector classification by PINOUT fingerprint — the second "known
not guessed" tier (docs/HIERARCHICAL_RECOGNITION.md).

A standard connector (RPi-40 header, mikroBUS socket, MIPI CSI/DSI FFC, Pmod,
Qwiic/STEMMA, Arduino Uno shield, FMC) is a SPEC: a fixed pin count / footprint
plus a standardized pin->function map. What is wired behind each pin varies
board-to-board, so a connector match classifies the CONNECTOR itself (``rpi40``,
``mikrobus``, ``mipi_csi``) — not the protocol behind it. We encode the ~10
common ones once in ``catalog/connectors.yaml`` and recognize them by:

  * pin count (primary — the connector's number of pads), plus
  * footprint substring hints when the footprint survived import, plus
  * standardized pin-function tokens found in the connector's net names.

When the footprint is unknown (a very common case — an Allegro ``.brd`` imported
through kicad-cli drops footprints), a minimum number of corroborating function
tokens is REQUIRED, so an ambiguous pin count (16 = mikroBUS, but also a 16-pin
USB-C; 40 = RPi, but also a plain 40-pin header) cannot be force-matched. No
corroboration, no match — the honesty rule.

Pure and deterministic (SELECTION.md §8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from infersynth.recognize.netlist import DesignNetlist

__all__ = [
    "ConnectorSignature",
    "ConnectorsError",
    "ConnectorMatch",
    "load_connectors",
    "classify_connector",
    "component_pin_count",
]

_TOP_KEYS = {"version", "connectors"}
_CONN_KEYS = {
    "pins",
    "footprint_hints",
    "function_hints",
    "min_corroboration",
    "base_confidence",
}
_TOKEN_SPLIT = re.compile(r"[^A-Z0-9]+")

#: Bump for a footprint substring hit; and per-function-token corroboration
#: bump (capped). Confidence ceiling mirrors the protocol classifier's.
FOOTPRINT_BUMP = 0.2
CORROBORATION_STEP = 0.08
CORROBORATION_CAP = 0.4
CONFIDENCE_CEILING = 0.95


class ConnectorsError(ValueError):
    """Raised when ``connectors.yaml`` is malformed (an authoring bug)."""


@dataclass(frozen=True)
class ConnectorSignature:
    """One loaded, validated entry from ``connectors.yaml``."""

    name: str
    pins: tuple[int, int]
    footprint_hints: tuple[str, ...]
    function_hints: tuple[str, ...]
    min_corroboration: int
    base_confidence: float


def _as_pin_range(where: str, val: object) -> tuple[int, int]:
    if not (isinstance(val, list) and len(val) == 2):
        raise ConnectorsError(f"{where} must be a [lo, hi] list of two ints")
    lo, hi = val
    if not (isinstance(lo, int) and isinstance(hi, int)) or isinstance(lo, bool):
        raise ConnectorsError(f"{where} bounds must be ints, got {val!r}")
    if lo > hi or lo < 1:
        raise ConnectorsError(f"{where} must be a valid positive range, got {val!r}")
    return (lo, hi)


def load_connectors(path: str | Path) -> dict[str, ConnectorSignature]:
    """Load + validate ``connectors.yaml``. Returns connector name -> signature."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ConnectorsError(f"{p}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConnectorsError(f"{p}: must be a mapping")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise ConnectorsError(f"{p}: unknown top-level key(s) {unknown}")
    conns = data.get("connectors")
    if not isinstance(conns, dict) or not conns:
        raise ConnectorsError(f"{p}: 'connectors' must be a non-empty mapping")

    out: dict[str, ConnectorSignature] = {}
    for name, spec in conns.items():
        where = f"{p}: connectors.{name}"
        if not isinstance(name, str) or not name:
            raise ConnectorsError(f"{p}: connector names must be non-empty strings")
        if not isinstance(spec, dict):
            raise ConnectorsError(f"{where} must be a mapping")
        bad = sorted(set(spec) - _CONN_KEYS)
        if bad:
            raise ConnectorsError(f"{where}: unknown key(s) {bad}")
        pins = _as_pin_range(f"{where}.pins", spec.get("pins"))
        fh_raw = spec.get("footprint_hints", []) or []
        if not isinstance(fh_raw, list) or not all(isinstance(h, str) for h in fh_raw):
            raise ConnectorsError(f"{where}.footprint_hints must be a list of strings")
        func_raw = spec.get("function_hints", []) or []
        if not isinstance(func_raw, list) or not all(isinstance(h, str) for h in func_raw):
            raise ConnectorsError(f"{where}.function_hints must be a list of strings")
        min_corr = spec.get("min_corroboration", 2)
        if not isinstance(min_corr, int) or isinstance(min_corr, bool) or min_corr < 0:
            raise ConnectorsError(f"{where}.min_corroboration must be a non-negative int")
        bc = spec.get("base_confidence", 0.4)
        if not isinstance(bc, (int, float)) or isinstance(bc, bool) or not 0.0 <= bc <= 1.0:
            raise ConnectorsError(f"{where}.base_confidence must be a float in [0, 1]")
        out[name] = ConnectorSignature(
            name=name,
            pins=pins,
            footprint_hints=tuple(fh_raw),
            function_hints=tuple(h.upper() for h in func_raw),
            min_corroboration=int(min_corr),
            base_confidence=float(bc),
        )
    return out


def component_pin_count(ref: str, design: DesignNetlist) -> int:
    """Number of distinct pins of *ref* that land on a net (its connected pad
    count) — the primary connector-recognition key."""
    pins: set[str] = set()
    for _name, pl in design.nets.items():
        for r, p in pl:
            if r == ref:
                pins.add(p)
    return len(pins)


def _net_names_of(ref: str, design: DesignNetlist) -> list[str]:
    return sorted({name for name, pl in design.nets.items() for r, _p in pl if r == ref})


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.upper()) if t}


def _hint_hits(hint: str, tokens: set[str]) -> bool:
    for t in tokens:
        if t == hint:
            return True
        if t.startswith(hint) and t[len(hint):].isdigit():
            return True
    return False


@dataclass(frozen=True)
class ConnectorMatch:
    """Result of :func:`classify_connector`.

    ``kind`` is a connector name (``rpi40``/``mikrobus``/``mipi_csi``…) or
    ``None`` when nothing matched confidently. ``basis`` records how it was
    reached: ``"footprint"``, ``"pinout"`` (net-name function map), or
    ``"none"``.
    """

    kind: str | None
    confidence: float
    basis: str
    pin_count: int = 0
    candidates: tuple[str, ...] = ()


def classify_connector(
    ref: str,
    design: DesignNetlist,
    connectors: dict[str, ConnectorSignature],
    *,
    use_names: bool = True,
) -> ConnectorMatch:
    """Classify a component as a standard ecosystem connector, or ``None``.

    Pin count selects the pin-compatible candidates; footprint substring hints
    and the standardized pin-function net-name map corroborate. When the
    footprint carries no hint, at least ``min_corroboration`` function tokens
    must be present — otherwise the candidate is rejected (an ambiguous pin count
    is never force-matched). Deterministic: candidates considered in sorted-name
    order, ties broken lexically.
    """
    pin_count = component_pin_count(ref, design)
    comp = design.components.get(ref)
    footprint = (comp.footprint if comp else "") or ""
    fp_upper = footprint.upper()
    net_tokens: set[str] = set()
    if use_names:
        for n in _net_names_of(ref, design):
            net_tokens |= _tokens(n)

    pin_candidates = [
        connectors[name]
        for name in sorted(connectors)
        if connectors[name].pins[0] <= pin_count <= connectors[name].pins[1]
    ]
    if not pin_candidates:
        return ConnectorMatch(None, 0.0, "none", pin_count, ())

    scored: list[tuple[float, str, str]] = []  # (final_score, basis, name)
    for sig in pin_candidates:
        fp_hit = any(h.upper() in fp_upper for h in sig.footprint_hints) if fp_upper else False
        n_func = (
            sum(1 for h in sig.function_hints if _hint_hits(h, net_tokens)) if use_names else 0
        )
        # honesty gate: no footprint hint AND too little pinout corroboration ->
        # reject this candidate (do not force a same-pin-count false match).
        if not fp_hit and n_func < sig.min_corroboration:
            continue
        score = sig.base_confidence
        if fp_hit:
            score += FOOTPRINT_BUMP
        score += min(n_func * CORROBORATION_STEP, CORROBORATION_CAP)
        basis = "footprint" if fp_hit else "pinout"
        scored.append((score, basis, sig.name))

    if not scored:
        return ConnectorMatch(None, 0.0, "none", pin_count, tuple(s.name for s in pin_candidates))

    scored.sort(key=lambda t: (-t[0], t[2]))
    best_score, best_basis, best_kind = scored[0]
    return ConnectorMatch(
        kind=best_kind,
        confidence=round(min(best_score, CONFIDENCE_CEILING), 4),
        basis=best_basis,
        pin_count=pin_count,
        candidates=tuple(name for _s, _b, name in sorted(scored, key=lambda t: t[2])),
    )
