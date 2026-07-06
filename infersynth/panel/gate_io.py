"""JSON (de)serialization of :class:`infersynth.gates.GateReport` for the panel.

The gates package has no JSON I/O of its own (a ``GateRunner`` is driven
in-process); the sidecar is the first consumer that needs a report *file*
(``infersynth gates`` output, or hand-authored fixtures). The shape is
intentionally the direct field-for-field mirror of ``GateResult`` so it can't
drift silently:

    {"results": [{"gate": str, "status": "pass"|"fail"|"skipped",
                  "diagnostics": [str, ...]}, ...]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infersynth.gates import GateReport, GateStatus

__all__ = ["GateReportParseError", "report_to_dict", "load_report_dict"]


class GateReportParseError(ValueError):
    """Raised when a JSON file does not match the GateReport shape."""


def report_to_dict(report: GateReport) -> dict[str, Any]:
    """The authoritative serialization, built from the real dataclasses."""
    return {
        "results": [
            {
                "gate": r.gate,
                "status": GateStatus(r.status).value,
                "diagnostics": list(r.diagnostics),
            }
            for r in report.results
        ]
    }


def load_report_dict(path: str | Path) -> dict[str, Any]:
    """Load and shape-check a GateReport-shaped JSON file.

    Returns the raw dict (gate names/status/diagnostics) rather than
    reconstructing a ``GateReport`` — the panel only ever displays it.
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text())
    except OSError as exc:
        raise GateReportParseError(f"cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GateReportParseError(f"{p}: invalid JSON: {exc}") from exc

    if not isinstance(data, dict) or "results" not in data:
        raise GateReportParseError(f"{p}: expected an object with a 'results' list")
    results = data["results"]
    if not isinstance(results, list):
        raise GateReportParseError(f"{p}: 'results' must be a list")

    valid_statuses = {s.value for s in GateStatus}
    normalized = []
    for i, r in enumerate(results):
        if not isinstance(r, dict) or "gate" not in r or "status" not in r:
            raise GateReportParseError(f"{p}: results[{i}] missing 'gate'/'status'")
        status = r["status"]
        if status not in valid_statuses:
            raise GateReportParseError(
                f"{p}: results[{i}].status {status!r} not one of {sorted(valid_statuses)}"
            )
        normalized.append(
            {
                "gate": str(r["gate"]),
                "status": status,
                "diagnostics": [str(d) for d in r.get("diagnostics", [])],
            }
        )
    return {"results": normalized}
