"""Gate runner with pluggable gates and structured results (DESIGN.md section 7).

Every gate returns a :class:`GateResult` (pass/fail/skipped + diagnostics).
Skipped gates are LOUD in the report (DESIGN.md section 4: graceful degradation
must never be silent).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["GateStatus", "GateResult", "GateReport", "GateRunner"]


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class GateResult:
    """Structured outcome of one gate run."""

    gate: str
    status: GateStatus
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def passed(cls, gate: str, *diagnostics: str) -> GateResult:
        return cls(gate=gate, status=GateStatus.PASS, diagnostics=tuple(diagnostics))

    @classmethod
    def failed(cls, gate: str, *diagnostics: str) -> GateResult:
        return cls(gate=gate, status=GateStatus.FAIL, diagnostics=tuple(diagnostics))

    @classmethod
    def skipped(cls, gate: str, reason: str) -> GateResult:
        return cls(gate=gate, status=GateStatus.SKIPPED, diagnostics=(reason,))


@dataclass
class GateReport:
    """Aggregate of all gate results for one run."""

    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no gate failed. Skipped gates do not fail the run,
        but they are reported loudly."""
        return all(r.status != GateStatus.FAIL for r in self.results)

    @property
    def skipped(self) -> list[GateResult]:
        return [r for r in self.results if r.status == GateStatus.SKIPPED]

    def summary(self) -> str:
        lines = []
        for r in self.results:
            if r.status == GateStatus.SKIPPED:
                # Loud by design: a skipped gate is unverified, not verified.
                lines.append(f"!! GATE SKIPPED !! {r.gate}: {'; '.join(r.diagnostics)}")
            else:
                lines.append(f"[{r.status.value.upper():4s}] {r.gate}")
                lines.extend(f"    {d}" for d in r.diagnostics)
        if self.skipped:
            lines.append(
                f"!! {len(self.skipped)} gate(s) SKIPPED — the design is NOT fully "
                "verified !!"
            )
        lines.append("RESULT: " + ("OK" if self.ok else "FAILED"))
        return "\n".join(lines)


#: A gate is any callable taking a context mapping and returning a GateResult.
Gate = Callable[[Mapping[str, Any]], GateResult]


class GateRunner:
    """Runs registered gates in registration order and aggregates results.

    The *context* mapping carries whatever the gates need (elaborated design,
    schematic paths, exported netlists, ...); each gate documents its keys and
    returns SKIPPED when its inputs or toolchain are unavailable.
    """

    def __init__(self) -> None:
        self._gates: list[tuple[str, Gate]] = []

    def register(self, name: str, gate: Gate) -> None:
        if any(n == name for n, _ in self._gates):
            raise ValueError(f"gate {name!r} already registered")
        self._gates.append((name, gate))

    @property
    def gate_names(self) -> list[str]:
        return [n for n, _ in self._gates]

    def run(self, context: Mapping[str, Any]) -> GateReport:
        report = GateReport()
        for name, gate in self._gates:
            try:
                result = gate(context)
            except Exception as exc:  # a crashing gate is a failing gate
                result = GateResult.failed(name, f"gate raised {type(exc).__name__}: {exc}")
            report.results.append(result)
        return report
