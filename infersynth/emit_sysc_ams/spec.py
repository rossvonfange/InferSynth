"""AMS model-tier discovery and per-cell emission specs (WP-S2 deliverable 1).

The AMS model tier is a convention: a cell ships ``model/ams/<cell>.h`` — a
SystemC-AMS timed-dataflow (TDF) module whose ports match the cell's ``ports``
and whose constructor takes the gain-prefixed idiom params (sorted) plus the
rail-headroom ``margin``. This module turns a :class:`CellPackage` (or the ports
of an IR :class:`~infersynth.ir.core.Cell`) into an :class:`AmsModuleSpec` the
emitter uses to instantiate and wire the module, and resolves a deterministic
operating point (param values + rail voltages + timing) that mirrors the v0
Python tier's ``linear`` scenario so the two tiers cross-validate at one point.

Precedence (docs/SIM.md §8): a cell with ``model/ams/`` is eligible for the
``ams-simulation`` gate; ``model/behavior.py`` remains the v0 ``simulation`` gate.
Neither replaces the other yet. Discovery here is pure and filesystem-only — no
timestamps, no toolchain — so emission is a pure function of the cell.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from infersynth.catalog.loader import CellPackage

__all__ = [
    "AMS_RAIL_MARGIN",
    "AmsModuleSpec",
    "OperatingPoint",
    "ams_header_path",
    "cxx_ident",
    "format_double",
    "operating_point",
    "spec_from_cell",
    "spec_from_ports",
]

#: Rail headroom (volts) the ideal AMS output stops short of each rail — the
#: same tier convention as the v0 ``DEFAULT_RAIL_MARGIN`` (docs/SIM.md §6).
AMS_RAIL_MARGIN = 0.1

#: Default stimulus for the emitted ``linear`` scenario when a cell carries no
#: v0 testbench to borrow an operating point from.
_DEFAULT_FREQ_HZ = 1000.0
_DEFAULT_DT = 1.0e-6
_DEFAULT_N_STEPS = 1000
_DEFAULT_AMPLITUDE = 1.0
_DEFAULT_RAILS = {"VCC": 12.0, "VEE": -12.0, "GND": 0.0}


def cxx_ident(name: str) -> str:
    """Turn a hyphenated cell name into a C++ identifier (``a-b`` -> ``a_b``)."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def format_double(value: float) -> str:
    """Format *value* as a deterministic C++ ``double`` literal.

    Integral floats render as ``N.0`` (never ``N``), so the emitted source is
    stable and unambiguously floating-point.
    """
    v = float(value)
    if v == int(v) and abs(v) < 1e16:
        return f"{int(v)}.0"
    return repr(v)


def ams_header_path(cell: CellPackage) -> Path | None:
    """Return the cell's ``model/ams/<name>.h`` if present, else ``None``."""
    header = cell.path / "model" / "ams" / f"{cell.name}.h"
    return header if header.is_file() else None


@dataclass(frozen=True)
class AmsModuleSpec:
    """How to include, instantiate, and wire one cell's AMS TDF module."""

    cell_name: str  # hyphenated catalog name (== header basename)
    header: str  # header filename, e.g. "opamp-gain-noninverting.h"
    module: str  # C++ module identifier, e.g. "opamp_gain_noninverting"
    header_text: str  # the header's committed contents (bundled for a hermetic build)
    in_ports: tuple[str, ...]  # electrical inputs (driven by stimulus)
    out_ports: tuple[str, ...]  # electrical outputs (recorded)
    power_ports: tuple[str, ...]  # power rails (driven by DC)
    ctor_params: tuple[str, ...]  # gain-prefixed idiom params, sorted (ctor order)

    @property
    def ports(self) -> tuple[str, ...]:
        return self.in_ports + self.out_ports + self.power_ports


def _classify_ports(ports: dict[str, dict[str, str]]) -> tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...]
]:
    """Split cell ports into (electrical-in, electrical-out, power) — sorted."""
    ins, outs, power = [], [], []
    for pname in sorted(ports):
        spec = ports[pname]
        if spec.get("kind") == "power":
            power.append(pname)
        elif spec.get("direction") == "out":
            outs.append(pname)
        else:  # in / inout / passive electrical
            ins.append(pname)
    return tuple(ins), tuple(outs), tuple(power)


