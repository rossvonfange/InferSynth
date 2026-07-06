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

    * ``recall`` — SELECTION §4 recall profile. ``strict`` = idioms only
      (the only layer that exists in WP-M1). ``semantic`` widens the net with
      the embeddings layer, which is WP-M2 scope — requesting it raises
      ``NotImplementedError`` naming WP-M2 rather than silently degrading.
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
    """

    recall: str = "strict"
    allocation: str = "lenient"
    variance_threshold: float = 0.0
    expansion_budget: int = 64

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
