"""Shape-validated loading of ``resolutions_needed.json`` for the panel.

Mirrors :mod:`infersynth.panel.trace_io` / :mod:`infersynth.panel.wiring_io`:
friendly parse errors, never a raw traceback. The on-disk shape is exactly
:meth:`infersynth.pipeline.PipelineResult.resolutions_document`'s output
(see that module's docstring, "Resolution artifact round-trip" section) —
written beside a design only when :func:`infersynth.pipeline.run_pipeline`
ran (``infersynth pipeline`` / the MCP ``pipeline`` tool), not by the
one-shot :func:`infersynth.synthesize.synthesize`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["ResolutionsParseError", "load_resolutions_dict"]

_SCHEMA_ID = "infersynth.pipeline.resolutions/v0"


class ResolutionsParseError(ValueError):
    """Raised when a JSON file does not match the resolutions_needed shape."""


def _require(cond: bool, msg: str, path: Path) -> None:
    if not cond:
        raise ResolutionsParseError(f"{path}: {msg}")


def load_resolutions_dict(path: str | Path) -> dict[str, Any]:
    """Load and shape-check a ``resolutions_needed.json``-shaped file.

    Returns the raw dict — consumers only ever render it, never reconstruct
    a typed :class:`~infersynth.pipeline.PendingResolution`.
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text())
    except OSError as exc:
        raise ResolutionsParseError(f"cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolutionsParseError(f"{p}: invalid JSON: {exc}") from exc

    _require(isinstance(data, dict), "expected a JSON object", p)
    schema = data.get("schema")
    _require(schema == _SCHEMA_ID, f"schema {schema!r} != expected {_SCHEMA_ID!r}", p)
    _require("resolutions" in data, "missing 'resolutions'", p)
    resolutions = data["resolutions"]
    _require(isinstance(resolutions, list), "'resolutions' must be a list", p)
    for i, r in enumerate(resolutions):
        _require(isinstance(r, dict), f"resolutions[{i}] must be an object", p)
        for key in ("id", "kind", "subject", "options", "spec_edit"):
            _require(key in r, f"resolutions[{i}] missing {key!r}", p)
    return data
