"""Candidate + chain value types with mandatory ``surfaced_by`` provenance.

SELECTION §7: "Provenance is recorded per candidate: ``surfaced_by: idiom |
semantic(0.83) | allocation-forced``, and appears in the explanation.
Disclosure is non-negotiable." Every :class:`Candidate` therefore carries a
non-empty ``surfaced_by`` string, and constructing one without it is an error.

These are the matcher's public output atoms; they are frozen and ordered so a
:class:`~infersynth.match.matcher.MatchResult` sorts deterministically
(SELECTION §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.lint.vocab import ParamBinding

__all__ = ["surfaced_by_idiom", "surfaced_by_semantic", "Candidate", "CandidateChain"]


def surfaced_by_idiom() -> str:
    """Provenance tag for a Layer-1 idiom-recall candidate (SELECTION §4/§7)."""
    return "idiom"


def surfaced_by_semantic(score: float) -> str:
    """Provenance tag for a Layer-2 embeddings candidate — WP-M2 only.

    Included for interface completeness / provenance-string discipline; WP-M1
    never emits it (the semantic recall layer is WP-M2).
    """
    return f"semantic({score:.2f})"


@dataclass(frozen=True, order=True)
class Candidate:
    """One catalog cell surfaced as a candidate for one requirement.

    Ordered by ``(requirement_id, cell_key)`` so a candidate list has a single
    deterministic sort key independent of discovery order (SELECTION §8).
    ``matched_keywords`` and ``params`` are the auditable idiom-recall evidence
    (which keywords matched, which numeric params were bound + range-checked).
    """

    requirement_id: str
    cell_key: str
    surfaced_by: str
    matched_keywords: tuple[str, ...] = field(default=(), compare=False)
    params: tuple[ParamBinding, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if not self.surfaced_by:
            raise ValueError(
                f"candidate {self.cell_key!r} for {self.requirement_id!r} has no "
                "surfaced_by provenance (SELECTION §7: disclosure is non-negotiable)"
            )


@dataclass(frozen=True, order=True)
class CandidateChain:
    """An ordered chain of candidate cells bridging a requirement's endpoints.

    ``cells`` are cell keys in signal order (source end first, sink end last).
    ``closed`` is True when the chain spans a declared input endpoint to a
    declared output endpoint (RECON_HARVEST §3 bidirectional closure); a
    trivial single-cell chain for a requirement with no declared endpoints is
    closed by definition. ``surfaced_by`` mirrors ``cells`` position-for-
    position. ``score`` is filled by the scoring pass (None until then).

    Ordered by ``(requirement_id, len(cells), cells)`` — a fixed, total tie-
    break so two runs enumerate chains byte-identically (SELECTION §8).
    """

    requirement_id: str
    length: int
    cells: tuple[str, ...]
    closed: bool = field(compare=False)
    surfaced_by: tuple[str, ...] = field(default=(), compare=False)
    score: float | None = field(default=None, compare=False)

    @classmethod
    def make(
        cls,
        requirement_id: str,
        cells: tuple[str, ...],
        *,
        closed: bool,
        surfaced_by: tuple[str, ...],
        score: float | None = None,
    ) -> CandidateChain:
        return cls(
            requirement_id=requirement_id,
            length=len(cells),
            cells=cells,
            closed=closed,
            surfaced_by=surfaced_by,
            score=score,
        )

    def with_score(self, score: float) -> CandidateChain:
        return CandidateChain(
            requirement_id=self.requirement_id,
            length=self.length,
            cells=self.cells,
            closed=self.closed,
            surfaced_by=self.surfaced_by,
            score=score,
        )
