"""``selection_trace.json`` — the decision layer's explanation (SELECTION §7).

Every selection emits a machine- and human-readable trace. For each requirement
it records, in deterministic terms only:

* **candidates considered**, each with its ``surfaced_by`` provenance
  (``idiom`` / ``semantic(0.83)`` / … — disclosure is non-negotiable);
* **rejections** — the candidates a hard check threw out and *which* check
  (allocation / no-primitive / endpoint-closure), taken from the matcher's typed
  diagnostics (never re-derived here);
* **finalists** — the competing covers with their full cost vectors, the
  per-dimension normalized columns, the weighted score, and an ``unpriced`` flag;
* the **winner** and a **justification stated in deterministic terms only** — a
  weighted cost and, if needed, the lexicographic tie-break. A semantic
  ``surfaced_by`` score may appear in the provenance field but *never* in the
  justification (SELECTION §7).
* the **profile** and **lockfile identity** the run used (repeatability, §8).

Schema (``schema`` = ``"infersynth.selection_trace/1"``)::

    {
      "schema": "infersynth.selection_trace/1",
      "profile": "production",
      "lockfile": "costs.lock.json@2026-07-06T00:00:00Z" | null,
      "requirements": [
        {
          "requirement_id": str,
          "status": "decided" | "undecided-resolution-request"
                    | "undecided-no-candidates",
          "candidates_considered": [{"cell_key": str, "surfaced_by": str,
                                     "matched_keywords": [str]}],
          "rejections": [{"code": str, "severity": str, "message": str}],
          "finalists": [{"cells": [str], "surfaced_by": [str], "score": float,
                         "unpriced": bool, "cost_vector": {...},
                         "normalized": {dim: float}}],
          "winner": {"cells": [str], "score": float} | null,
          "justification": str
        }
      ]
    }

The dict :func:`build` returns is JSON-native (round-trips through
``json.dumps``/``loads`` unchanged); :func:`write` stores it beside a design and
:func:`render_text` renders the CLI/panel human summary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infersynth.catalog import Catalog
from infersynth.decide.costs import CostVector
from infersynth.decide.engine import Decision, RequirementOutcome
from infersynth.match.matcher import MatchResult

__all__ = ["SCHEMA_ID", "build", "write", "render_text"]

SCHEMA_ID = "infersynth.selection_trace/1"


def _cost_vector_json(cv: CostVector) -> dict[str, Any]:
    return {
        "bom": dict(sorted(cv.bom.items())),
        "area_mm2": cv.area_mm2,
        "power_mw": cv.power_mw,
        "part_count": cv.part_count,
        "dev_hours": cv.dev_hours,
        "production_steps": list(cv.production_steps),
        "sourcing_risk": cv.sourcing_risk,
        "unpriced": cv.unpriced,
    }


def _finalist_json(outcome: RequirementOutcome, index: int) -> dict[str, Any]:
    f = outcome.finalists[index]
    return {
        "cells": list(f.chain.cells),
        "surfaced_by": list(f.chain.surfaced_by),
        "score": f.score,
        "unpriced": f.unpriced,
        "cost_vector": _cost_vector_json(f.cost),
        "normalized": {dim: col[index] for dim, col in sorted(outcome.normalized.items())},
    }


def _rejections_for(req_id: str, match_result: MatchResult) -> list[dict[str, Any]]:
    """The matcher's typed diagnostics attributed to *req_id* (message prefix)."""
    prefix = f"{req_id}:"
    out: list[dict[str, Any]] = []
    for d in match_result.diagnostics:
        if d.message.startswith(prefix):
            out.append(
                {"code": d.code, "severity": d.severity.name, "message": d.message}
            )
    return out


def build(match_result: MatchResult, catalog: Catalog, decision: Decision) -> dict[str, Any]:
    """Assemble the selection trace as a JSON-native dict (SELECTION §7)."""
    requirements: list[dict[str, Any]] = []
    for req_id, outcome in decision.outcomes.items():
        candidates = [
            {
                "cell_key": c.cell_key,
                "surfaced_by": c.surfaced_by,
                "matched_keywords": list(c.matched_keywords),
            }
            for c in match_result.candidates.get(req_id, ())
        ]
        finalists = [_finalist_json(outcome, i) for i in range(len(outcome.finalists))]
        winner = (
            {"cells": list(outcome.winner.chain.cells), "score": outcome.winner.score}
            if outcome.winner is not None
            else None
        )
        requirements.append(
            {
                "requirement_id": req_id,
                "status": outcome.status,
                "candidates_considered": candidates,
                "rejections": _rejections_for(req_id, match_result),
                "finalists": finalists,
                "winner": winner,
                "justification": outcome.justification,
            }
        )
    return {
        "schema": SCHEMA_ID,
        "profile": decision.profile_name,
        "lockfile": decision.lockfile_identity,
        "requirements": requirements,
    }


def write(trace: dict[str, Any], path: str | Path) -> None:
    """Write the trace beside a design as ``selection_trace.json`` (deterministic:
    ``sort_keys`` off — key order is already fixed by construction — with a
    trailing newline)."""
    Path(path).write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")


def render_text(trace: dict[str, Any]) -> str:
    """A human summary of the trace for the CLI / panel."""
    lines: list[str] = []
    lines.append(f"selection trace ({trace['schema']})")
    lines.append(f"  profile:  {trace['profile']}")
    lines.append(f"  lockfile: {trace['lockfile'] or '(none — cell.yaml costs)'}")
    decided = sum(1 for r in trace["requirements"] if r["status"] == "decided")
    total = len(trace["requirements"])
    lines.append(f"  decided:  {decided}/{total} requirement(s)")
    lines.append("")
    for r in trace["requirements"]:
        lines.append(f"[{r['requirement_id']}] {r['status']}")
        for f in r["finalists"]:
            mark = "*" if (r["winner"] and f["cells"] == r["winner"]["cells"]) else " "
            flag = " UNPRICED" if f["unpriced"] else ""
            chain = " -> ".join(f["cells"]) or "(empty)"
            lines.append(f"   {mark} cost={f['score']:.4f}  {chain}{flag}")
        for rej in r["rejections"]:
            lines.append(f"     rejected [{rej['code']}]: {rej['message']}")
        lines.append(f"     -> {r['justification']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
