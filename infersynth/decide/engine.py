"""The decision engine (WP-D1): pick the winning chain per requirement.

``decide`` is a **pure, deterministic function** of ``(match_result, catalog,
profile, lockfile)`` (SELECTION §8) — same inputs, byte-identical output, no
wall-clock read anywhere in the decision path (the only clock read in this
package is at *lock* time, in :mod:`infersynth.decide.lockfile`, a user action).

Winner-picking is WP-D1's job, not the matcher's (WP-M1 deliberately stops at
enumeration + variance triage). ``decide`` therefore:

* scores each requirement's closed candidate chains as competing covers
  (SELECTION §6, via :mod:`infersynth.decide.profiles`);
* picks the winner by lowest weighted cost, tie-broken deterministically by the
  chain's cell-key tuple (lexicographic) — documented on
  :func:`_pick_winner`;
* leaves a requirement **undecided** (never guessed) when the matcher raised a
  :class:`~infersynth.match.resolution.ResolutionRequest` for it (the ensemble is
  underspecified and must be resolved *between* runs) or when it has no closed
  chain at all. Undecided requirements are carried through, not dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.decide.costs import CostVector, compose, cost_vector_for_cell
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import WeightProfile, load_profile, score_candidates
from infersynth.match.matcher import MatchResult
from infersynth.match.provenance import CandidateChain
from infersynth.match.resolution import ResolutionRequest

__all__ = ["Finalist", "RequirementOutcome", "Decision", "decide"]

#: status values for a :class:`RequirementOutcome`.
DECIDED = "decided"
UNDECIDED_RESOLUTION = "undecided-resolution-request"
UNDECIDED_NO_CANDIDATES = "undecided-no-candidates"


@dataclass(frozen=True)
class Finalist:
    """One scored competing cover for a requirement."""

    chain: CandidateChain
    cost: CostVector
    score: float

    @property
    def key(self) -> tuple[str, ...]:
        """Deterministic tie-break key: the chain's cell-key tuple."""
        return self.chain.cells

    @property
    def unpriced(self) -> bool:
        return self.cost.unpriced


@dataclass(frozen=True)
class RequirementOutcome:
    """The decision (or non-decision) for a single requirement."""

    requirement_id: str
    status: str
    finalists: tuple[Finalist, ...] = ()
    winner: Finalist | None = None
    justification: str = ""
    #: per-dimension normalized columns aligned to ``finalists`` (trace input)
    normalized: dict[str, tuple[float, ...]] = field(default_factory=dict)

    @property
    def decided(self) -> bool:
        return self.status == DECIDED


@dataclass(frozen=True)
class Decision:
    """The decision engine's output over a whole match result."""

    profile_name: str
    lockfile_identity: str | None
    outcomes: dict[str, RequirementOutcome] = field(default_factory=dict)
    resolution_requests: tuple[ResolutionRequest, ...] = ()

    def winners(self) -> dict[str, Finalist]:
        return {
            rid: o.winner
            for rid, o in self.outcomes.items()
            if o.winner is not None
        }

    def undecided(self) -> dict[str, str]:
        """requirement id -> status, for every requirement without a winner."""
        return {rid: o.status for rid, o in self.outcomes.items() if o.winner is None}

    @property
    def all_decided(self) -> bool:
        return bool(self.outcomes) and all(o.decided for o in self.outcomes.values())


def _cost_for_chain(
    chain: CandidateChain, catalog: Catalog, lockfile: Lockfile | None
) -> CostVector:
    """Compose a chain's cost vector from its cells (lockfile overrides applied).

    A cell absent from the catalog, or one with no ``costs:`` block, contributes
    an unpriced vector — which makes the whole cover unpriced (SELECTION §6).
    """
    vectors: list[CostVector] = []
    for key in chain.cells:
        try:
            cell = catalog.get(key)
        except Exception:  # noqa: BLE001 — any resolution failure ⇒ unpriced
            vectors.append(CostVector(unpriced=True))
            continue
        costs = dict(cell.costs)
        if lockfile is not None:
            costs = lockfile.apply_overrides(cell.key, costs)
        vectors.append(cost_vector_for_cell(costs))
    return compose(vectors)


