"""Panel adapter for GateReport JSON.

The serialization logic now lives in :mod:`infersynth.gates.report_io` so the
dependency points the right way (``panel`` -> ``gates``). This module is a thin
re-export kept for the panel's import site and its tests.
"""

from __future__ import annotations

from infersynth.gates.report_io import (
    GateReportParseError,
    load_report_dict,
    report_to_dict,
)

__all__ = ["GateReportParseError", "report_to_dict", "load_report_dict"]
