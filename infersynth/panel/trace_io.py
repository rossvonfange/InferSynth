"""Shape-validated loading of ``selection_trace.json`` for the panel.

Mirrors :mod:`infersynth.panel.gate_io`'s approach (friendly parse errors,
never a raw traceback) but the validation itself lives here rather than in
``infersynth.decide`` — ``decide.trace`` owns :data:`SCHEMA_ID` and the
*writer* (:func:`infersynth.decide.trace.build`/``write``); the panel is the
only place that reads an on-disk trace back in as an untrusted dict, so the
reader-side shape check belongs on this side of the import boundary (panel
imports from decide, never the reverse — UX.md "thin adapter, no logic").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infersynth.decide.trace import SCHEMA_ID

__all__ = ["TraceParseError", "load_trace_dict"]


class TraceParseError(ValueError):
    """Raised when a JSON file does not match the selection_trace shape."""


def _require(cond: bool, msg: str, path: Path) -> None:
    if not cond:
        raise TraceParseError(f"{path}: {msg}")


def load_trace_dict(path: str | Path) -> dict[str, Any]:
    """Load and shape-check a ``selection_trace.json``-shaped file.

    Returns the raw dict — consumers only ever render it, never reconstruct
    a typed object (same posture as :func:`infersynth.panel.gate_io.load_report_dict`).
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text())
    except OSError as exc:
        raise TraceParseError(f"cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TraceParseError(f"{p}: invalid JSON: {exc}") from exc

    _require(isinstance(data, dict), "expected a JSON object", p)
    schema = data.get("schema")
    _require(
        schema == SCHEMA_ID,
        f"schema {schema!r} != expected {SCHEMA_ID!r}",
        p,
    )
    _require("profile" in data, "missing 'profile'", p)
    _require("requirements" in data, "missing 'requirements'", p)
    reqs = data["requirements"]
    _require(isinstance(reqs, list), "'requirements' must be a list", p)
    for i, r in enumerate(reqs):
        _require(isinstance(r, dict), f"requirements[{i}] must be an object", p)
        for key in (
            "requirement_id",
            "status",
            "candidates_considered",
            "rejections",
            "finalists",
            "winner",
            "justification",
        ):
            _require(key in r, f"requirements[{i}] missing {key!r}", p)
    return data