def _pick_winner(finalists: tuple[Finalist, ...]) -> tuple[Finalist, str]:
    """Lowest weighted cost wins; ties broken by lexicographic chain key.

    Deterministic and documented (SELECTION §8): the sort key is
    ``(score, chain.cells)`` — a total order, so two runs on identical inputs
    pick the identical winner. Returns ``(winner, justification)`` where the
    justification is stated in deterministic terms only (never a similarity
    score — SELECTION §7).
    """
    ranked = sorted(finalists, key=lambda f: (f.score, f.key))
    winner = ranked[0]
    tie = len(ranked) > 1 and ranked[1].score == winner.score
    priced_note = " (unpriced — charged worst-in-set)" if winner.unpriced else ""
    if tie:
        justification = (
            f"tie at weighted cost {winner.score:.4f}; broken by lexicographic "
            f"chain key {list(winner.key)}{priced_note}"
        )
    else:
        justification = (
            f"lowest weighted cost {winner.score:.4f} among "
            f"{len(finalists)} finalist(s){priced_note}"
        )
    return winner, justification


def decide(
    match_result: MatchResult,
    catalog: Catalog,
    profile: str | dict | WeightProfile,
    lockfile: Lockfile | None = None,
) -> Decision:
    """Pick a winning chain per requirement (WP-D1). Pure and deterministic.

    * requirements the matcher flagged with a :class:`ResolutionRequest` stay
      **undecided** (the ensemble is underspecified — resolve between runs);
    * requirements with no closed chain stay **undecided** (catalog gap — the
      matcher already emitted the diagnostic);
    * every other requirement is **decided** to its lowest-weighted-cost cover.
    """
    prof = load_profile(profile)
    blocked = {rr.requirement_id for rr in match_result.resolution_requests}
    outcomes: dict[str, RequirementOutcome] = {}

    for req_id, chains in match_result.chains.items():
        finalists, normalized = _score_finalists(chains, catalog, lockfile, prof)

        if req_id in blocked:
            outcomes[req_id] = RequirementOutcome(
                requirement_id=req_id,
                status=UNDECIDED_RESOLUTION,
                finalists=finalists,
                winner=None,
                justification=(
                    "undecided: matcher raised a ResolutionRequest (underspecified "
                    "ensemble); resolve between runs (SELECTION §8)"
                ),
                normalized=normalized,
            )
            continue

        if not finalists:
            outcomes[req_id] = RequirementOutcome(
                requirement_id=req_id,
                status=UNDECIDED_NO_CANDIDATES,
                justification="undecided: no closed candidate chain (catalog gap)",
            )
            continue

        winner, justification = _pick_winner(finalists)
        outcomes[req_id] = RequirementOutcome(
            requirement_id=req_id,
            status=DECIDED,
            finalists=finalists,
            winner=winner,
            justification=justification,
            normalized=normalized,
        )

    return Decision(
        profile_name=prof.name,
        lockfile_identity=lockfile.identity if lockfile is not None else None,
        outcomes=outcomes,
        resolution_requests=match_result.resolution_requests,
    )


def _score_finalists(
    chains: tuple[CandidateChain, ...],
    catalog: Catalog,
    lockfile: Lockfile | None,
    profile: WeightProfile,
) -> tuple[tuple[Finalist, ...], dict[str, tuple[float, ...]]]:
    """Build + score the finalists for one requirement (order preserved)."""
    if not chains:
        return (), {}
    vectors = [_cost_for_chain(c, catalog, lockfile) for c in chains]
    scores, per_dim = score_candidates(vectors, profile)
    finalists = tuple(
        Finalist(chain=c, cost=v, score=s)
        for c, v, s in zip(chains, vectors, scores, strict=True)
    )
    normalized = {dim: tuple(col) for dim, col in per_dim.items()}
    return finalists, normalized
