"""Matcher policy knobs (SELECTION.md §4/§5/§8).

Every knob changes *how much work* a run does or *which policy profile* it
runs under, never — for a fixed ``(spec, catalog@version)`` — which answer a
deterministic layer reaches (SELECTION §8: "effort knobs change how much work
a run does, never which answer it reaches"). The knobs are a frozen value so a
``MatchKnobs`` instance is hashable and safe to share across a run.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MatchKnobs"]


@dataclass(frozen=True)
class MatchKnobs:
    """Policy profile + effort budget for one matcher run.

    * ``recall`` — SELECTION §4 recall profile. ``strict`` = idioms only.
      ``semantic`` additionally runs the Layer-2 embeddings recall (WP-M2) on
      the idiom-recall residual (RECON_HARVEST §2 cheapest-first ladder: a
      requirement that already has in-scope idiom candidates never reaches
      this layer).
    * ``allocation`` — SELECTION §2 allocation profile. Under ``strict`` a
      requirement with candidates but no allocation in its ancestry emits a
      ``frd.unallocated`` WARN; under ``lenient`` (the default) that
      diagnostic is absent.
    * ``variance_threshold`` — RECON_HARVEST §3: when the ensemble variance of
      scored closed chains for a requirement exceeds this, the matcher emits a
      typed ``ResolutionRequest`` instead of leaving the ambiguity implicit.
      The default ``0.0`` treats *any* score spread across >1 closed chain as
      underspecification (the most conservative, "ask rather than guess"
      setting).
    * ``expansion_budget`` — SELECTION §8 count-based (never wall-clock)
      candidate-expansion cap for bidirectional propagation: the maximum
      number of candidate-cell expansion steps per requirement before
      enumeration stops. Raising it only lets a run explore more; it never
      changes the ordering or the answer for chains already within budget.
    * ``semantic_threshold`` — SELECTION §4 "catalog-configured floor": the
      minimum cosine similarity for Layer-2 embeddings recall to surface a
      cell. ``0.75`` is a conservative default chosen so the
      zero-dependency :class:`~infersynth.match.embed.HashingBackend`
      placeholder (character-ngram overlap, not real semantic similarity)
      does not flood the residual with loosely-related cells; raising it
      narrows the net, lowering it widens it — either way the answer for
      candidates already above/below the floor never changes (SELECTION §8:
      effort/profile knobs change how much surfaces, never which answer a
      deterministic layer reaches for a fixed threshold).
    """

    recall: str = "strict"
    allocation: str = "lenient"
    variance_threshold: float = 0.0
    expansion_budget: int = 64
    semantic_threshold: float = 0.75

    def __post_init__(self) -> None:
        if self.recall not in ("strict", "semantic"):
            raise ValueError(f"recall must be 'strict' or 'semantic', got {self.recall!r}")
        if self.allocation not in ("strict", "lenient"):
            raise ValueError(
                f"allocation must be 'strict' or 'lenient', got {self.allocation!r}"
            )
        if self.expansion_budget < 1:
            raise ValueError(f"expansion_budget must be >= 1, got {self.expansion_budget}")
        if self.variance_threshold < 0:
            raise ValueError(
                f"variance_threshold must be >= 0, got {self.variance_threshold}"
            )
        if not (0.0 <= self.semantic_threshold <= 1.0):
            raise ValueError(
                f"semantic_threshold must be in [0.0, 1.0], got {self.semantic_threshold}"
            )
