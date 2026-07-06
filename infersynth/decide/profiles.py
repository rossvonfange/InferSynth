"""Weight profiles + deterministic weighted-normalized scoring (SELECTION §6).

A **weight profile** turns a set of competing cost vectors into scores. It
carries (a) a weight per scored dimension and (b) a BOM qty tier the ``bom``
dimension resolves against. Lower score = cheaper = better (these are *costs*).

Named defaults (SELECTION §6 "mirror the FRD personas"):

* ``prototype`` — firmware/config engineering is the expensive thing; BOM barely
  matters at qty1. dev_hours and production_steps dominate. This is the "555s
  win" persona: discrete parts with zero code beat a part you must program.
* ``production`` — BOM at volume and board area dominate; a few dev_hours to
  program a part amortize to nothing across a run. This is the "PIC wins"
  persona: the programmable part's low per-unit BOM wins at qty1k.
* ``hobbyist`` — part_count (fewer things to hand-solder) and sourcing_risk
  (get it from one distributor) dominate; BOM at qty1.

The named weights below are the knob that does the real work: the *same*
requirement set flips winners as the profile changes (the PIC-vs-555 flip,
SELECTION §6). Inline explicit weight dicts (``{"bom": 5, "area_mm2": 2, …}``)
are also accepted via :meth:`WeightProfile.from_weights`.

**Normalization (documented, deterministic).** Per-dimension min–max over the
candidate set: for dimension ``d`` with values ``x`` across the finalists,
``norm_d(x) = (x - min_d) / (max_d - min_d)``. A degenerate range
(``max_d == min_d`` — every finalist equal on that dimension) contributes ``0``
for all (no discriminating power), so ties are broken by other dimensions, never
by a divide-by-zero. Only *priced* finalists set ``min_d`` / ``max_d``; an
**unpriced** finalist takes ``norm_d = 1.0`` (worst-in-set) on *every* dimension
(SELECTION §6 honesty rule). The score is ``Σ_d weight_d · norm_d``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from infersynth.decide.costs import SCORE_DIMENSIONS, CostVector

__all__ = ["WeightProfile", "NAMED_PROFILES", "load_profile", "score_candidates"]


@dataclass(frozen=True)
class WeightProfile:
    """A named or inline weight profile over the 7 scored dimensions."""

    name: str
    weights: dict[str, float]
    bom_qty: str = "qty1"

    def weight(self, dim: str) -> float:
        return float(self.weights.get(dim, 0.0))

    @classmethod
    def from_weights(
        cls, weights: dict[str, Any], *, name: str = "inline", bom_qty: str = "qty1"
    ) -> WeightProfile:
        """Build an inline profile from an explicit ``{dim: weight}`` mapping.

        Unknown dimension keys are rejected (a typo must not silently score 0);
        a ``bom_qty`` key inside *weights* selects the BOM tier and is stripped
        from the weight set.
        """
        cleaned = dict(weights)
        qty = str(cleaned.pop("bom_qty", bom_qty))
        unknown = sorted(set(cleaned) - set(SCORE_DIMENSIONS))
        if unknown:
            raise ValueError(
                f"unknown cost dimension(s) {unknown} in weight profile "
                f"(known: {sorted(SCORE_DIMENSIONS)})"
            )
        return cls(name=name, weights={k: float(v) for k, v in cleaned.items()}, bom_qty=qty)


#: SELECTION §6 named defaults. Weights chosen so the persona each names wins the
#: candidate it should; rationale in the module docstring.
NAMED_PROFILES: dict[str, WeightProfile] = {
    "prototype": WeightProfile(
        name="prototype",
        weights={
            "bom": 1.0,
            "area_mm2": 1.0,
            "power_mw": 1.0,
            "part_count": 1.0,
            "dev_hours": 8.0,
            "production_steps": 6.0,
            "sourcing_risk": 2.0,
        },
        bom_qty="qty1",
    ),
    "production": WeightProfile(
        name="production",
        weights={
            "bom": 8.0,
            "area_mm2": 5.0,
            "power_mw": 2.0,
            "part_count": 2.0,
            "dev_hours": 1.0,
            "production_steps": 2.0,
            "sourcing_risk": 3.0,
        },
        bom_qty="qty1k",
    ),
    "hobbyist": WeightProfile(
        name="hobbyist",
        weights={
            "bom": 3.0,
            "area_mm2": 1.0,
            "power_mw": 1.0,
            "part_count": 5.0,
            "dev_hours": 2.0,
            "production_steps": 3.0,
            "sourcing_risk": 4.0,
        },
        bom_qty="qty1",
    ),
}


def load_profile(spec: str | dict[str, Any] | WeightProfile) -> WeightProfile:
    """Resolve *spec* to a :class:`WeightProfile`.

    * a :class:`WeightProfile` is returned as-is;
    * a ``str`` names one of :data:`NAMED_PROFILES` (else ``ValueError``);
    * a ``dict`` is an inline explicit weight set (:meth:`from_weights`).
    """
    if isinstance(spec, WeightProfile):
        return spec
    if isinstance(spec, str):
        try:
            return NAMED_PROFILES[spec]
        except KeyError:
            raise ValueError(
                f"unknown weight profile {spec!r} (named: {sorted(NAMED_PROFILES)})"
            ) from None
    if isinstance(spec, dict):
        return WeightProfile.from_weights(spec)
    raise TypeError(f"cannot load weight profile from {type(spec).__name__}")


def _normalized(values: list[float], unpriced: list[bool]) -> list[float]:
    """Per-dimension min–max over *priced* values; unpriced → 1.0 (worst)."""
    priced = [v for v, up in zip(values, unpriced, strict=True) if not up]
    if not priced:
        # every candidate unpriced: all worst, all tie (broken elsewhere)
        return [1.0 for _ in values]
    lo, hi = min(priced), max(priced)
    span = hi - lo
    out: list[float] = []
    for v, up in zip(values, unpriced, strict=True):
        if up:
            out.append(1.0)
        elif span == 0.0:
            out.append(0.0)
        else:
            out.append((v - lo) / span)
    return out


def score_candidates(
    vectors: list[CostVector], profile: WeightProfile
) -> tuple[list[float], dict[str, list[float]]]:
    """Score each cost vector against *profile* over the candidate set.

    Returns ``(scores, per_dimension_normalized)`` where ``scores[i]`` is the
    weighted normalized sum for ``vectors[i]`` (lower = better) and
    ``per_dimension_normalized[dim]`` is the normalized column for that dimension
    (recorded in the trace). Normalization is min–max over this exact set, so a
    candidate's score is only meaningful relative to its co-competitors — which
    is exactly the covers-vs-covers frame (SELECTION §6).
    """
    unpriced = [v.unpriced for v in vectors]
    per_dim: dict[str, list[float]] = {}
    for dim in SCORE_DIMENSIONS:
        raw = [v.dimension(dim, profile.bom_qty) for v in vectors]
        per_dim[dim] = _normalized(raw, unpriced)
    scores = [
        sum(profile.weight(dim) * per_dim[dim][i] for dim in SCORE_DIMENSIONS)
        for i in range(len(vectors))
    ]
    return scores, per_dim
