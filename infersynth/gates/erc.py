"""ERC gate — shells to ``kicad-cli sch erc`` and triages the result (WP4).

The oracle NEVER shares code with the writer (UX.md): this module runs the
real KiCad ERC engine on the ROOT schematic and reads its JSON verdict, rather
than reasoning about the schematic in Python.

``kicad-cli 10`` invocation (flags verified against ``kicad-cli sch erc
--help``)::

    kicad-cli sch erc --format json --severity-all -o <report.json> <root.kicad_sch>

The JSON shape (``$schema`` erc.v1) is ``{"sheets": [{"path", "violations":
[{"type", "severity", "description", "items": [{"description", ...}]}]}]}``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from infersynth.gates.runner import GateResult
from infersynth.gates.triage import ErcViolation, TriagePolicy

__all__ = ["ErcError", "kicad_cli_available", "run_erc", "erc_gate"]

_ERC_GATE = "erc"


class ErcError(RuntimeError):
    """Raised when ``kicad-cli sch erc`` cannot be run or its output parsed."""


def kicad_cli_available() -> bool:
    """True when a ``kicad-cli`` binary is on PATH."""
    return shutil.which("kicad-cli") is not None


def _parse_erc_json(data: Mapping[str, Any]) -> list[ErcViolation]:
    violations: list[ErcViolation] = []
    for sheet in data.get("sheets", []):
        sheet_path = str(sheet.get("path", ""))
        for v in sheet.get("violations", []):
            items = tuple(
                str(it.get("description", "")) for it in v.get("items", []) if it.get("description")
            )
            violations.append(
                ErcViolation(
                    code=str(v.get("type", "unknown")),
                    severity=str(v.get("severity", "error")),
                    description=str(v.get("description", "")),
                    items=items,
                    sheet=sheet_path,
                )
            )
    return violations


def run_erc(schematic_path: str | Path) -> list[ErcViolation]:
    """Run ERC on *schematic_path* (a root .kicad_sch); return its violations.

    Runs full-hierarchy ERC from the root sheet. Raises :class:`ErcError` if
    ``kicad-cli`` is missing, fails to load the schematic, or emits no report.
    """
    root = Path(schematic_path)
    if not kicad_cli_available():
        raise ErcError("kicad-cli not found on PATH")
    if not root.is_file():
        raise ErcError(f"schematic not found: {root}")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "erc.json"
        proc = subprocess.run(
            [
                "kicad-cli",
                "sch",
                "erc",
                "--format",
                "json",
                "--severity-all",
                "-o",
                str(report),
                str(root),
            ],
            capture_output=True,
            text=True,
        )
        if not report.is_file():
            raise ErcError(
                f"kicad-cli sch erc produced no report (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            data = json.loads(report.read_text())
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ErcError(f"unparseable ERC report: {exc}") from exc
    return _parse_erc_json(data)


def erc_gate(context: Mapping[str, Any]) -> GateResult:
    """ERC-zero gate (DESIGN.md section 7). Context keys:

    * ``schematic_path``: root .kicad_sch of the design/harness to check.
    * ``triage_policy``: optional :class:`TriagePolicy` (default: errors fail,
      warnings pass-with-note).

    SKIPPED (loudly) when ``schematic_path`` is absent or ``kicad-cli`` is not
    installed — an unverified design is never reported as verified.
    """
    schematic = context.get("schematic_path")
    if schematic is None:
        return GateResult.skipped(_ERC_GATE, "missing context: schematic_path")
    if not kicad_cli_available():
        return GateResult.skipped(_ERC_GATE, "kicad-cli not on PATH; ERC not run")

    policy = context.get("triage_policy") or TriagePolicy.default()
    try:
        violations = run_erc(schematic)
    except ErcError as exc:
        return GateResult.failed(_ERC_GATE, f"ERC could not run: {exc}")

    outcome = policy.triage(violations)
    n_err = sum(1 for v in violations if v.severity == "error")
    n_warn = sum(1 for v in violations if v.severity == "warning")
    header = f"{n_err} error(s), {n_warn} warning(s); {len(outcome.notes)} triaged as notes"
    if outcome.ok:
        return GateResult.passed(_ERC_GATE, header, *outcome.notes)
    return GateResult.failed(
        _ERC_GATE, header, *(f"FAIL {v.one_line()}" for v in outcome.failures), *outcome.notes
    )
