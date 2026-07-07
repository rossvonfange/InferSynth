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

from infersynth.recognize.hierarchical import (
    CandidateCell,
    HierarchicalResult,
    SegmentRecognition,
    hierarchical_recognize,
    restrict_netlist,
)
from infersynth.recognize.index import ReverseIndex, build_reverse_index
from infersynth.recognize.invert import InversionResult, invert_params
from infersynth.recognize.match import CellMatch, match_cell
from infersynth.recognize.netlist import Component, DesignNetlist, load_design_netlist
from infersynth.recognize.recognizer import (
    RecognitionResult,
    RecognizedInstance,
    recognize,
)
from infersynth.recognize.segment import (
    BoundaryPin,
    LabelClaim,
    Segment,
    SegmentationResult,
    classify_nets,
    segment,
)
from infersynth.recognize.subsystem import (
    CompositionRule,
    InterfaceSig,
    SubsystemCell,
    SubsystemCellError,
    SubsystemMatch,
    load_subsystem_cell,
    load_subsystem_cells,
    match_subsystem,
    match_subsystems,
)

__all__ = [
    "BoundaryPin",
    "CandidateCell",
    "CellMatch",
    "Component",
    "CompositionRule",
    "DesignNetlist",
    "HierarchicalResult",
    "InterfaceSig",
    "InversionResult",
    "LabelClaim",
    "RecognitionResult",
    "RecognizedInstance",
    "ReverseIndex",
    "Segment",
    "SegmentRecognition",
    "SegmentationResult",
    "SubsystemCell",
    "SubsystemCellError",
    "SubsystemMatch",
    "build_reverse_index",
    "classify_nets",
    "hierarchical_recognize",
    "invert_params",
    "load_design_netlist",
    "load_subsystem_cell",
    "load_subsystem_cells",
    "match_cell",
    "match_subsystem",
    "match_subsystems",
    "recognize",
    "restrict_netlist",
    "segment",
]