def _gain_params(idiom_params: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    """Gain-prefixed idiom params, sorted — the AMS constructor argument order."""
    return tuple(sorted(p for p in idiom_params if p.startswith("gain")))


def spec_from_ports(
    cell_name: str,
    header_text: str,
    ports: dict[str, dict[str, str]],
    ctor_params: tuple[str, ...],
) -> AmsModuleSpec:
    """Build an :class:`AmsModuleSpec` from raw port + param data (design tier)."""
    ins, outs, power = _classify_ports(ports)
    return AmsModuleSpec(
        cell_name=cell_name,
        header=f"{cell_name}.h",
        module=cxx_ident(cell_name),
        header_text=header_text,
        in_ports=ins,
        out_ports=outs,
        power_ports=power,
        ctor_params=ctor_params,
    )


def spec_from_cell(cell: CellPackage) -> AmsModuleSpec | None:
    """Build the AMS spec for *cell*, or ``None`` when it ships no AMS model."""
    header = ams_header_path(cell)
    if header is None:
        return None
    return spec_from_ports(
        cell_name=cell.name,
        header_text=header.read_text(),
        ports=cell.ports,
        ctor_params=_gain_params(cell.idiom_params),
    )


@dataclass(frozen=True)
class OperatingPoint:
    """The single deterministic point the emitted ``linear`` scenario runs at."""

    params: dict[str, float]  # ctor param name -> value (gain params)
    margin: float
    rails: dict[str, float]  # power port -> DC voltage
    amplitude: float  # stimulus sine peak (volts)
    freq_hz: float
    dt: float
    n_steps: int


def _load_module(path: Path, mod_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def operating_point(cell: CellPackage, module_spec: AmsModuleSpec) -> OperatingPoint:
    """Resolve the emitted scenario's operating point for *cell*.

    Mirrors the v0 ``linear`` scenario so the AMS and Python tiers cross-validate
    at one point (WP-S2 acceptance §6): if the cell ships a v0 ``testbench/tb.py``
    its ``PARAMS`` (gain values) and ``rails``/timing are reused verbatim;
    otherwise the AMS-tier defaults (1 Vpk 1 kHz sine, ±12 V rails, 1000 × 1 µs)
    apply. Deterministic — no wall-clock, no randomness.
    """
    params: dict[str, float] = {}
    rails = dict(_DEFAULT_RAILS)
    amplitude = _DEFAULT_AMPLITUDE
    freq_hz = _DEFAULT_FREQ_HZ
    dt = _DEFAULT_DT
    n_steps = _DEFAULT_N_STEPS

    tb_path = cell.path / "testbench" / "tb.py"
    if tb_path.is_file():
        safe = cxx_ident(cell.key)
        tb = _load_module(tb_path, f"infersynth._ams_tb.{safe}")
        raw = dict(getattr(tb, "PARAMS", {}) or {})
        for pname in module_spec.ctor_params:
            if pname in raw:
                params[pname] = float(raw[pname])
        tb_rails = getattr(tb, "rails", None)
        # Prefer explicit module-level rail constants if present.
        for rail in module_spec.power_ports:
            const = getattr(tb, rail, None)
            if isinstance(const, (int, float)):
                rails[rail] = float(const)
        if getattr(tb, "GND", None) is None:
            rails.setdefault("GND", 0.0)
        if isinstance(getattr(tb, "DT", None), (int, float)):
            dt = float(tb.DT)
        if isinstance(getattr(tb, "N_STEPS", None), int):
            n_steps = int(tb.N_STEPS)
        if isinstance(getattr(tb, "FREQ_HZ", None), (int, float)):
            freq_hz = float(tb.FREQ_HZ)
        del tb_rails

    # Any ctor param the tb did not pin falls back to the cell's declared default
    # or the midpoint of its range (WP4 harness rule), so emission never crashes.
    for pname in module_spec.ctor_params:
        if pname in params:
            continue
        schema = cell.idiom_params.get(pname, {})
        if schema.get("default") is not None:
            params[pname] = float(schema["default"])
        else:
            prange = schema.get("range")
            if prange and prange[0] is not None and prange[1] is not None:
                params[pname] = float((prange[0] + prange[1]) / 2)
            else:
                params[pname] = 1.0

    # Only keep rails for ports the module actually has.
    rails = {p: rails.get(p, 0.0) for p in module_spec.power_ports}

    return OperatingPoint(
        params=params,
        margin=AMS_RAIL_MARGIN,
        rails=rails,
        amplitude=amplitude,
        freq_hz=freq_hz,
        dt=dt,
        n_steps=n_steps,
    )
