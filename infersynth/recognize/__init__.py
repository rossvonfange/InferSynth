"""Cell recognizer — Loom Pillar 2 (reverse weaving), the catalog run backwards.

Given a *design* netlist (KiCad ``kicadxml``, the shape :mod:`infersynth.gates.
netlist` exports) and a loaded :class:`~infersynth.catalog.Catalog`, recover
which catalog cells the design is built from and their parameters. Four steps
(Loom docs/LOOM.md, Pillar 2):

1. **MPN-anchored candidates** (:mod:`.index`): the catalog's
   ``selection.candidates`` reverse-indexed ``MPN -> [cell keys]``; each
   MPN-bearing component in the design yields its candidate cells.
2. **Golden-partition subgraph match** (:mod:`.match`): anchored backtracking
   subgraph isomorphism of the anchor's local netlist neighborhood against a
   candidate cell's ``golden_netlist.txt`` partition (typed nodes = component
   class + pin roles; nets = hyperedges).
3. **Parameter inversion** (:mod:`.invert`): recover idiom params by inverting
   the cell's ``bindings`` expressions against the matched components' observed
   values (exact where linear/identity, deterministic numeric bisection
   otherwise).
4. **Residual** (:mod:`.recognizer`): components claimed by no recognized cell
   instance — candidates for capture-as-a-new-cell or declared glue.

Pure and deterministic (SELECTION.md §8): no clocks, no randomness, stable
ordering — two runs are byte-identical.
"""

from __future__ import annotations

from infersynth.recognize.index import ReverseIndex, build_reverse_index
from infersynth.recognize.invert import InversionResult, invert_params
from infersynth.recognize.match import CellMatch, match_cell
from infersynth.recognize.netlist import Component, DesignNetlist, load_design_netlist
from infersynth.recognize.recognizer import (
    RecognitionResult,
    RecognizedInstance,
    recognize,
)

__all__ = [
    "CellMatch",
    "Component",
    "DesignNetlist",
    "InversionResult",
    "RecognitionResult",
    "RecognizedInstance",
    "ReverseIndex",
    "build_reverse_index",
    "invert_params",
    "load_design_netlist",
    "match_cell",
    "recognize",
]
