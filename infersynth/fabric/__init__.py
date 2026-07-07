"""Fabric stuffing synthesis (docs/FABRIC.md) — v0.

The gate-array / structured-ASIC model applied to PCBs: a **fabric** is a
fully placed AND routed ``.kicad_pcb`` whose components are organized as
**sites**. A decided requirement set is compiled onto a fabric by
POPULATION — no placement, no routing:

* :func:`~infersynth.fabric.loader.load_fabric` loads + validates a
  ``fabric.yaml`` against a catalog (deliverable 1);
* :func:`~infersynth.fabric.fit.fit` assigns each decided winner to a
  compatible free site, reports utilization + doesn't-fit diagnostics, and
  computes value-stuffing from the winning params (deliverable 2);
* :func:`~infersynth.fabric.stuff.stuff` copies the fabric board, sets the
  ``dnp`` / ``exclude_from_bom`` attributes on unstuffed-site footprints by
  TEXT SURGERY (never sexpdata round-trips — house rule), updates stuffed
  value-parametric refs, and emits ``STUFFING.md`` + a stuffing BOM CSV
  (deliverable 3).
"""

from __future__ import annotations

from infersynth.fabric.fit import (
    FitResult,
    StuffedSite,
    StuffValue,
    extracted_params_for_winners,
    fit,
)
from infersynth.fabric.loader import (
    Fabric,
    FabricError,
    Site,
    TieOff,
    load_fabric,
)
from infersynth.fabric.stuff import StuffResult, stuff

__all__ = [
    "Fabric",
    "FabricError",
    "Site",
    "TieOff",
    "load_fabric",
    "FitResult",
    "StuffValue",
    "StuffedSite",
    "extracted_params_for_winners",
    "fit",
    "StuffResult",
    "stuff",
]
