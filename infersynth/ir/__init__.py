"""InferSynth IR: the Python-embedded structural DSL (DESIGN.md section 4)."""

from infersynth.ir.core import (
    BOUNDARY,
    Cell,
    Design,
    Instance,
    IRError,
    Net,
    Param,
    Port,
    PortDirection,
    PortKind,
)
from infersynth.ir.elaborate import (
    ElaboratedDesign,
    ElaboratedInstance,
    ElaborationError,
    elaborate,
)

__all__ = [
    "BOUNDARY",
    "Cell",
    "Design",
    "ElaboratedDesign",
    "ElaboratedInstance",
    "ElaborationError",
    "Instance",
    "IRError",
    "Net",
    "Param",
    "Port",
    "PortDirection",
    "PortKind",
    "elaborate",
]
