"""Part binding: real MPNs, component values, footprints (DESIGN.md sections 2 and 10).

Scored, explainable selection over cell selection metadata. This pass ships
the binding-expression evaluator (:mod:`infersynth.bind.expr`, BUILD_PLAN WP1);
the scoring engine is v2 scope.
"""

from infersynth.bind.expr import BindingError, bind_cell, evaluate, free_names
from infersynth.bind.parts import BindResult, PartBinding, bind_parts, fragment_refs

__all__ = [
    "BindResult",
    "BindingError",
    "PartBinding",
    "bind_cell",
    "bind_parts",
    "evaluate",
    "fragment_refs",
    "free_names",
]
