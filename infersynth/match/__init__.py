"""Catalog matching: spec idioms/semantics -> candidate cells + chains.

The v2 "Infer" matcher, layer by SELECTION.md §4's pipeline order:

    idiom recall  ->  semantic recall (residual only, WP-M2)
                  ->  allocation scoping  ->  bidirectional endpoint
                  propagation             ->  chain scoring
                  ->  ensemble-variance triage

It is a **pure, deterministic function** of ``(requirement set, catalog,
allocations, knobs, embedding_backend)`` (SELECTION §8): same inputs,
byte-identical output, sorted everywhere, no wall-clock, no randomness.
``recall="strict"`` (default) runs idioms only; ``recall="semantic"``
additionally cosine-scores each idiom-recall residual against the catalog's
cached ``embedding.json`` vectors (:mod:`infersynth.match.embed`) — embeddings
only ever SURFACE candidates, they never decide or resolve. Winner-picking is
WP-D1's job: this layer enumerates and, when the scored candidate ensemble is
underspecified (RECON_HARVEST §3), emits a typed :class:`ResolutionRequest`
instead of guessing.

Entry point: :func:`match`. See :mod:`infersynth.match.matcher`.
"""

from infersynth.match.allocation import (
    Allocation,
    AllocationError,
    AllocationTable,
    allocations_from_spec,
    resolve_scope,
)
from infersynth.match.embed import (
    EmbeddingBackend,
    HashingBackend,
    SentenceTransformersBackend,
    backend_from_name,
    build_semantic_index,
)
from infersynth.match.knobs import MatchKnobs
from infersynth.match.matcher import MatchResult, match
from infersynth.match.propagate import EndpointSpec, propagate_chains
from infersynth.match.provenance import Candidate, CandidateChain
from infersynth.match.recall import idiom_recall, semantic_recall
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
    "EmbeddingBackend",
    "EndpointSpec",
    "HashingBackend",
    "MatchKnobs",
    "MatchResult",
    "ResolutionOption",
    "ResolutionRequest",
    "SentenceTransformersBackend",
    "SimGateScorer",
    "StructuralScorer",
    "allocations_from_spec",
    "backend_from_name",
    "build_semantic_index",
    "ensemble_variance",
    "idiom_recall",
    "match",
    "propagate_chains",
    "resolve_scope",
    "score_chains",
    "semantic_recall",
]
