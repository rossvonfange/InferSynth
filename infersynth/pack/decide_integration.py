"""Feed packed covers into the decision layer as competing finalists (WP-P1 item 5).

SELECTION §6 rule 1 — *covers compete against covers*: a packed cover is never
compared against one discrete cell in isolation, but against the **best
composition** of the discrete cells covering the *same* requirement set. For
homogeneous packing that composition is simply the N discrete singles. This
module builds, for one :class:`~infersynth.pack.homogeneous.PackClaim`, a
**synthetic combined outcome** whose two finalists are

    * the **packed** cover  — one pack-target cell (``opamp-gain-x4-noninverting``);
    * the **composition** cover — N discrete source cells summed
      (``opamp-gain-noninverting`` × N).

Both are costed over the *identical* requirement set and scored through the exact
WP-D1 machinery (:func:`infersynth.decide.profiles.score_candidates` +
:class:`~infersynth.decide.engine.Finalist`/:class:`RequirementOutcome`), so the
plumbing is strictly **additive** — it does not touch ``decide()``. The profile
flip is the point (SELECTION §6): at ``production`` (qty1k) weights the single
quad package beats four discrete op-amps on BOM/area; at ``prototype`` weights a
lockfile that prices a per-channel calibration step onto the quad can keep the
four discretes ahead — the same requirement set, opposite winners.

The synthetic outcome's ``requirement_id`` is ``pack:<r1>+<r2>+…`` — a stable,
deterministic composite key over the absorbed set.
"""

from __future__ import annotations

from infersynth.catalog import Catalog
from infersynth.decide.costs import CostVector, compose, cost_vector_for_cell
from infersynth.decide.engine import DECIDED, Finalist, RequirementOutcome
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import WeightProfile, load_profile, score_candidates
from infersynth.match.provenance import CandidateChain
from infersynth.pack.homogeneous import PackClaim

__all__ = ["synthetic_requirement_id", "pack_finalists", "pack_outcome"]


def synthetic_requirement_id(claim: PackClaim) -> str:
    """The composite requirement id for *claim*'s synthetic combined outcome."""
    return "pack:" + "+".join(claim.absorbed)


def _cell_cost(cell_key: str, catalog: Catalog, lockfile: Lockfile | None) -> CostVector:
    """One cell's cost vector (lockfile overrides applied); unpriced if unknown.

    Mirrors :func:`infersynth.decide.engine._cost_for_chain`'s per-cell rule so a
    packed cover is priced on exactly the same basis as any other cover.
    """
    try:
        cell = catalog.get(cell_key)
    except Exception:  # noqa: BLE001 — any resolution failure ⇒ unpriced
        return CostVector(unpriced=True)
    costs = dict(cell.costs)
    if lockfile is not None:
        costs = lockfile.apply_overrides(cell.key, costs)
    return cost_vector_for_cell(costs)


def pack_finalists(
    claim: PackClaim,
    catalog: Catalog,
    profile: str | dict | WeightProfile,
    lockfile: Lockfile | None = None,
) -> tuple[Finalist, Finalist]:
    """Build the (packed, composition) finalists for *claim*, scored under *profile*.

    Returns them in a fixed order — packed first, composition second — each a
    :class:`Finalist` carrying its :class:`CandidateChain`, composed cost vector,
    and weighted score over the two-cover set (SELECTION §6 covers-vs-covers).
    """
    prof = load_profile(profile)
    rid = synthetic_requirement_id(claim)
    n = len(claim.absorbed)

    packed_chain = CandidateChain.make(
        rid, (claim.target_cell,), closed=True, surfaced_by=("pack",)
    )
    comp_chain = CandidateChain.make(
        rid, (claim.source_cell,) * n, closed=True, surfaced_by=("idiom",) * n
    )

    packed_cost = _cell_cost(claim.target_cell, catalog, lockfile)
    comp_cost = compose([_cell_cost(claim.source_cell, catalog, lockfile)] * n)

    scores, _per_dim = score_candidates([packed_cost, comp_cost], prof)
    packed = Finalist(chain=packed_chain, cost=packed_cost, score=scores[0])
    composition = Finalist(chain=comp_chain, cost=comp_cost, score=scores[1])
    return packed, composition


def pack_outcome(
    claim: PackClaim,
    catalog: Catalog,
    profile: str | dict | WeightProfile,
    lockfile: Lockfile | None = None,
) -> RequirementOutcome:
    """Score the packed cover against the discrete composition (WP-P1 item 5).

    A synthetic :class:`RequirementOutcome` over the combined requirement set:
    lowest weighted cost wins, ties broken by the lexicographic chain key (the
    same deterministic rule WP-D1 uses — SELECTION §8). The justification is in
    deterministic terms only (a weighted cost, never a provenance score).
    """
    packed, composition = pack_finalists(claim, catalog, profile, lockfile)
    finalists = (packed, composition)
    ranked = sorted(finalists, key=lambda f: (f.score, f.chain.cells))
    winner = ranked[0]
    tie = ranked[1].score == winner.score
    kind = "pack" if winner is packed else "discrete composition"
    if tie:
        justification = (
            f"tie at weighted cost {winner.score:.4f}; broken by lexicographic "
            f"chain key {list(winner.chain.cells)} ({kind})"
        )
    else:
        justification = (
            f"{kind} wins at weighted cost {winner.score:.4f} vs "
            f"{ranked[1].score:.4f} (covers-vs-covers over {len(claim.absorbed)} "
            "requirements, SELECTION §6)"
        )
    return RequirementOutcome(
        requirement_id=synthetic_requirement_id(claim),
        status=DECIDED,
        finalists=finalists,
        winner=winner,
        justification=justification,
    )
