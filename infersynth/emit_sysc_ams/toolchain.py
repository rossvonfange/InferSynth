"""The real AMS kernel seam (WP-S2 deliverable 3/4): probe, compile, run, parse.

A SystemC-AMS kernel is a heavy, out-of-tree toolchain (Accellera reference impl
or a commercial simulator). This module defines the seam — an
:class:`AmsKernel` (compile / run / parse) — and ships one backend,
:class:`SystemCAmsKernel`, that drives the emitted ``Makefile`` via ``make`` and
reads back the CSV trace. **Detection is real** (:func:`detect_toolchain`): it
probes for a C++ compiler and the SystemC-AMS headers via ``SYSTEMC_HOME`` /
``SYSTEMC_AMS_HOME`` (and conventional locations). When the toolchain is absent
the caller SKIPs LOUDLY — nothing is ever faked (DESIGN.md §4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

__all__ = [
    "AmsToolchain",
    "AmsKernel",
    "AmsKernelError",
    "SystemCAmsKernel",
    "detect_toolchain",
]

#: Conventional install prefixes probed when the env vars are unset.
_DEFAULT_PREFIXES = ("/usr/local", "/usr", "/opt/systemc", "/opt")
#: Header that only SystemC-AMS ships (distinct from core SystemC's systemc.h).
_AMS_HEADER = "systemc-ams"
_AMS_HEADER_ALT = "systemc-ams.h"
#: A core-SystemC marker header.
_SYSTEMC_HEADER = "systemc.h"
_SYSTEMC_HEADER_ALT = "systemc"


@dataclass(frozen=True)
class AmsToolchain:
    """The result of probing this machine for a SystemC-AMS build toolchain."""

    available: bool
    cxx: str | None = None
    systemc_home: str | None = None
    systemc_ams_home: str | None = None
    reasons: tuple[str, ...] = ()

    def why_unavailable(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "toolchain available"


def _find_header_dir(prefixes: list[str], names: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        inc = Path(prefix) / "include"
        for name in names:
            if (inc / name).exists():
                return prefix
    return None


def detect_toolchain(env: dict[str, str] | None = None) -> AmsToolchain:
    """Probe for ``g++``/``c++`` and the SystemC + SystemC-AMS headers.

    Honors ``SYSTEMC_HOME`` / ``SYSTEMC_AMS_HOME`` first, then falls back to
    conventional prefixes. Returns ``available=False`` with concrete *reasons*
    when anything is missing — the caller turns that into a loud skip.
    """
    env = dict(os.environ if env is None else env)
    reasons: list[str] = []

    cxx = None
    for candidate in (env.get("CXX"), "g++", "c++"):
        if candidate and shutil.which(candidate):
            cxx = shutil.which(candidate)
            break
    if cxx is None:
        reasons.append("no C++ compiler (g++/c++) on PATH")

    sc_home = env.get("SYSTEMC_HOME")
    prefixes = ([sc_home] if sc_home else []) + list(_DEFAULT_PREFIXES)
    resolved_sc = _find_header_dir(prefixes, (_SYSTEMC_HEADER, _SYSTEMC_HEADER_ALT))
    if resolved_sc is None:
        reasons.append("SystemC headers (systemc.h) not found — set SYSTEMC_HOME")

    ams_home = env.get("SYSTEMC_AMS_HOME")
    ams_prefixes = ([ams_home] if ams_home else []) + list(_DEFAULT_PREFIXES)
    resolved_ams = _find_header_dir(ams_prefixes, (_AMS_HEADER, _AMS_HEADER_ALT))
    if resolved_ams is None:
        reasons.append(
            "SystemC-AMS headers (systemc-ams) not found — set SYSTEMC_AMS_HOME"
        )

    available = not reasons
    return AmsToolchain(
        available=available,
        cxx=cxx,
        systemc_home=sc_home or resolved_sc,
        systemc_ams_home=ams_home or resolved_ams,
        reasons=tuple(reasons),
    )


class AmsKernelError(RuntimeError):
    """Raised when compile or run of an emitted AMS testbench fails."""


class AmsKernel(Protocol):
    """The swap seam: compile emitted sources, run them, parse the CSV trace."""

    def compile(self, workdir: Path, target: str = "sim") -> Path: ...

    def run(self, executable: Path, csv_path: Path) -> Path: ...

    def parse(self, csv_path: Path) -> dict[str, list[float]]: ...


@dataclass
class SystemCAmsKernel:
    """Backend that builds via the emitted Makefile and reads the CSV trace."""

    toolchain: AmsToolchain
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    timeout_s: float = 120.0

    def _make_env(self) -> dict[str, str]:
        env = dict(self.env)
        if self.toolchain.systemc_home:
            env["SYSTEMC_HOME"] = self.toolchain.systemc_home
        if self.toolchain.systemc_ams_home:
            env["SYSTEMC_AMS_HOME"] = self.toolchain.systemc_ams_home
        if self.toolchain.cxx:
            env["CXX"] = self.toolchain.cxx
        return env

    def compile(self, workdir: Path, target: str = "sim") -> Path:  # pragma: no cover
        if not self.toolchain.available:
            raise AmsKernelError("SystemC-AMS toolchain unavailable")
        proc = subprocess.run(
            ["make", target],
            cwd=str(workdir),
            env=self._make_env(),
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise AmsKernelError(f"compile failed:\n{proc.stdout}\n{proc.stderr}")
        exe = workdir / target
        if not exe.is_file():
            raise AmsKernelError(f"compile produced no executable at {exe}")
        return exe

    def run(self, executable: Path, csv_path: Path) -> Path:  # pragma: no cover
        proc = subprocess.run(
            [str(executable), str(csv_path)],
            cwd=str(executable.parent),
            env=self._make_env(),
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise AmsKernelError(f"run failed:\n{proc.stdout}\n{proc.stderr}")
        if not csv_path.is_file():
            raise AmsKernelError(f"run produced no trace at {csv_path}")
        return csv_path

    def parse(self, csv_path: Path) -> dict[str, list[float]]:
        """Parse the emitted CSV (header row + numeric rows) into column traces."""
        return parse_csv_trace(csv_path.read_text())


def parse_csv_trace(text: str) -> dict[str, list[float]]:
    """Parse a headered CSV trace into ``{column: [values]}`` (pure, testable)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise AmsKernelError("empty CSV trace")
    header = [h.strip() for h in lines[0].split(",")]
    traces: dict[str, list[float]] = {h: [] for h in header}
    for row in lines[1:]:
        cells = row.split(",")
        if len(cells) != len(header):
            raise AmsKernelError(
                f"CSV row has {len(cells)} fields, expected {len(header)}: {row!r}"
            )
        for h, cell in zip(header, cells, strict=True):
            traces[h].append(float(cell))
    return traces
