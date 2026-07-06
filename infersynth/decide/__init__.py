"""Decision engine (WP-D1): cost vectors × weight profiles (SELECTION §§6-8).

Consumes the matcher's :class:`~infersynth.match.matcher.MatchResult` (closed
candidate chains + typed ResolutionRequests) and picks a winning cover per
requirement:

    cost vectors (§6)  ->  weight-profile scoring (§6)  ->  deterministic
    winner pick        ->  selection_trace.json explanation (§7)

Pure and deterministic (SELECTION §8): the only external cost data comes from a
versioned :mod:`~infersynth.decide.lockfile`, and :func:`decide` never reads the
clock. Winner-picking is this layer's job; requirements the matcher left
underspecified (a ResolutionRequest) stay undecided, resolved *between* runs.

Entry point: :func:`decide`.
"""

from infersynth.decide.costs import (
    CostVector,
    compose,
    cost_vector_for_cell,
)
from infersynth.decide.engine import (
    Decision,
    Finalist,
    RequirementOutcome,
    decide,
)
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import (
    NAMED_PROFILES,
    WeightProfile,
    load_profile,
    score_candidates,
)
from infersynth.decide.trace import build as build_trace
from infersynth.decide.trace import render_text, write

__all__ = [
    "CostVector",
    "Decision",
    "Finalist",
    "Lockfile",
    "NAMED_PROFILES",
    "RequirementOutcome",
    "WeightProfile",
    "build_trace",
    "compose",
    "cost_vector_for_cell",
    "decide",
    "load_profile",
    "render_text",
    "score_candidates",
    "write",
]
