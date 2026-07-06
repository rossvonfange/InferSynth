"""Catalog matching (WP-M1): spec idioms -> candidate cells + chains.

The v2 "Infer" matcher, layer by SELECTION.md §4's pipeline order:

    idiom recall  ->  allocation scoping  ->  bidirectional endpoint
    propagation   ->  chain scoring        ->  ensemble-variance triage

It is a **pure, deterministic function** of ``(requirement set, catalog,
allocations, knobs)`` (SELECTION §8): same inputs, byte-identical output,
sorted everywhere, no wall-clock, no randomness. The only recall layer present
is idioms (SELECTION §4 "strict"); the semantic/embeddings layer is WP-M2 and
requesting it (``recall='semantic'``) raises ``NotImplementedError`` naming
that WP. Winner-picking is WP-D1's job: this layer enumerates and, when the
scored candidate ensemble is underspecified (RECON_HARVEST §3), emits a typed
:class:`ResolutionRequest` instead of guessing.

Entry point: :func:`match`. See :mod:`infersynth.match.matcher`.
"""

from infersynth.match.allocation import (
    Allocation,
    AllocationError,
    AllocationTable,
    allocations_from_spec,
    resolve_scope,
)
from infersynth.match.knobs import MatchKnobs
from infersynth.match.matcher import MatchResult, match
from infersynth.match.propagate import EndpointSpec, propagate_chains
from infersynth.match.provenance import Candidate, CandidateChain
from infersynth.match.recall import idiom_recall
from infersynth.match.resolution import (
    ChainScorer,
    ResolutionOption,
    ResolutionRequest,
    SimGateScorer,
    StructuralScorer,
    ensemble_variance,
    score_chains,
)

__all__ = [
    "Allocation",
    "AllocationError",
    "AllocationTable",
    "Candidate",
    "CandidateChain",
    "ChainScorer",
    "EndpointSpec",
    "MatchKnobs",
    "MatchResult",
    "ResolutionOption",
    "ResolutionRequest",
    "SimGateScorer",
    "StructuralScorer",
    "allocations_from_spec",
    "ensemble_variance",
    "idiom_recall",
    "match",
    "propagate_chains",
    "resolve_scope",
    "score_chains",
]
