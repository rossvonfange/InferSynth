"""Shape-validated loading of ``wiring_plan.json`` for the panel.

Mirrors :mod:`infersynth.panel.trace_io`'s posture: friendly parse errors,
never a raw traceback, reader-side shape check kept on the panel's side of
the import boundary (panel imports from ``gates``/``netflow``, never the
reverse). The on-disk shape is exactly :func:`infersynth.gates.design_netlist.
plan_to_dict`'s output — ``{"instances": {instname: cell_key}, "nets": [
{"kind", "name", "members": [[instname, port], ...]}, ...]}`` — persisted by
:mod:`infersynth.synthesize` only when ``verify=True`` produced a wiring
plan. There is no ``driven``/diagnostics field in this artifact (round-trip
is lossy by design, see ``plan_to_dict``'s docstring); the panel sources
those from ``SYNTHESIS.md`` instead (see ``infersynth.panel.app``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["WiringPlanParseError", "load_wiring_plan_dict"]


class WiringPlanParseError(ValueError):
    """Raised when a JSON file does not match the wiring_plan.json shape."""


def _require(cond: bool, msg: str, path: Path) -> None:
    if not cond:
        raise WiringPlanParseError(f"{path}: {msg}")


def load_wiring_plan_dict(path: str | Path) -> dict[str, Any]:
    """Load and shape-check a ``wiring_plan.json``-shaped file.

    Returns the raw dict — consumers only ever render it, never reconstruct
    a typed :class:`~infersynth.netflow.plan.WiringPlan` (same posture as
    :func:`infersynth.panel.trace_io.load_trace_dict`).
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text())
    except OSError as exc:
        raise WiringPlanParseError(f"cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WiringPlanParseError(f"{p}: invalid JSON: {exc}") from exc

    _require(isinstance(data, dict), "expected a JSON object", p)
    _require("instances" in data, "missing 'instances'", p)
    _require(isinstance(data["instances"], dict), "'instances' must be an object", p)
    _require("nets" in data, "missing 'nets'", p)
    nets = data["nets"]
    _require(isinstance(nets, list), "'nets' must be a list", p)
    for i, n in enumerate(nets):
        _require(isinstance(n, dict), f"nets[{i}] must be an object", p)
        for key in ("kind", "name", "members"):
            _require(key in n, f"nets[{i}] missing {key!r}", p)
        _require(isinstance(n["members"], list), f"nets[{i}].members must be a list", p)
        for j, m in enumerate(n["members"]):
            _require(
                isinstance(m, list) and len(m) == 2,
                f"nets[{i}].members[{j}] must be a 2-element [instname, port]",
                p,
            )
    return data
